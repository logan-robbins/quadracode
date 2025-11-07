from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Iterable, Sequence
from copy import deepcopy
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Awaitable, Callable, Dict, List, Optional

from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    messages_from_dict,
    messages_to_dict,
)

from quadracode_contracts import (
    HUMAN_RECIPIENT,
    ORCHESTRATOR_RECIPIENT,
    MessageEnvelope,
    AutonomousRoutingDirective,
)
from quadracode_tools.tools.workspace import ensure_workspace

from .graph import CHECKPOINTER, GRAPH_RECURSION_LIMIT, build_graph
from .messaging import RedisMCPMessaging
from .profiles import RuntimeProfile, is_autonomous_mode_enabled
from .state import ExhaustionMode, RuntimeState
from .registry import AgentRegistryIntegration
from .validation import validate_human_clone_envelope

IDENTITY_ENV_VAR = "QUADRACODE_ID"
AUTONOMOUS_DEFAULT_MAX_ITERATIONS = 1000
AUTONOMOUS_DEFAULT_MAX_HOURS = 48.0
AUTONOMOUS_STREAM_KEY = os.environ.get("QUADRACODE_AUTONOMOUS_STREAM_KEY", "qc:autonomous:events")
AUTONOMOUS_METRICS_REDIS_URL = os.environ.get("QUADRACODE_METRICS_REDIS_URL", "redis://redis:6379/0")
_AUTONOMOUS_METRICS_CLIENT = None
_AUTONOMOUS_METRICS_DISABLED = False
_AUTONOMOUS_METRICS_LOCK = Lock()

_WORKSPACE_ENV_KEYS = (
    "QUADRACODE_ACTIVE_WORKSPACE_DESCRIPTOR",
    "QUADRACODE_ACTIVE_WORKSPACE_ID",
    "QUADRACODE_ACTIVE_WORKSPACE_VOLUME",
    "QUADRACODE_ACTIVE_WORKSPACE_CONTAINER",
    "QUADRACODE_ACTIVE_WORKSPACE_MOUNT",
)

LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _autonomous_max_iterations() -> int | None:
    value = os.environ.get("QUADRACODE_AUTONOMOUS_MAX_ITERATIONS")
    if value is None or value.strip() == "":
        return AUTONOMOUS_DEFAULT_MAX_ITERATIONS
    try:
        parsed = int(value)
        return parsed if parsed > 0 else AUTONOMOUS_DEFAULT_MAX_ITERATIONS
    except ValueError:
        return AUTONOMOUS_DEFAULT_MAX_ITERATIONS


def _autonomous_max_hours() -> float | None:
    value = os.environ.get("QUADRACODE_AUTONOMOUS_MAX_HOURS")
    if value is None or value.strip() == "":
        return AUTONOMOUS_DEFAULT_MAX_HOURS
    try:
        parsed = float(value)
        return parsed if parsed > 0 else AUTONOMOUS_DEFAULT_MAX_HOURS
    except ValueError:
        return AUTONOMOUS_DEFAULT_MAX_HOURS


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _publish_autonomous_event(
    event: str,
    payload: Dict[str, Any],
    *,
    categories: List[str] | None = None,
) -> None:
    global _AUTONOMOUS_METRICS_CLIENT, _AUTONOMOUS_METRICS_DISABLED

    if _AUTONOMOUS_METRICS_DISABLED or not AUTONOMOUS_STREAM_KEY:
        return

    record_payload = dict(payload)
    if categories:
        existing = record_payload.get("categories")
        if isinstance(existing, list):
            record_payload["categories"] = sorted(set(existing + categories))
        else:
            record_payload["categories"] = categories

    try:
        import redis  # type: ignore
    except ImportError:
        _AUTONOMOUS_METRICS_DISABLED = True
        return

    try:
        client = _AUTONOMOUS_METRICS_CLIENT
        if client is None:
            with _AUTONOMOUS_METRICS_LOCK:
                client = _AUTONOMOUS_METRICS_CLIENT
                if client is None and not _AUTONOMOUS_METRICS_DISABLED:
                    client = redis.Redis.from_url(
                        AUTONOMOUS_METRICS_REDIS_URL, decode_responses=True
                    )
                    _AUTONOMOUS_METRICS_CLIENT = client
        if client is None:
            return

        client.xadd(
            AUTONOMOUS_STREAM_KEY,
            {
                "event": event,
                "timestamp": _now_iso(),
                "payload": json.dumps(record_payload),
            },
        )
    except Exception:
        _AUTONOMOUS_METRICS_DISABLED = True


