from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, List, Tuple

from langchain_core.messages import BaseMessage, HumanMessage, message_to_dict
from langchain_core.messages.utils import get_buffer_string

from ..config import ContextEngineConfig
from ..state import ContextSegment, QuadraCodeState
from .context_reducer import ContextReducer


@dataclass(slots=True)
class ContextResetArtifacts:
    reset_id: str
    root: str
    history_path: str
    trimmed_history_path: str
    history_jsonl_path: str
    segments_path: str
    summary_path: str
    system_prompt_path: str
    metadata_path: str
    summary: str
    prompt_addendum: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "reset_id": self.reset_id,
            "root": self.root,
            "history_path": self.history_path,
            "trimmed_history_path": self.trimmed_history_path,
            "history_jsonl_path": self.history_jsonl_path,
            "segments_path": self.segments_path,
            "summary_path": self.summary_path,
            "system_prompt_path": self.system_prompt_path,
            "metadata_path": self.metadata_path,
            "summary": self.summary,
            "prompt_addendum": self.prompt_addendum,
        }


class ContextResetAgent:
    def __init__(
        self,
        config: ContextEngineConfig,
        *,
        system_prompt: str,
        reducer: ContextReducer | None = None,
    ) -> None:
        self.config = config
        self.system_prompt = system_prompt
        self.reducer = reducer or ContextReducer(config)
        self._history_char_limit = 40_000
        self._segments_char_limit = 12_000

    async def reset_if_needed(
        self, state: QuadraCodeState
    ) -> tuple[QuadraCodeState, bool, ContextResetArtifacts | None]:
        if not self._should_reset(state):
            return state, False, None
        updated, artifacts = await self.reset_context(state)
        return updated, True, artifacts

    async def reset_context(
        self,
        state: QuadraCodeState,
    ) -> tuple[QuadraCodeState, ContextResetArtifacts]:
        """
        Persist context reset artifacts and trim active context.

        Args:
            state (QuadraCodeState): Active state to persist and reset.

        Returns:
            tuple[QuadraCodeState, ContextResetArtifacts]: Updated state and artifact metadata.

        Raises:
            RuntimeError: If reset artifacts cannot be written to disk.

        Last Grunted: 01/30/2026 09:45:00 AM PST
        """
        state = state.copy()
        messages = list(state.get("messages") or [])
        segments = list(state.get("context_segments") or [])
        thread_id = str(state.get("thread_id") or "global")
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        reset_id = f"{self._sanitize_thread_id(thread_id)}-{timestamp}"

        reset_root = self._resolve_reset_root()
        reset_dir = reset_root / self._sanitize_thread_id(thread_id) / reset_id
        try:
            reset_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(f"Context reset failed to create directory: {reset_dir}") from exc

        history_markdown = self._render_history_markdown(messages)
        trimmed_messages = self._trim_messages_by_turn(
            messages,
            keep_turns=max(1, int(self.config.context_reset_keep_turns)),
        )
        trimmed_markdown = self._render_history_markdown(trimmed_messages)
        segments_jsonl = self._serialize_segments_jsonl(segments)

        history_markdown = self._truncate(history_markdown, self._history_char_limit)
        trimmed_markdown = self._truncate(trimmed_markdown, self._history_char_limit)
        segments_prompt = self._truncate(self._render_segments_prompt(segments), self._segments_char_limit)

        summary_prompt = self.config.prompt_templates.get_prompt(
            "context_reset_summary_prompt",
            history=history_markdown,
            segments=segments_prompt,
        )
        summary_text, summary_tokens = await self._summarize_context(summary_prompt)

        history_path = reset_dir / "history.md"
        trimmed_history_path = reset_dir / "trimmed_history.md"
        history_jsonl_path = reset_dir / "history.jsonl"
        segments_path = reset_dir / "context_segments.jsonl"
        summary_path = reset_dir / "context_summary.md"
        system_prompt_path = reset_dir / "system_prompt.md"
        metadata_path = reset_dir / "reset_metadata.json"

        try:
            self._write_text(history_path, history_markdown)
            self._write_text(trimmed_history_path, trimmed_markdown)
            self._write_text(summary_path, summary_text)
            self._write_text(system_prompt_path, self.system_prompt)
            self._write_jsonl(history_jsonl_path, self._serialize_messages_jsonl(messages))
            self._write_raw_jsonl(segments_path, segments_jsonl)
        except OSError as exc:
            raise RuntimeError(f"Context reset failed to write artifacts: {reset_dir}") from exc

        prompt_addendum = self.config.prompt_templates.get_prompt(
            "context_reset_system_prompt",
            summary=summary_text,
            history_path=str(history_path),
            trimmed_history_path=str(trimmed_history_path),
            segments_path=str(segments_path),
            metadata_path=str(metadata_path),
        )

        metadata_payload = {
            "reset_id": reset_id,
            "thread_id": thread_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "reset_root": str(reset_root),
            "history_path": str(history_path),
            "trimmed_history_path": str(trimmed_history_path),
            "history_jsonl_path": str(history_jsonl_path),
            "segments_path": str(segments_path),
            "summary_path": str(summary_path),
            "system_prompt_path": str(system_prompt_path),
            "summary_tokens": summary_tokens,
            "message_count": len(messages),
            "trimmed_message_count": len(trimmed_messages),
            "segment_count": len(segments),
        }
        try:
            metadata_path.write_text(json.dumps(metadata_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(f"Context reset failed to write metadata: {metadata_path}") from exc

        state["system_prompt_addendum"] = prompt_addendum
        state["context_reset_count"] = int(state.get("context_reset_count", 0) or 0) + 1
        reset_log = state.setdefault("context_reset_log", [])
        if isinstance(reset_log, list):
            reset_log.append(dict(metadata_payload))
        state["last_context_reset"] = dict(metadata_payload)

        summary_segment: ContextSegment = {
            "id": f"context-reset-summary-{reset_id}",
            "content": summary_text,
            "type": "context_reset_summary",
            "priority": 10,
            "token_count": summary_tokens,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "decay_rate": 0.0,
            "compression_eligible": False,
            "restorable_reference": str(summary_path),
        }
        history_segment: ContextSegment = {
            "id": f"context-reset-history-{reset_id}",
            "content": (
                "Context reset history artifacts:\n"
                f"- history.md: {history_path}\n"
                f"- trimmed_history.md: {trimmed_history_path}\n"
                f"- history.jsonl: {history_jsonl_path}\n"
            ),
            "type": "context_reset_history",
            "priority": 9,
            "token_count": max(1, len(str(history_path).split())),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "decay_rate": 0.0,
            "compression_eligible": False,
            "restorable_reference": str(history_path),
        }

        state["messages"] = trimmed_messages
        state["context_segments"] = [summary_segment, history_segment]

        artifacts = ContextResetArtifacts(
            reset_id=reset_id,
            root=str(reset_root),
            history_path=str(history_path),
            trimmed_history_path=str(trimmed_history_path),
            history_jsonl_path=str(history_jsonl_path),
            segments_path=str(segments_path),
            summary_path=str(summary_path),
            system_prompt_path=str(system_prompt_path),
            metadata_path=str(metadata_path),
            summary=summary_text,
            prompt_addendum=prompt_addendum,
        )

        return state, artifacts

    def _should_reset(self, state: QuadraCodeState) -> bool:
        if not self.config.context_reset_enabled:
            return False
        messages = state.get("messages") or []
        user_turns = self._count_user_turns(messages)
        if user_turns < max(1, int(self.config.context_reset_min_user_turns)):
            return False
        used = int(state.get("context_window_used", 0) or 0)
        if used <= 0:
            return False
        trigger_tokens = int(self.config.context_reset_trigger_tokens or 0)
        if trigger_tokens > 0:
            return used >= trigger_tokens
        max_tokens = int(state.get("context_window_max", 0) or 0)
        if max_tokens <= 0:
            return False
        ratio = used / max_tokens
        return ratio >= float(self.config.context_reset_trigger_ratio)

    async def _summarize_context(self, prompt: str) -> Tuple[str, int]:
        reduction = await self.reducer.reduce(prompt, focus="context_reset")
        return reduction.content, reduction.token_count

    def _resolve_reset_root(self) -> Path:
        root = self.config.context_reset_root
        if not root:
            root = str(Path(self.config.external_memory_path) / "context_resets")
        return Path(root).expanduser().resolve()

    def _render_history_markdown(self, messages: Iterable[BaseMessage]) -> str:
        if not messages:
            return "No conversation history available."
        rendered = get_buffer_string(list(messages))
        return "# Conversation History\n\n" + rendered.strip()

    def _render_segments_prompt(self, segments: Iterable[ContextSegment]) -> str:
        lines: List[str] = []
        for segment in segments:
            segment_id = segment.get("id", "unknown")
            segment_type = segment.get("type", "context")
            content = str(segment.get("content", "")).strip()
            if not content:
                continue
            lines.append(f"[{segment_type}:{segment_id}]\n{content}")
        if not lines:
            return "No context segments available."
        return "\n\n".join(lines)

    def _serialize_messages_jsonl(self, messages: Iterable[BaseMessage]) -> List[dict[str, Any]]:
        payload: List[dict[str, Any]] = []
        for idx, message in enumerate(messages):
            record = message_to_dict(message)
            record["index"] = idx
            payload.append(record)
        return payload

    def _serialize_segments_jsonl(self, segments: Iterable[ContextSegment]) -> List[dict[str, Any]]:
        return [dict(segment) for segment in segments]

    def _write_text(self, path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")

    def _write_jsonl(self, path: Path, records: List[dict[str, Any]]) -> None:
        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False))
                handle.write("\n")

    def _write_raw_jsonl(self, path: Path, records: List[dict[str, Any]]) -> None:
        self._write_jsonl(path, records)

    def _trim_messages_by_turn(
        self, messages: List[BaseMessage], *, keep_turns: int
    ) -> List[BaseMessage]:
        if keep_turns <= 0 or not messages:
            return []
        user_indices = [idx for idx, msg in enumerate(messages) if self._is_user_message(msg)]
        if len(user_indices) <= keep_turns:
            return list(messages)
        start_idx = user_indices[-keep_turns]
        return list(messages[start_idx:])

    def _count_user_turns(self, messages: Iterable[BaseMessage]) -> int:
        return sum(1 for msg in messages if self._is_user_message(msg))

    @staticmethod
    def _is_user_message(message: BaseMessage) -> bool:
        if isinstance(message, HumanMessage):
            return True
        role = getattr(message, "role", None)
        if isinstance(role, str) and role.lower() == "user":
            return True
        msg_type = getattr(message, "type", "")
        return str(msg_type).lower() in {"human", "user"}

    @staticmethod
    def _sanitize_thread_id(thread_id: str) -> str:
        return thread_id.replace("/", "_").replace(" ", "_").strip("_") or "global"

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        if limit <= 0 or len(text) <= limit:
            return text
        return text[:limit] + "..."
