"""
Time-travel debugging utilities for recording and replaying agent state transitions.

This module provides a `TimeTravelRecorder` class that captures a fine-grained,
append-only log of events occurring during a Quadracode runtime session. The log
is structured as a JSONL file, where each line represents a discrete event, such
as a stage transition, a tool call, or a full state snapshot.

Key features include:
- **Thread-safe logging**: Each execution thread (or agent) can write to its
  own log file without conflicts.
- **Structured events**: Events are logged with consistent metadata, including
  timestamps, cycle IDs, PRP state, and exhaustion modes.
- **Deterministic replay**: The logs can be used to reconstruct the sequence
  of events for a specific refinement cycle, aiding in debugging and analysis.
- **State diffing**: Utilities are provided to compare snapshots from different
  cycles, highlighting changes in key metrics.
- **CLI for inspection**: A command-line interface is included for replaying
  and diffing cycles directly from the log files.

The core singleton `get_time_travel_recorder()` provides global access to the
recorder instance, making it easy to integrate logging throughout the runtime.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, MutableMapping, Optional, Sequence


def _utc_iso() -> str:
    """Returns the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _coerce_enum_value(value: Any) -> str | None:
    """
    Safely extracts the string value from an enum-like object.

    If the object has a `.value` attribute, it is used. Otherwise, the object
    is converted to a string. Returns `None` if the input is `None`.

    Args:
        value: The object to coerce, potentially an Enum member.

    Returns:
        The string representation of the value, or None.
    """
    if value is None:
        return None
    if hasattr(value, "value"):
        return getattr(value, "value")
    return str(value)


def _safe_json_dump(data: Dict[str, Any]) -> str:
    """
    Serializes a dictionary to a compact JSON string, handling complex types.

    This function is designed to serialize runtime state objects, which may contain
    Pydantic models, datetime objects, or other non-standard JSON types. It uses a
    custom default handler to convert these types into JSON-compatible formats.

    Args:
        data: The dictionary to serialize.

    Returns:
        A compact, single-line JSON string.
    """
    def _default(value: Any):
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")  # type: ignore[no-any-return]
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    return json.dumps(data, default=_default, separators=(",", ":"))


def _cycle_id_from_state(state: MutableMapping[str, Any]) -> str:
    """
    Extracts the active PRP cycle ID from the runtime state.

    It first attempts to find the ID from the most recent entry in the
    `refinement_ledger`. If the ledger is unavailable, it calculates a
    provisional ID based on the `prp_cycle_count`.

    Args:
        state: The runtime state dictionary.

    Returns:
        The string identifier for the current or next refinement cycle.
    """
    ledger = state.get("refinement_ledger")
    if isinstance(ledger, list) and ledger:
        tail = ledger[-1]
        if isinstance(tail, dict):
            value = tail.get("cycle_id")
            if value:
                return str(value)
        elif hasattr(tail, "cycle_id"):
            value = getattr(tail, "cycle_id")
            if value:
                return str(value)
    cycle_number = int(state.get("prp_cycle_count", 0) or 0) + 1
    return f"cycle-{cycle_number}"