def _set_workspace_environment(descriptor: dict[str, Any] | None) -> None:
    if descriptor:
        os.environ["QUADRACODE_ACTIVE_WORKSPACE_DESCRIPTOR"] = json.dumps(
            descriptor, separators=(",", ":")
        )
        mapping = {
            "QUADRACODE_ACTIVE_WORKSPACE_ID": descriptor.get("workspace_id"),
            "QUADRACODE_ACTIVE_WORKSPACE_VOLUME": descriptor.get("volume"),
            "QUADRACODE_ACTIVE_WORKSPACE_CONTAINER": descriptor.get("container"),
            "QUADRACODE_ACTIVE_WORKSPACE_MOUNT": descriptor.get("mount_path"),
        }
        for key, value in mapping.items():
            if value is not None and str(value).strip():
                os.environ[key] = str(value)
            else:
                os.environ.pop(key, None)
    else:
        for key in _WORKSPACE_ENV_KEYS:
            os.environ.pop(key, None)


def _append_error_history(result: dict[str, object], entry: dict[str, object]) -> None:
    errors = result.get("error_history")
    if isinstance(errors, list):
        errors.append(entry)
        return
    result["error_history"] = [entry]


def _apply_autonomous_limits(
    state: RuntimeState,
    result: dict[str, object],
) -> None:
    if not result.get("autonomous_mode"):
        return

    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.isoformat(timespec="seconds")
    iteration_count = result.get("iteration_count")
    if not isinstance(iteration_count, int):
        try:
            iteration_count = int(iteration_count)  # type: ignore[assignment]
        except Exception:
            iteration_count = 0
        result["iteration_count"] = iteration_count

    settings = {}
    settings_payload = result.get("autonomous_settings")
    if isinstance(settings_payload, dict):
        settings = settings_payload
    elif isinstance(state.get("autonomous_settings"), dict):
        settings = state["autonomous_settings"]  # type: ignore[assignment]

    max_iterations_override = settings.get("max_iterations") if isinstance(settings, dict) else None
    if isinstance(max_iterations_override, str):
        try:
            max_iterations_override = int(max_iterations_override)
        except Exception:
            max_iterations_override = None
    max_iterations = _autonomous_max_iterations()
    if isinstance(max_iterations_override, int) and max_iterations_override > 0:
        max_iterations = max_iterations_override
    thread_id = result.get("thread_id") or state.get("thread_id")
    if (
        isinstance(iteration_count, int)
        and max_iterations is not None
        and iteration_count >= max_iterations
        and not result.get("iteration_limit_triggered")
    ):
        directive = AutonomousRoutingDirective(
            deliver_to_human=True,
            escalate=True,
            reason=f"Iteration limit reached ({iteration_count}/{max_iterations}).",
            recovery_attempts=["hit_iteration_guard"],
        )
        result["autonomous_routing"] = directive.to_payload()
        result["iteration_limit_triggered"] = True
        _append_error_history(
            result,
            {
                "error_type": "iteration_limit",
                "description": f"Reached iteration limit {iteration_count}/{max_iterations}",
                "recovery_attempts": ["hit_iteration_guard"],
                "escalated": True,
                "resolved": False,
                "timestamp": now_iso,
            },
        )
        result["current_phase"] = "awaiting_human"
        _publish_autonomous_event(
            "guardrail_trigger",
            {
                "type": "iteration_limit",
                "iteration_count": iteration_count,
                "limit": max_iterations,
                "thread_id": thread_id,
            },
            categories=["guardrail", "iteration_limit"],
        )
        return

    max_hours_override = settings.get("max_hours") if isinstance(settings, dict) else None
    if isinstance(max_hours_override, str):
        try:
            max_hours_override = float(max_hours_override)
        except Exception:
            max_hours_override = None
    max_hours = _autonomous_max_hours()
    if isinstance(max_hours_override, (int, float)) and max_hours_override > 0:
        max_hours = float(max_hours_override)
    if max_hours is None:
        return

    started_at = result.get("autonomous_started_at") or state.get("autonomous_started_at")
    started_dt = _parse_timestamp(started_at if isinstance(started_at, str) else None)
    if not started_dt:
        return

    elapsed_hours = (now_dt - started_dt).total_seconds() / 3600
    if elapsed_hours >= max_hours and not result.get("runtime_limit_triggered"):
        result["runtime_limit_triggered"] = True
        escalated = False
        if not result.get("autonomous_routing"):
            directive = AutonomousRoutingDirective(
                deliver_to_human=True,
                escalate=True,
                reason=f"Runtime limit reached ({elapsed_hours:.2f}h/{max_hours}h).",
                recovery_attempts=["hit_runtime_guard"],
            )
            result["autonomous_routing"] = directive.to_payload()
            result["current_phase"] = "awaiting_human"
            escalated = True

        _append_error_history(
            result,
            {
                "error_type": "runtime_limit",
                "description": f"Exceeded runtime budget ({elapsed_hours:.2f}h/{max_hours}h)",
                "recovery_attempts": ["hit_runtime_guard"],
                "escalated": escalated,
                "resolved": False,
                "timestamp": now_iso,
            },
        )
        _publish_autonomous_event(
            "guardrail_trigger",
            {
                "type": "runtime_limit",
                "elapsed_hours": elapsed_hours,
                "limit_hours": max_hours,
                "thread_id": thread_id,
                "escalated": escalated,
            },
            categories=["guardrail", "runtime_limit"],
        )


