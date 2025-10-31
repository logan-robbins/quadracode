from __future__ import annotations

import asyncio
import os
from collections.abc import Iterable, Sequence
from copy import deepcopy
from typing import Awaitable, Callable

from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    messages_from_dict,
    messages_to_dict,
)

from quadracode_contracts import HUMAN_RECIPIENT, MessageEnvelope

from .graph import CHECKPOINTER, build_graph
from .messaging import RedisMCPMessaging
from .profiles import RuntimeProfile
from .state import RuntimeState
from .registry import AgentRegistryIntegration

IDENTITY_ENV_VAR = "QUADRACODE_ID"


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
        config = {"configurable": {"thread_id": thread_id}}

        has_checkpoint = CHECKPOINTER.get_tuple(config) is not None
        messages = _extract_messages(
            payload,
            envelope,
            include_history=not has_checkpoint,
        )
        state: RuntimeState = {"messages": messages}

        result = await asyncio.to_thread(self._graph.invoke, state, config)
        output_messages = result.get("messages", [])
        output_serialized = messages_to_dict(output_messages)

        response_payload = {
            key: value
            for key, value in payload.items()
            if key not in {"reply_to", "messages", "state"}
        }
        response_payload["messages"] = output_serialized
        response_payload.setdefault("chat_id", thread_id)
        response_payload["thread_id"] = thread_id

        response_body = _last_message_content(output_messages)
        recipients = self._profile.resolve_recipients(envelope, payload)

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