class TimeTravelRecorder:
    """
    An append-only recorder for runtime events, designed for observability and deterministic replay.

    This class provides a thread-safe mechanism to log structured events to a
    JSONL file. Each agent or thread can have its own log, identified by a
    `thread_id`. The recorder automatically enriches log entries with metadata
    from the current runtime state, such as the PRP cycle ID, state, and exhaustion
    mode. This detailed log is invaluable for debugging complex agent behaviors.

    The log directory can be configured via the `base_dir` argument or the
    `QUADRACODE_TIME_TRAVEL_DIR` environment variable.

    Attributes:
        base_dir: The root directory where log files are stored.
        retention: The maximum number of log entries to keep in the in-memory
                   `time_travel_log` list within the state object.
    """

    def __init__(
        self,
        base_dir: str | Path | None = None,
        *,
        retention: int = 500,
    ) -> None:
        raw_dir = base_dir or os.environ.get("QUADRACODE_TIME_TRAVEL_DIR", "./time_travel_logs")
        self.base_dir = Path(raw_dir).expanduser().resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.retention = retention
        self._locks: Dict[Path, threading.Lock] = {}

    def log_stage(
        self,
        state: MutableMapping[str, Any],
        *,
        stage: str,
        payload: Dict[str, Any] | None = None,
        state_update: Dict[str, Any] | None = None,
    ) -> None:
        """
        Logs an event corresponding to a specific stage in the LangGraph.

        Args:
            state: The current runtime state.
            stage: The name of the stage being executed.
            payload: An optional dictionary of data related to the stage.
            state_update: An optional dictionary representing the change in state
                        produced by this stage.
        """
        self._persist(
            state,
            event=f"stage.{stage}",
            payload=payload or {},
            stage=stage,
            state_update=state_update,
        )

    def log_tool(
        self,
        state: MutableMapping[str, Any],
        *,
        tool_name: str,
        payload: Dict[str, Any] | None = None,
    ) -> None:
        """
        Logs an event for a tool call.

        Args:
            state: The current runtime state.
            tool_name: The name of the tool that was called.
            payload: An optional dictionary containing tool inputs or outputs.
        """
        self._persist(
            state,
            event=f"tool.{tool_name}",
            payload=payload or {},
            tool=tool_name,
        )

    def log_transition(
        self,
        state: MutableMapping[str, Any],
        *,
        event: str,
        payload: Dict[str, Any],
        state_update: Dict[str, Any] | None = None,
    ) -> None:
        """

        Logs a generic state transition or significant event.

        This method is used for logging events that are not tied to a specific
        stage or tool, such as PRP state transitions or invariant violations.

        Args:
            state: The current runtime state.
            event: A descriptive name for the event (e.g., "prp_transition").
            payload: A dictionary containing data about the event.
            state_update: An optional dictionary of the resulting state changes.
        """
        self._persist(
            state,
            event=event,
            payload=payload,
            state_update=state_update,
        )

    def log_snapshot(
        self,
        state: MutableMapping[str, Any],
        *,
        reason: str,
        payload: Dict[str, Any] | None = None,
    ) -> None:
        """
        Logs a full snapshot of key metrics or state at a specific moment.

        This is typically used at the end of a PRP cycle to record a summary
        of the cycle's performance and outcome.

        Args:
            state: The current runtime state.
            reason: The reason for taking the snapshot (e.g., "cycle_end").
            payload: A dictionary containing the snapshot data.
        """
        self._persist(
            state,
            event="cycle_snapshot",
            payload={
                "reason": reason,
                **(payload or {}),
            },
        )

    def _persist(
        self,
        state: MutableMapping[str, Any],
        *,
        event: str,
        payload: Dict[str, Any],
        stage: str | None = None,
        tool: str | None = None,
        state_update: Dict[str, Any] | None = None,
    ) -> None:
        """
        The core persistence logic for writing a log entry.

        This internal method constructs the final log entry dictionary, appends it
        to the in-memory `time_travel_log` within the state, and writes it as a
        JSON line to the appropriate thread-specific log file.

        Args:
            state: The runtime state.
            event: The event name.
            payload: The event's data payload.
            stage: The stage name, if applicable.
            tool: The tool name, if applicable.
            state_update: State changes, if applicable.
        """
        metadata = self._metadata(state)
        entry = {
            **metadata,
            "timestamp": _utc_iso(),
            "event": event,
            "payload": payload,
            "stage": stage,
            "tool": tool,
            "state_update": state_update,
        }
        log = state.setdefault("time_travel_log", [])
        if isinstance(log, list):
            log.append(entry)
            if self.retention and len(log) > self.retention:
                del log[0 : len(log) - self.retention]
        self._write_entry(entry, self._log_path(metadata["thread_id"]))

    def _metadata(self, state: MutableMapping[str, Any]) -> Dict[str, Any]:
        thread_id = str(state.get("thread_id") or "global")
        prp_state = state.get("prp_state")
        exhaustion = state.get("exhaustion_mode")
        metadata = {
            "thread_id": thread_id,
            "cycle_id": _cycle_id_from_state(state),
            "prp_state": _coerce_enum_value(prp_state) or "unknown",
            "exhaustion_mode": _coerce_enum_value(exhaustion) or "none",
            "iteration_count": int(state.get("iteration_count", 0) or 0),
        }
        return metadata

    def _log_path(self, thread_id: str) -> Path:
        sanitized = thread_id.replace("/", "_")
        return self.base_dir / f"{sanitized}.jsonl"

    def _write_entry(self, entry: Dict[str, Any], path: Path) -> None:
        lock = self._locks.setdefault(path, threading.Lock())
        with lock, path.open("a", encoding="utf-8") as handle:
            handle.write(_safe_json_dump(entry))
            handle.write("\n")


_RECORDER: TimeTravelRecorder | None = None


def get_time_travel_recorder() -> TimeTravelRecorder:
    """
    Retrieves the global singleton instance of the `TimeTravelRecorder`.

    This function ensures that only one recorder instance is created and
    accessible throughout the runtime. It is typically used to initialize
    logging at the start of a runtime session.

    Returns:
        The `TimeTravelRecorder` instance.
    """
    global _RECORDER
    if _RECORDER is None:
        _RECORDER = TimeTravelRecorder()
    return _RECORDER