class RuntimeRunner:
    def __init__(
        self,
        profile: RuntimeProfile,
        *,
        poll_interval: float = 1.0,
        batch_size: int = 5,
    ) -> None:
        self._profile = profile
        self._poll_interval = poll_interval
        self._batch_size = batch_size
        self._identity = os.environ.get(IDENTITY_ENV_VAR, profile.default_identity)
        self._graph = build_graph(profile.system_prompt)
        self._messaging: RedisMCPMessaging | None = None
        self._registry = AgentRegistryIntegration.from_environment(
            profile.name, self._identity
        )
        if profile.name == "agent":
            if self._registry:
                LOGGER.info(
                    "Agent registry auto-registration enabled for identity=%s",
                    self._identity,
                )
            else:
                LOGGER.warning(
                    "Agent registry auto-registration disabled for identity=%s",
                    self._identity,
                )
        print(
            f"[RuntimeRunner] profile={profile.name} identity={self._identity} "
            f"registry={'enabled' if self._registry else 'disabled'}"
        )

    async def start(self) -> None:
        messaging = await RedisMCPMessaging.create()
        self._messaging = messaging
        try:
            try:
                if self._registry:
                    await self._registry.start()
            except Exception as exc:  # noqa: BLE001
                print(f"Failed to initialize agent registry integration: {exc}")
                self._registry = None
            while True:
                entries = await messaging.read(
                    self._identity, batch_size=self._batch_size
                )
                if not entries:
                    await asyncio.sleep(self._poll_interval)
                    continue
                for entry_id, envelope in entries:
                    await self._handle_entry(messaging, entry_id, envelope)
        finally:
            if self._registry:
                await self._registry.shutdown()

    async def _handle_entry(
        self,
        messaging: RedisMCPMessaging,
        entry_id: str,
        envelope: MessageEnvelope,
    ) -> None:
        valid, feedback = validate_human_clone_envelope(envelope)
        if not valid:
            if feedback:
                await messaging.publish(feedback.recipient, feedback)
            await messaging.delete(self._identity, entry_id)
            return

        try:
            outgoing = await self._process_envelope(envelope)
        except Exception as exc:  # noqa: BLE001
            print(f"Runtime error for message {entry_id}: {exc}")
            await messaging.delete(self._identity, entry_id)
            return

        if outgoing:
            for response in outgoing:
                await messaging.publish(response.recipient, response)

        await messaging.delete(self._identity, entry_id)

    async def shutdown(self) -> None:
        if self._registry:
            await self._registry.shutdown()

    async def _process_envelope(
        self, envelope: MessageEnvelope
    ) -> Sequence[MessageEnvelope]:
        payload = deepcopy(envelope.payload)
        raw_thread_id = (
            payload.get("chat_id")
            or payload.get("thread_id")
            or payload.get("session_id")
            or payload.get("ticket_id")
            or envelope.sender
            or self._identity
        )
        if raw_thread_id is None or str(raw_thread_id).strip() == "":
            raw_thread_id = self._identity
        thread_id = str(raw_thread_id)
        configurable: dict[str, object] = {"thread_id": thread_id}
        is_orchestrator = self._profile.default_identity == ORCHESTRATOR_RECIPIENT
        autonomous_active = is_orchestrator and is_autonomous_mode_enabled()
        if autonomous_active:
            configurable["autonomous_mode"] = True

        config = {"configurable": configurable, "recursion_limit": GRAPH_RECURSION_LIMIT}

        has_checkpoint = CHECKPOINTER.get_tuple(config) is not None
        messages = _extract_messages(
            payload,
            envelope,
            include_history=not has_checkpoint,
        )
        state: RuntimeState = {"messages": messages, "thread_id": thread_id}
        state["_last_envelope_sender"] = envelope.sender

        state_payload = payload.get("state")
        if isinstance(state_payload, dict):
            for key in (
                "autonomous_mode",
                "task_goal",
                "current_phase",
                "iteration_count",
                "milestones",
                "error_history",
                "autonomous_started_at",
                "last_iteration_at",
                "iteration_limit_triggered",
                "runtime_limit_triggered",
                "autonomous_settings",
                "workspace",
            ):
                value = state_payload.get(key)
                if value is None:
                    continue
                if key in {"milestones", "error_history"} and not isinstance(value, list):
                    continue
                if key == "workspace":
                    if isinstance(value, dict):
                        state["workspace"] = deepcopy(value)
                    continue
                state[key] = value  # type: ignore[assignment]

        workspace_payload = payload.get("workspace")
        if isinstance(workspace_payload, dict):
            state["workspace"] = deepcopy(workspace_payload)

        settings_payload = payload.get("autonomous_settings")
        if isinstance(settings_payload, dict):
            state["autonomous_settings"] = settings_payload

        control_payload = payload.get("autonomous_control")
        if isinstance(control_payload, dict) and control_payload.get("action") == "emergency_stop":
            return self._handle_emergency_stop(envelope, payload, state, thread_id)

        if is_orchestrator:
            self._ensure_workspace_for_thread(thread_id, state, payload)

        workspace_descriptor = state.get("workspace")
        if isinstance(workspace_descriptor, dict):
            _set_workspace_environment(workspace_descriptor)
        else:
            _set_workspace_environment(None)

        if autonomous_active:
            state["autonomous_mode"] = True
            state.setdefault("iteration_count", 0)
            state.setdefault("milestones", [])
            state.setdefault("error_history", [])
            state.setdefault("current_phase", None)
            state.setdefault("iteration_limit_triggered", False)
            state.setdefault("runtime_limit_triggered", False)
            if not state.get("autonomous_started_at"):
                state["autonomous_started_at"] = _now_iso()
            if "task_goal" not in state or not state.get("task_goal"):
                goal = payload.get("task_goal")
                if isinstance(goal, str) and goal.strip():
                    state["task_goal"] = goal
                elif (
                    envelope.sender == HUMAN_RECIPIENT
                    and isinstance(envelope.message, str)
                    and envelope.message.strip()
                ):
                    state["task_goal"] = envelope.message
        elif "autonomous_mode" not in state:
            state["autonomous_mode"] = False

        result = await asyncio.to_thread(self._graph.invoke, state, config)
        result.pop("_last_envelope_sender", None)
        output_messages = result.get("messages", [])
        output_serialized = messages_to_dict(output_messages)
        if "workspace" not in result and isinstance(state.get("workspace"), dict):
            result["workspace"] = deepcopy(state["workspace"])  # type: ignore[index]

        if autonomous_active:
            prior_raw = state.get("iteration_count", 0)
            try:
                prior_iterations = int(prior_raw)  # type: ignore[arg-type]
            except Exception:
                prior_iterations = 0
            iteration_count = result.get("iteration_count")
            if isinstance(iteration_count, int):
                new_iteration = iteration_count
            else:
                try:
                    new_iteration = int(iteration_count)  # type: ignore[arg-type]
                except Exception:
                    new_iteration = prior_iterations + 1
                else:
                    if new_iteration <= prior_iterations:
                        new_iteration = prior_iterations + 1
            result["iteration_count"] = new_iteration
            result["autonomous_mode"] = True
            if "milestones" not in result and state.get("milestones") is not None:
                result["milestones"] = deepcopy(state.get("milestones", []))
            if "error_history" not in result and state.get("error_history") is not None:
                result["error_history"] = deepcopy(state.get("error_history", []))
            if "task_goal" not in result and state.get("task_goal") is not None:
                result["task_goal"] = state.get("task_goal")
            if "current_phase" not in result and state.get("current_phase") is not None:
                result["current_phase"] = state.get("current_phase")
            if not result.get("autonomous_started_at"):
                result["autonomous_started_at"] = state.get("autonomous_started_at") or _now_iso()
            iteration_timestamp = _now_iso()
            result["last_iteration_at"] = iteration_timestamp
            if "iteration_limit_triggered" not in result:
                result["iteration_limit_triggered"] = state.get("iteration_limit_triggered", False)
            if "runtime_limit_triggered" not in result:
                result["runtime_limit_triggered"] = state.get("runtime_limit_triggered", False)
            if "autonomous_settings" not in result and state.get("autonomous_settings") is not None:
                result["autonomous_settings"] = deepcopy(state.get("autonomous_settings", {}))
            result["thread_id"] = thread_id
            _apply_autonomous_limits(state, result)
        else:
            result.pop("autonomous_routing", None)

        response_payload = {
            key: value
            for key, value in payload.items()
            if key not in {"reply_to", "messages", "state"}
        }
        autonomous_snapshot: dict[str, object] = {}
        for key in (
            "autonomous_mode",
            "task_goal",
            "current_phase",
            "iteration_count",
            "milestones",
            "error_history",
            "autonomous_started_at",
            "last_iteration_at",
            "iteration_limit_triggered",
            "runtime_limit_triggered",
            "autonomous_settings",
            "thread_id",
            "workspace",
            "exhaustion_mode",
            "exhaustion_probability",
            "exhaustion_recovery_log",
        ):
            value = result.get(key)
            if value is None:
                continue
            if key == "workspace":
                if isinstance(value, dict):
                    autonomous_snapshot[key] = deepcopy(value)
                continue
            if key == "exhaustion_mode":
                if isinstance(value, ExhaustionMode):
                    autonomous_snapshot[key] = value.value
                elif isinstance(value, str):
                    autonomous_snapshot[key] = value
                else:
                    autonomous_snapshot[key] = ExhaustionMode.NONE.value
                continue
            if key == "exhaustion_probability":
                try:
                    autonomous_snapshot[key] = float(value)
                except (TypeError, ValueError):
                    autonomous_snapshot[key] = 0.0
                continue
            if key == "exhaustion_recovery_log":
                if isinstance(value, list):
                    autonomous_snapshot[key] = deepcopy(value[-20:])
                continue
            autonomous_snapshot[key] = deepcopy(value)
        if autonomous_snapshot:
            response_payload["state"] = autonomous_snapshot

        routing_payload = result.pop("autonomous_routing", None)
        if routing_payload:
            response_payload["autonomous"] = deepcopy(routing_payload)
        response_payload["messages"] = output_serialized
        workspace_descriptor = result.get("workspace")
        if isinstance(workspace_descriptor, dict):
            response_payload["workspace"] = deepcopy(workspace_descriptor)
        else:
            response_payload.pop("workspace", None)
        response_payload.setdefault("chat_id", thread_id)
        response_payload["thread_id"] = thread_id
        exhaustion_mode_value = result.get("exhaustion_mode")
        if isinstance(exhaustion_mode_value, ExhaustionMode):
            response_payload["exhaustion_mode"] = exhaustion_mode_value.value
        elif isinstance(exhaustion_mode_value, str):
            response_payload["exhaustion_mode"] = exhaustion_mode_value
        else:
            response_payload["exhaustion_mode"] = ExhaustionMode.NONE.value
        try:
            response_payload["exhaustion_probability"] = float(
                result.get("exhaustion_probability", 0.0)
            )
        except (TypeError, ValueError):
            response_payload["exhaustion_probability"] = 0.0
        recovery_log = result.get("exhaustion_recovery_log")
        if isinstance(recovery_log, list):
            response_payload["exhaustion_recovery_log"] = deepcopy(recovery_log[-20:])

        response_body = _last_message_content(output_messages)
        routing_context = deepcopy(payload)
        if routing_payload:
            routing_context["autonomous"] = deepcopy(routing_payload)
        recipients = self._profile.resolve_recipients(envelope, routing_context)

        responses = [
            MessageEnvelope(
                sender=self._identity,
                recipient=recipient,
                message=response_body,
                payload=response_payload,
            )
            for recipient in recipients
        ]
        return responses

    def _ensure_workspace_for_thread(
        self,
        thread_id: str,
        state: RuntimeState,
        payload: dict,
    ) -> None:
        existing = state.get("workspace")
        overrides = payload.get("workspace_config")
        image: Optional[str] = None
        network: Optional[str] = None
        if isinstance(overrides, dict):
            raw_image = overrides.get("image")
            raw_network = overrides.get("network")
            if isinstance(raw_image, str) and raw_image.strip():
                image = raw_image.strip()
            if isinstance(raw_network, str) and raw_network.strip():
                network = raw_network.strip()
        if isinstance(existing, dict):
            existing_image = existing.get("image")
            existing_network = existing.get("network")  # allow override if captured
            if image is None and isinstance(existing_image, str) and existing_image.strip():
                image = existing_image.strip()
            if network is None and isinstance(existing_network, str) and existing_network.strip():
                network = existing_network.strip()

        success, descriptor_model, error = ensure_workspace(thread_id, image=image, network=network)
        if not success or descriptor_model is None:
            if error:
                print(f"[workspace] unable to provision workspace for {thread_id}: {error}")
            return

        descriptor_dict = descriptor_model.dict()
        if network:
            descriptor_dict.setdefault("network", network)
        if image:
            descriptor_dict.setdefault("image", image)
        state["workspace"] = descriptor_dict
        payload["workspace"] = deepcopy(descriptor_dict)

    def _handle_emergency_stop(
        self,
        envelope: MessageEnvelope,
        payload: dict,
        state: RuntimeState,
        thread_id: str,
    ) -> Sequence[MessageEnvelope]:
        state.setdefault("error_history", [])
        state.setdefault("autonomous_mode", True)
        directive = AutonomousRoutingDirective(
            deliver_to_human=True,
            escalate=True,
            reason="Emergency stop requested by human.",
            recovery_attempts=["human_override"],
        )

        _append_error_history(
            state,
            {
                "error_type": "emergency_stop",
                "description": "Human requested emergency stop",
                "recovery_attempts": ["human_override"],
                "escalated": True,
                "resolved": False,
                "timestamp": _now_iso(),
            },
        )
        _publish_autonomous_event(
            "control_event",
            {
                "type": "emergency_stop",
                "thread_id": thread_id,
                "initiator": "human",
            },
            categories=["control", "emergency_stop"],
        )

        snapshot = {
            "autonomous_mode": True,
            "task_goal": state.get("task_goal"),
            "current_phase": "halted_by_human",
            "iteration_count": state.get("iteration_count", 0),
            "milestones": state.get("milestones", []),
            "error_history": state.get("error_history", []),
            "autonomous_started_at": state.get("autonomous_started_at") or _now_iso(),
            "last_iteration_at": _now_iso(),
            "iteration_limit_triggered": state.get("iteration_limit_triggered", False),
            "runtime_limit_triggered": True,
            "autonomous_settings": state.get("autonomous_settings", {}),
            "thread_id": thread_id,
        }
        workspace_state = state.get("workspace")
        if isinstance(workspace_state, dict):
            snapshot["workspace"] = deepcopy(workspace_state)

        response_payload = {
            key: value
            for key, value in payload.items()
            if key not in {"reply_to", "messages", "state"}
        }
        response_payload["messages"] = []
        response_payload.setdefault("chat_id", thread_id)
        response_payload["thread_id"] = thread_id
        response_payload["state"] = deepcopy(snapshot)
        if isinstance(workspace_state, dict):
            response_payload["workspace"] = deepcopy(workspace_state)
        response_payload["autonomous"] = directive.to_payload()

        response_body = "Emergency stop acknowledged. Autonomous run halted."

        routing_context = deepcopy(payload)
        routing_context["autonomous"] = directive.to_payload()
        recipients = self._profile.resolve_recipients(envelope, routing_context)

        return [
            MessageEnvelope(
                sender=self._identity,
                recipient=recipient,
                message=response_body,
                payload=response_payload,
            )
            for recipient in recipients
        ]


