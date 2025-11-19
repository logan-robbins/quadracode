from __future__ import annotations

import asyncio
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .state import QuadraCodeState


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _preview(text: str, limit: int = 400) -> str:
    normalized = (text or "").strip()
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit]}…"


def _resolve_thread_id(state: Mapping[str, Any] | None) -> str:
    if isinstance(state, Mapping):
        value = state.get("thread_id")
        if value:
            return str(value)
    return "global"


def _resolve_cycle_id(state: Mapping[str, Any] | None) -> str:
    if not isinstance(state, Mapping):
        return "cycle-0"
    ledger = state.get("refinement_ledger")
    if isinstance(ledger, list) and ledger:
        tail = ledger[-1]
        if isinstance(tail, dict):
            value = tail.get("cycle_id")
            if value:
                return str(value)
        if hasattr(tail, "cycle_id"):
            value = getattr(tail, "cycle_id")
            if value:
                return str(value)
    cycle_number = int(state.get("prp_cycle_count", 0) or 0) + 1
    return f"cycle-{cycle_number}"


class ContextEngineCompressionLogger:
    """
    Persists per-thread compression events for the context engine.
    """

    def __init__(self, base_dir: str | Path | None = None) -> None:
        raw_dir = base_dir or os.environ.get("QUADRACODE_CONTEXT_ENGINE_LOG_DIR", "./context_engine_logs")
        self.base_dir = Path(raw_dir).expanduser().resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._locks: dict[Path, threading.Lock] = {}
        self._pending: list[asyncio.Task[Any]] = []

    def record(self, thread_id: str, entry: dict[str, Any]) -> None:
        path = self._log_path(thread_id)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._write_entry(entry, path)
            return
        task = loop.create_task(asyncio.to_thread(self._write_entry, entry, path))
        self._pending.append(task)

        def _cleanup(completed: asyncio.Task[Any]) -> None:
            try:
                completed.result()
            except Exception:  # pragma: no cover - logging best-effort
                pass
            finally:
                self._pending = [pending for pending in self._pending if not pending.done()]

        task.add_done_callback(_cleanup)

    def _write_entry(self, entry: dict[str, Any], path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock = self._locks.setdefault(path, threading.Lock())
        with lock, path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False))
            handle.write("\n")

    def _log_path(self, thread_id: str) -> Path:
        sanitized = thread_id.replace("/", "_")
        return self.base_dir / f"{sanitized}.jsonl"


_COMPRESSION_LOGGER: ContextEngineCompressionLogger | None = None


def get_context_engine_logger() -> ContextEngineCompressionLogger:
    global _COMPRESSION_LOGGER
    if _COMPRESSION_LOGGER is None:
        _COMPRESSION_LOGGER = ContextEngineCompressionLogger()
    return _COMPRESSION_LOGGER


def log_context_compression(
    state: QuadraCodeState | Mapping[str, Any] | None,
    *,
    action: str,
    stage: str,
    reason: str,
    segment_id: str | None,
    segment_type: str | None,
    before_tokens: int,
    after_tokens: int,
    before_content: str,
    after_content: str,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    """
    Records a compression or summarization event for later inspection.
    """

    logger = get_context_engine_logger()
    thread_id = _resolve_thread_id(state)
    cycle_id = _resolve_cycle_id(state)
    before = max(int(before_tokens or 0), 0)
    after = max(int(after_tokens or 0), 0)
    tokens_saved = before - after
    ratio = float(after / before) if before else 0.0
    context_used = None
    context_max = None
    prp_state = None
    exhaustion_mode = None

    if isinstance(state, Mapping):
        context_used = int(state.get("context_window_used", 0) or 0)
        context_max = int(state.get("context_window_max", 0) or 0) or None
        prp_state = getattr(state.get("prp_state"), "value", state.get("prp_state"))
        exhaustion_mode = getattr(state.get("exhaustion_mode"), "value", state.get("exhaustion_mode"))

    entry = {
        "timestamp": _utc_iso(),
        "thread_id": thread_id,
        "cycle_id": cycle_id,
        "stage": stage,
        "action": action,
        "reason": reason,
        "segment_id": segment_id,
        "segment_type": segment_type,
        "before_tokens": before,
        "after_tokens": after,
        "tokens_saved": tokens_saved,
        "compression_ratio": ratio,
        "context_window_used": context_used,
        "context_window_max": context_max,
        "prp_state": prp_state,
        "exhaustion_mode": exhaustion_mode,
        "before_preview": _preview(before_content),
        "after_preview": _preview(after_content),
        "metadata": dict(metadata or {}),
    }
    logger.record(thread_id, entry)