def load_log_entries(log_path: Path | str) -> List[Dict[str, Any]]:
    """
    Loads all entries from a JSONL time-travel log file.

    Args:
        log_path: The path to the `.jsonl` log file.

    Returns:
        A list of dictionaries, where each dictionary is a log entry.
    """
    path = Path(log_path).expanduser()
    entries: List[Dict[str, Any]] = []
    if not path.exists():
        return entries
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def replay_cycle(log_path: Path | str, cycle_id: str) -> List[Dict[str, Any]]:
    """
    Filters a log file to retrieve all events for a specific PRP cycle.

    Args:
        log_path: The path to the `.jsonl` log file.
        cycle_id: The identifier of the cycle to replay.

    Returns:
        A list of log entries corresponding to the specified cycle.
    """
    entries = load_log_entries(log_path)
    return [entry for entry in entries if entry.get("cycle_id") == cycle_id]


def diff_cycles(
    log_path: Path | str,
    cycle_a: str,
    cycle_b: str,
) -> Dict[str, Any]:
    """
    Compares cycle snapshots to identify key differences in metrics.

    This function loads all `cycle_snapshot` events from a log file and computes
    the delta between two specified cycles for metrics like token usage, tool
    calls, and stage counts.

    Args:
        log_path: The path to the `.jsonl` log file.
        cycle_a: The identifier for the baseline cycle.
        cycle_b: The identifier for the comparison cycle.

    Returns:
        A dictionary summarizing the delta between the two cycles.
    """
    entries = load_log_entries(log_path)
    snapshots: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        if entry.get("event") != "cycle_snapshot":
            continue
        cid = entry.get("cycle_id")
        if cid:
            snapshots[cid] = entry

    snap_a = snapshots.get(cycle_a)
    snap_b = snapshots.get(cycle_b)
    if not snap_a or not snap_b:
        return {
            "cycle_a_found": snap_a is not None,
            "cycle_b_found": snap_b is not None,
            "delta": {},
        }

    payload_a = snap_a.get("payload", {})
    payload_b = snap_b.get("payload", {})
    metrics_a = payload_a.get("cycle_metrics", {})
    metrics_b = payload_b.get("cycle_metrics", {})

    delta = {
        "tokens_delta": int(metrics_b.get("total_tokens", 0) or 0) - int(metrics_a.get("total_tokens", 0) or 0),
        "tool_calls_delta": int(metrics_b.get("tool_calls", 0) or 0) - int(metrics_a.get("tool_calls", 0) or 0),
        "stage_count_delta": len(metrics_b.get("stage_usage", []) or []) - len(metrics_a.get("stage_usage", []) or []),
        "status_change": {
            "from": payload_a.get("status"),
            "to": payload_b.get("status"),
        },
    }
    return {
        "cycle_a": cycle_a,
        "cycle_b": cycle_b,
        "delta": delta,
    }


def _format_event(entry: Dict[str, Any]) -> str:
    """Formats a single log entry into a human-readable string for CLI output."""
    payload = entry.get("payload") or {}
    event = entry.get("event", "unknown")
    timestamp = entry.get("timestamp", "")
    summary = payload if isinstance(payload, str) else _safe_json_dump(payload)
    return f"[{timestamp}] {event} :: {summary}"


def _cli_replay(args: argparse.Namespace) -> int:
    """Handler for the 'replay' CLI command."""
    events = replay_cycle(args.log, args.cycle)
    if not events:
        print(f"No events recorded for {args.cycle}")  # noqa: T201
        return 1
    for entry in events:
        print(_format_event(entry))  # noqa: T201
    return 0


def _cli_diff(args: argparse.Namespace) -> int:
    """Handler for the 'diff' CLI command."""
    result = diff_cycles(args.log, args.cycle_a, args.cycle_b)
    if not result.get("delta"):
        print(json.dumps(result, indent=2))  # noqa: T201
        return 1
    print(json.dumps(result, indent=2))  # noqa: T201
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    """
    The main entry point for the time-travel debugging command-line interface.

    Parses command-line arguments and dispatches to the appropriate handler
    function (`replay` or `diff`).

    Args:
        argv: A sequence of command-line arguments (e.g., from `sys.argv`).

    Returns:
        An exit code (0 for success, non-zero for failure).
    """
    parser = argparse.ArgumentParser(description="Quadracode time-travel debugging CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    replay_parser = subparsers.add_parser("replay", help="Replay events for a cycle")
    replay_parser.add_argument("--log", required=True, help="Path to JSONL time-travel log")
    replay_parser.add_argument("--cycle", required=True, help="Cycle identifier to replay")
    replay_parser.set_defaults(func=_cli_replay)

    diff_parser = subparsers.add_parser("diff", help="Compare two cycles")
    diff_parser.add_argument("--log", required=True, help="Path to JSONL time-travel log")
    diff_parser.add_argument("--cycle-a", required=True, help="Baseline cycle identifier")
    diff_parser.add_argument("--cycle-b", required=True, help="Comparison cycle identifier")
    diff_parser.set_defaults(func=_cli_diff)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover - manual invocation
    raise SystemExit(main())
