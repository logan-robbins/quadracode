"""Time-travel debugging utilities with append-only event logging."""

from __future__ import annotations

import argparse
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, MutableMapping, Optional, Sequence


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _coerce_enum_value(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "value"):
        return getattr(value, "value")
    return str(value)


def _safe_json_dump(data: Dict[str, Any]) -> str:
    def _default(value: Any):
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")  # type: ignore[no-any-return]
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    return json.dumps(data, default=_default, separators=(",", ":"))


def _cycle_id_from_state(state: MutableMapping[str, Any]) -> str:
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
    """Append-only recorder for runtime events, supporting deterministic replay."""

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
    global _RECORDER
    if _RECORDER is None:
        _RECORDER = TimeTravelRecorder()
    return _RECORDER


def load_log_entries(log_path: Path | str) -> List[Dict[str, Any]]:
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
    entries = load_log_entries(log_path)
    return [entry for entry in entries if entry.get("cycle_id") == cycle_id]


def diff_cycles(
    log_path: Path | str,
    cycle_a: str,
    cycle_b: str,
) -> Dict[str, Any]:
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
    payload = entry.get("payload") or {}
    event = entry.get("event", "unknown")
    timestamp = entry.get("timestamp", "")
    summary = payload if isinstance(payload, str) else _safe_json_dump(payload)
    return f"[{timestamp}] {event} :: {summary}"


def _cli_replay(args: argparse.Namespace) -> int:
    events = replay_cycle(args.log, args.cycle)
    if not events:
        print(f"No events recorded for {args.cycle}")  # noqa: T201
        return 1
    for entry in events:
        print(_format_event(entry))  # noqa: T201
    return 0


def _cli_diff(args: argparse.Namespace) -> int:
    result = diff_cycles(args.log, args.cycle_a, args.cycle_b)
    if not result.get("delta"):
        print(json.dumps(result, indent=2))  # noqa: T201
        return 1
    print(json.dumps(result, indent=2))  # noqa: T201
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
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
