"""Context reduction utilities for summarising large segments."""

from __future__ import annotations

import asyncio
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Optional

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage

from ..config import ContextEngineConfig


@dataclass
class ReducerResult:
    content: str
    token_count: int


class ContextReducer:
    """Reduces long context using either heuristics or an LLM."""

    def __init__(self, config: ContextEngineConfig) -> None:
        self.config = config
        self.model_name = config.reducer_model
        self.chunk_tokens = max(50, config.reducer_chunk_tokens)
        self.target_tokens = max(20, config.reducer_target_tokens)
        self._llm = None
        self._llm_lock = asyncio.Lock()

    async def reduce(self, content: str, *, focus: str | None = None) -> ReducerResult:
        if not content.strip():
            return ReducerResult(content="", token_count=0)

        if not self.model_name or self.model_name.lower() == "heuristic":
            summary = self._heuristic_reduce(content, focus=focus)
            return ReducerResult(content=summary, token_count=self._estimate_tokens(summary))

        summary = await self._reduce_with_llm(content, focus=focus)
        return ReducerResult(content=summary, token_count=self._estimate_tokens(summary))

    def _heuristic_reduce(self, content: str, *, focus: str | None = None) -> str:
        tokens = content.split()
        if len(tokens) <= self.target_tokens:
            return content
        focus_tokens = set()
        if focus:
            for token in re.findall(r"\b\w+\b", focus.lower()):
                if len(token) > 3:
                    focus_tokens.add(token)
        if focus_tokens:
            weighted: list[tuple[int, str]] = []
            window = max(5, self.target_tokens * 2)
            for i in range(0, len(tokens), window):
                chunk = tokens[i : i + window]
                score = sum(1 for token in chunk if token.lower() in focus_tokens)
                weighted.append((score, " ".join(chunk)))
            weighted.sort(key=lambda item: item[0], reverse=True)
            selected_words: list[str] = []
            for _, chunk in weighted:
                for word in chunk.split():
                    selected_words.append(word)
                    if len(selected_words) >= self.target_tokens:
                        summary = " ".join(selected_words) + " …"
                        return summary
        summary_words = tokens[: self.target_tokens]
        return " ".join(summary_words) + " …"

    async def _reduce_with_llm(self, content: str, *, focus: str | None = None) -> str:
        llm = await self._ensure_llm()
        chunks = self._chunk_content(content)
        partial_summaries: list[str] = []
        system_prompt = (
            "You condense technical context. Use structured bullet points. Keep critical details."
        )
        for chunk in chunks:
            focus_clause = f" Focus on {focus}." if focus else ""
            prompt = (
                "Summarize the following context into concise bullet points." + focus_clause +
                " Limit to approximately "
                f"{self.target_tokens} tokens.\n\n```\n{chunk}\n```"
            )
            response = await asyncio.to_thread(
                llm.invoke,
                [SystemMessage(content=system_prompt), HumanMessage(content=prompt)],
            )
            partial_summaries.append(str(response.content).strip())
        combined = "\n\n".join(partial_summaries)
        if len(partial_summaries) == 1:
            return combined
        final_prompt = (
            "Combine the following partial summaries into a single concise summary."
            " Preserve key facts and actions. Use bullet points when helpful."
            "\n\n" + combined
        )
        response = await asyncio.to_thread(
            llm.invoke,
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=final_prompt),
            ],
        )
        return str(response.content).strip()

    async def _ensure_llm(self):
        if self._llm is not None:
            return self._llm
        async with self._llm_lock:
            if self._llm is None:
                self._llm = init_chat_model(self.model_name)
        return self._llm

    def _chunk_content(self, content: str) -> Iterable[str]:
        words = content.split()
        chunk_size = self.chunk_tokens
        for start in range(0, len(words), chunk_size):
            yield " ".join(words[start : start + chunk_size])

    def _estimate_tokens(self, text: str) -> int:
        return max(1, len(text.split()))