def _extract_messages(
    payload: dict,
    envelope: MessageEnvelope,
    *,
    include_history: bool,
) -> Sequence[BaseMessage]:
    if include_history:
        state_payload = payload.get("state")
        if isinstance(state_payload, dict):
            messages_raw = state_payload.get("messages")
            if isinstance(messages_raw, list):
                try:
                    return messages_from_dict(messages_raw)
                except Exception:  # noqa: BLE001
                    pass

        messages_raw = payload.get("messages")
        if isinstance(messages_raw, list):
            try:
                return messages_from_dict(messages_raw)
            except Exception:  # noqa: BLE001
                pass

    if envelope.message:
        return [HumanMessage(content=envelope.message)]

    if not include_history:
        messages_raw = payload.get("messages")
        if isinstance(messages_raw, list) and messages_raw:
            try:
                last_message = messages_from_dict([messages_raw[-1]])
                if last_message:
                    return last_message
            except Exception:  # noqa: BLE001
                pass

    return []


def _last_message_content(messages: Sequence[BaseMessage]) -> str:
    if not messages:
        return ""
    content = messages[-1].content
    if isinstance(content, str):
        return content
    return str(content)


def create_runtime(
    profile: RuntimeProfile, *, poll_interval: float = 1.0, batch_size: int = 5
) -> RuntimeRunner:
    return RuntimeRunner(profile, poll_interval=poll_interval, batch_size=batch_size)


async def run_forever(
    profile: RuntimeProfile, *, poll_interval: float = 1.0, batch_size: int = 5
) -> None:
    runtime = create_runtime(profile, poll_interval=poll_interval, batch_size=batch_size)
    await runtime.start()
