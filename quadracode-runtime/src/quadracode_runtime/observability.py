"""
This module provides the `MetaCognitiveObserver`, a component responsible for 
publishing real-time observability data about the meta-cognitive signals within 
the Quadracode runtime.

The `MetaCognitiveObserver` is a key part of the runtime's telemetry system. It 
provides a centralized interface for publishing events related to the autonomous 
loop, the refinement ledger, and the PRP state machine. These events are 
published to a set of dedicated Redis streams, allowing for real-time monitoring 
and analysis of the system's high-level reasoning processes. This observability 
is crucial for debugging, performance tuning, and understanding the behavior of 
the autonomous system.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable, Dict, MutableMapping, Optional

LOGGER = logging.getLogger(__name__)

try:  # pragma: no cover - optional dependency
    import redis  # type: ignore
except Exception:  # pragma: no cover - redis not installed in some environments
    redis = None  # type: ignore[assignment]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_ts() -> str:
    return _utc_now().isoformat(timespec="seconds")


def _json_dumps(payload: Dict[str, Any]) -> str:
    def _default(value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")  # type: ignore[no-any-return]
        if isinstance(value, (datetime,)):
            return value.isoformat()
        return str(value)

    return json.dumps(payload, default=_default, separators=(",", ":"))


def _as_dict(entry: Any) -> Dict[str, Any]:
    if entry is None:
        return {}
    if isinstance(entry, dict):
        return dict(entry)
    if hasattr(entry, "model_dump"):
        return entry.model_dump(mode="json")  # type: ignore[no-any-return]
    result: Dict[str, Any] = {}
    for attr in ("cycle_id", "hypothesis", "status", "outcome_summary", "strategy"):
        if hasattr(entry, attr):
            result[attr] = getattr(entry, attr)
    return result


def _coerce_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    try:
        return str(value)
    except Exception:  # pragma: no cover - defensive
        return None


class MetaCognitiveObserver:
    """
    Publishes real-time observability data for meta-cognitive signals to a set 
    of dedicated Redis streams.

    This class provides a set of methods for publishing structured events related 
    to the autonomous loop, the refinement ledger, and other high-level 
    reasoning processes. It is designed to be a singleton, with a single 
    instance being shared across the entire runtime.

    Attributes:
        redis_url: The URL of the Redis server.
        ... and the names of the various Redis streams.
    """

    def __init__(
        self,
        redis_url: str,
        *,
        autonomous_stream: str,
        cycle_stream: str,
        exhaustion_stream: str,
        ledger_stream: str,
        test_stream: str,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        """
        Initializes the `MetaCognitiveObserver`.

        Args:
            redis_url: The URL of the Redis server.
            ... and the names of the various Redis streams.
        """
        self.redis_url = redis_url
        self.autonomous_stream = autonomous_stream
        self.cycle_stream = cycle_stream
        self.exhaustion_stream = exhaustion_stream
        self.ledger_stream = ledger_stream
        self.test_stream = test_stream
        self._client_factory = client_factory
        self._client: Any | None = None

    @classmethod
    def from_environment(cls) -> "MetaCognitiveObserver":
        """
        Creates a `MetaCognitiveObserver` instance from environment variables.
        """
        redis_url = os.environ.get("QUADRACODE_METRICS_REDIS_URL", "redis://redis:6379/0")
        autonomous_stream = os.environ.get("QUADRACODE_AUTONOMOUS_STREAM_KEY", "qc:autonomous:events")
        cycle_stream = os.environ.get("QUADRACODE_META_CYCLE_STREAM", "qc:meta:cycles")
        exhaustion_stream = os.environ.get("QUADRACODE_META_EXHAUSTION_STREAM", "qc:meta:exhaustion")
        ledger_stream = os.environ.get("QUADRACODE_META_LEDGER_STREAM", "qc:meta:ledger")
        test_stream = os.environ.get("QUADRACODE_META_TEST_STREAM", "qc:meta:tests")
        return cls(
            redis_url,
            autonomous_stream=autonomous_stream,
            cycle_stream=cycle_stream,
            exhaustion_stream=exhaustion_stream,
            ledger_stream=ledger_stream,
            test_stream=test_stream,
        )

    def publish_autonomous_event(self, event: str, payload: Dict[str, Any]) -> None:
        """Publishes an event to the autonomous events stream."""
        self._push(self.autonomous_stream, event, payload)

    def publish_ledger_event(self, event: str, payload: Dict[str, Any]) -> None:
        """Publishes an event to the refinement ledger stream."""
        self._push(self.ledger_stream, event, payload)

    def publish_test_result(self, test_type: str, payload: Dict[str, Any]) -> None:
        """Publishes a test result to the test stream."""
        event = f"test_{test_type}"
        self._push(self.test_stream, event, payload)

    def publish_exhaustion_event(
        self,
        state: MutableMapping[str, Any],
        *,
        stage: str,
        previous_mode: Any,
        mode: Any,
        probability: float,
    ) -> None:
        """Publishes an exhaustion event to the exhaustion stream."""
        payload = {
            "stage": stage,
            "previous_mode": getattr(previous_mode, "value", previous_mode),
            "mode": getattr(mode, "value", mode),
            "probability": float(probability),
            "loop_depth": int(state.get("prp_cycle_count", 0) or 0),
            "cycle_id": self._resolve_cycle_id(state),
            "timestamp": _iso_ts(),
        }
        self._push(self.exhaustion_stream, "exhaustion_update", payload)

    def publish_cycle_snapshot(
        self,
        state: MutableMapping[str, Any],
        *,
        source: str | None = None,
    ) -> None:
        """Publishes a snapshot of the current PRP cycle to the cycle stream."""
        cycle_id = self._resolve_cycle_id(state)
        entry = self._latest_ledger_entry(state)
        entry_payload = _as_dict(entry)
        metrics_record = self._active_cycle_metrics(state, cycle_id)
        payload = {
            "cycle_id": cycle_id,
            "loop_depth": int(state.get("prp_cycle_count", 0) or 0),
            "ledger_size": len(state.get("refinement_ledger", [])),
            "prp_state": self._coerce_enum(state.get("prp_state")),
            "exhaustion_mode": self._coerce_enum(state.get("exhaustion_mode")),
            "status": entry_payload.get("status"),
            "hypothesis": entry_payload.get("hypothesis"),
            "updated_at": _iso_ts(),
            "source": source or "runtime",
            "cycle_metrics": metrics_record or {},
        }
        if entry_payload.get("outcome_summary"):
            payload["outcome_summary"] = entry_payload["outcome_summary"]
        self._push(self.cycle_stream, "cycle_snapshot", payload)

    def track_stage_tokens(
        self,
        state: MutableMapping[str, Any],
        *,
        stage: str,
        tokens_override: int | None = None,
    ) -> None:
        """
        Tracks the token usage for a given stage of the PRP cycle and publishes 
        an updated cycle snapshot.
        """
        if not state:
            return
        cycle_id = self._resolve_cycle_id(state)
        if not cycle_id:
            return
        tokens = tokens_override
        if tokens is None:
            context_used = int(state.get("context_window_used", 0) or 0)
            baseline_key = "_metacog_token_baseline"
            previous = int(state.get(baseline_key, 0) or 0)
            delta = context_used - previous
            if delta <= 0:
                delta = max(50, context_used // 10 or 50)
            tokens = max(delta, 0)
            state[baseline_key] = context_used
        record = self._ensure_cycle_metrics(state, cycle_id)
        stage_tokens = int(max(tokens or 0, 0))
        record["total_tokens"] = int(record.get("total_tokens", 0) + stage_tokens)
        usage_history = record.setdefault("stage_usage", [])
        usage_history.append(
            {
                "stage": stage,
                "tokens": stage_tokens,
                "timestamp": _iso_ts(),
            }
        )
        if len(usage_history) > 40:
            del usage_history[0]
        if stage == "handle_tool_response":
            record["tool_calls"] = int(record.get("tool_calls", 0) + 1)
        record["last_stage"] = stage
        record["updated_at"] = _iso_ts()
        self.publish_cycle_snapshot(state, source=f"tokens::{stage}")

    def finalize_cycle_metrics(
        self,
        state: MutableMapping[str, Any],
        cycle_id: str,
        *,
        status: str,
        summary: str | None = None,
    ) -> None:
        """
        Finalizes the metrics for a given cycle and publishes an updated cycle 
        snapshot.
        """
        record = self._ensure_cycle_metrics(state, cycle_id)
        record["status"] = status
        record["outcome_summary"] = summary
        record["updated_at"] = _iso_ts()
        self.publish_cycle_snapshot(state, source="ledger::conclude")

    def record_test_value(
        self,
        state: MutableMapping[str, Any],
        *,
        cycle_id: str,
        status: str | None,
        payload: Dict[str, Any],
        test_type: str,
    ) -> None:
        """
        Records the result of a test and publishes an updated cycle snapshot.
        """
        record = self._ensure_cycle_metrics(state, cycle_id)
        normalized_status = (status or "").lower()
        key = f"last_{test_type}_status"
        record[key] = normalized_status or "unknown"
        if payload:
            record[f"{test_type}_evidence"] = payload
        record["updated_at"] = _iso_ts()
        self.publish_cycle_snapshot(state, source=f"test::{test_type}")

    def _active_cycle_metrics(
        self,
        state: MutableMapping[str, Any],
        cycle_id: str,
    ) -> Dict[str, Any]:
        """Retrieves the active metrics for a given cycle."""
        metrics_map = state.get("hypothesis_cycle_metrics")
        if isinstance(metrics_map, dict):
            entry = metrics_map.get(cycle_id)
            if isinstance(entry, dict):
                return dict(entry)
        return {}

    def _ensure_cycle_metrics(
        self,
        state: MutableMapping[str, Any],
        cycle_id: str,
    ) -> Dict[str, Any]:
        """
        Ensures that a metrics record exists for the given cycle, creating one 
        if necessary.
        """
        metrics_map = state.setdefault("hypothesis_cycle_metrics", {})
        if not isinstance(metrics_map, dict):
            metrics_map = {}
            state["hypothesis_cycle_metrics"] = metrics_map
        record = metrics_map.get(cycle_id)
        if not isinstance(record, dict):
            record = {
                "cycle_id": cycle_id,
                "total_tokens": 0,
                "tool_calls": 0,
                "stage_usage": [],
                "updated_at": _iso_ts(),
            }
            metrics_map[cycle_id] = record
        return record

    def _latest_ledger_entry(self, state: MutableMapping[str, Any]) -> Any:
        """Retrieves the latest entry from the refinement ledger."""
        ledger = state.get("refinement_ledger")
        if not isinstance(ledger, list) or not ledger:
            return None
        return ledger[-1]

    def _resolve_cycle_id(self, state: MutableMapping[str, Any]) -> str:
        """Resolves the current cycle ID from the state."""
        ledger_entry = self._latest_ledger_entry(state)
        if ledger_entry is not None:
            value = getattr(ledger_entry, "cycle_id", None)
            if value:
                return str(value)
            if isinstance(ledger_entry, dict):
                ref = ledger_entry.get("cycle_id")
                if ref:
                    return str(ref)
        return f"cycle-{int(state.get('prp_cycle_count', 0) or 0) + 1}"

    def _coerce_enum(self, value: Any) -> str | None:
        """Safely coerces an enum member to its string value."""
        if value is None:
            return None
        if hasattr(value, "value"):
            return getattr(value, "value")
        return _coerce_str(value)

    def _push(self, stream_key: str, event: str, payload: Dict[str, Any]) -> None:
        """Pushes an event to the specified Redis stream."""
        if not stream_key or not event:
            return
        client = self._ensure_client()
        if client is None:
            return
        record = {
            "event": event,
            "timestamp": _iso_ts(),
            "payload": _json_dumps(payload),
        }
        try:
            client.xadd(stream_key, record)
        except Exception as exc:  # pragma: no cover - best-effort
            LOGGER.debug("Failed to publish observability event %s: %s", event, exc)

    def _ensure_client(self):
        """
        Manages the Redis client connection, ensuring it is initialized.
        
        When QUADRACODE_MOCK_MODE is enabled, uses fakeredis instead of
        connecting to a real Redis server.
        """
        if self._client is not None:
            return self._client
        if self._client_factory:
            self._client = self._client_factory()
            return self._client
        
        # Check for mock mode
        from .mock import is_mock_mode_enabled, get_mock_redis_client
        if is_mock_mode_enabled():
            self._client = get_mock_redis_client()
            if self._client is not None:
                LOGGER.info("Using mock Redis client for observability (mock mode enabled)")
            return self._client
        
        if redis is None:  # pragma: no cover - dependency missing
            LOGGER.info("redis not available; disabling observability stream publishing")
            return None
        try:
            self._client = redis.Redis.from_url(self.redis_url, decode_responses=True)  # type: ignore[call-arg]
        except Exception as exc:  # pragma: no cover - connection issues
            LOGGER.info("Unable to initialize Redis client for observability: %s", exc)
            self._client = None
        return self._client


_OBSERVER: MetaCognitiveObserver | None = None


def get_meta_observer() -> MetaCognitiveObserver:
    """
    Returns a singleton instance of the `MetaCognitiveObserver`.
    """
    global _OBSERVER
    if _OBSERVER is None:
        _OBSERVER = MetaCognitiveObserver.from_environment()
    return _OBSERVER
