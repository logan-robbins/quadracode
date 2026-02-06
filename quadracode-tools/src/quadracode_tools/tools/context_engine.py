"""Provides a LangChain tool for inspecting the Quadracode Context Engine.

This module offers the ``inspect_context_engine`` tool, which aggregates and
summarizes recent context engine activity (stage transitions, compression
events, exhaustion mode changes) from JSONL log files.  Agents use this tool
to understand the current state of their context window and any compression
or reset operations that have occurred.
"""
from __future__ import annotations

import json
import os
from collections import deque
from pathlib import Path
from collections.abc import Iterable
from typing import Any

from langchain_core.tools import tool


def _time_travel_dir() -> Path:
    raw = os.environ.get("QUADRACODE_TIME_TRAVEL_DIR", "./time_travel_logs")
    return Path(raw).expanduser().resolve()


def _compression_log_dir() -> Path:
    raw = os.environ.get("QUADRACODE_CONTEXT_ENGINE_LOG_DIR", "./context_engine_logs")
    return Path(raw).expanduser().resolve()


def _load_jsonl_tail(path: Path, limit: int) -> list[dict[str, Any]]:
    entries: deque[dict[str, Any]] = deque(maxlen=limit)
    try:
        with path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    entries.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        return []
    return list(entries)


def _format_float(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "?"
    return f"{number:.2f}"


def _summarize_stage_entries(entries: Iterable[dict[str, Any]], limit: int) -> tuple[list[str], dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for entry in entries:
        event = str(entry.get("event") or "")
        if event.startswith("stage.") or event == "exhaustion_update":
            filtered.append(entry)
    tail = filtered[-limit:]
    lines: list[str] = []
    last_quality = None
    last_exhaustion = None
    for item in tail:
        timestamp = item.get("timestamp", "")
        event = str(item.get("event") or "")
        payload = item.get("payload") or {}
        if event.startswith("stage."):
            stage_name = event.split(".", 1)[1]
            tokens = payload.get("context_window_used")
            quality = payload.get("quality_score")
            segments = payload.get("context_segments")
            if isinstance(segments, list):
                segments = len(segments)
            elif segments is None:
                segments = payload.get("context_segments_count")
            exhaustion = item.get("exhaustion_mode") or "unknown"
            if quality is not None:
                last_quality = quality
            if exhaustion:
                last_exhaustion = exhaustion
            lines.append(
                f"- [{timestamp}] {stage_name:<16} tokens={tokens if tokens is not None else '?'} "
                f"quality={_format_float(quality)} segments={segments if segments is not None else '?'} "
                f"exhaustion={exhaustion}"
            )
        else:
            lines.append(
                f"- [{timestamp}] exhaustion transition {payload.get('from', '?')} -> {payload.get('to', '?')} "
                f"(action={payload.get('action', payload.get('reason', 'unknown'))})"
            )
    meta = {
        "count": len(tail),
        "last_quality": last_quality,
        "last_exhaustion": last_exhaustion,
    }
    return lines, meta


def _summarize_compression_entries(entries: Iterable[dict[str, Any]], limit: int) -> tuple[list[str], dict[str, Any]]:
    tail = list(entries)[-limit:]
    lines: list[str] = []
    total_saved = 0
    ratios: list[float] = []
    for item in tail:
        before = int(item.get("before_tokens") or 0)
        after = int(item.get("after_tokens") or 0)
        saved = before - after
        total_saved += saved
        ratio = item.get("compression_ratio")
        if isinstance(ratio, (int, float)):
            ratios.append(float(ratio))
        timestamp = item.get("timestamp", "")
        action = item.get("action", "compress")
        lines.append(
            f"- [{timestamp}] {action} segment={item.get('segment_id') or '?'} "
            f"type={item.get('segment_type') or 'unknown'} {before}->{after} (Δ {saved}) "
            f"reason={item.get('reason')} stage={item.get('stage')}"
        )
    meta = {
        "count": len(tail),
        "total_saved": total_saved,
        "avg_ratio": sum(ratios) / len(ratios) if ratios else None,
    }
    return lines, meta


def _format_section(title: str, lines: list[str], footer: str | None = None) -> str:
    if not lines:
        if footer:
            return f"{title}\n{footer}"
        return f"{title}\n(No data found.)"
    body = "\n".join(lines)
    if footer:
        return f"{title}\n{body}\n{footer}"
    return f"{title}\n{body}"


@tool
def inspect_context_engine(thread_id: str | None = None, last_n_events: int = 20) -> str:
    """Summarizes recent context engine activity for a thread using time-travel and compression logs."""
    normalized_thread = (thread_id or "global").strip() or "global"
    history_limit = max(5, min(last_n_events, 200))

    time_travel_path = _time_travel_dir() / f"{normalized_thread}.jsonl"
    stages = _load_jsonl_tail(time_travel_path, history_limit * 4)
    stage_lines, stage_meta = _summarize_stage_entries(stages, history_limit)

    compression_path = _compression_log_dir() / f"{normalized_thread}.jsonl"
    compression_entries = _load_jsonl_tail(compression_path, history_limit)
    compression_lines, compression_meta = _summarize_compression_entries(compression_entries, history_limit)

    sections = [
        f"Context Engine Inspection for thread '{normalized_thread}'",
        "",
        _format_section(
            f"Stage timeline (last {stage_meta['count']} events)",
            stage_lines,
            footer=(
                f"Last quality={_format_float(stage_meta['last_quality'])}, "
                f"Last exhaustion={stage_meta['last_exhaustion'] or 'unknown'}"
                if stage_meta["count"]
                else None
            ),
        ),
        "",
        _format_section(
            f"Compression history (last {compression_meta['count']} events)",
            compression_lines,
            footer=(
                f"Total tokens saved={compression_meta['total_saved']}, "
                f"Average ratio={_format_float(compression_meta['avg_ratio'])}"
                if compression_meta["count"]
                else None
            ),
        ),
    ]

    return "\n".join(section for section in sections if section).strip()

