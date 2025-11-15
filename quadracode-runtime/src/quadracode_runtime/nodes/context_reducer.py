"""
This module provides the `ContextReducer`, a utility for summarizing and 
condensing large context segments.

The `ContextReducer` is a key component of the context engine's optimization 
process. It is designed to reduce the token count of verbose context segments 
while preserving their essential information. It can operate in two modes: a 
fast, heuristic-based mode that uses simple truncation and keyword focusing, and 
a more powerful, LLM-based mode that can generate more nuanced summaries. This 
dual-mode capability allows for a flexible trade-off between speed and quality.
"""

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
    """
    Represents the result of a context reduction operation.

    Attributes:
        content: The reduced (summarized or compressed) content.
        token_count: The estimated token count of the reduced content.
    """
    content: str
    token_count: int


class ContextReducer:
    """
    Reduces the size of long context segments using either heuristics or an LLM.

    This class provides the `reduce` method, which is the main entry point for 
    the reduction process. It automatically selects the appropriate reduction 
    strategy (heuristic or LLM) based on the configuration and then executes 
    the summarization.

    Attributes:
        config: The configuration for the context engine.
        model_name: The name of the LLM to use for reduction, or "heuristic".
        ... and other configuration parameters.
    """

    def __init__(self, config: ContextEngineConfig) -> None:
        """
        Initializes the `ContextReducer`.

        Args:
            config: The configuration for the context engine.
        """
        self.config = config
        self.model_name = config.reducer_model
        self.chunk_tokens = max(50, config.reducer_chunk_tokens)
        self.target_tokens = max(20, config.reducer_target_tokens)
        self._llm = None
        self._llm_lock = asyncio.Lock()

    async def reduce(self, content: str, *, focus: str | None = None) -> ReducerResult:
        """
        Reduces the content of a context segment to a target size.

        This method is the main public interface for the `ContextReducer`. It 
        takes a string of content and a focus hint, and then applies either a 
        heuristic or an LLM-based reduction strategy to produce a condensed 
        version of the content.

        Args:
            content: The content to be reduced.
            focus: An optional hint to guide the reduction process.

        Returns:
            A `ReducerResult` containing the reduced content and its token count.
        """
        if not content.strip():
            return ReducerResult(content="", token_count=0)

        if not self.model_name or self.model_name.lower() == "heuristic":
            summary = self._heuristic_reduce(content, focus=focus)
            return ReducerResult(content=summary, token_count=self._estimate_tokens(summary))

        summary = await self._reduce_with_llm(content, focus=focus)
        return ReducerResult(content=summary, token_count=self._estimate_tokens(summary))

    def _heuristic_reduce(self, content: str, *, focus: str | None = None) -> str:
        """
        Performs a fast, heuristic-based reduction of the content.

        This method uses simple truncation and keyword-based scoring to produce a 
        summary. It is much faster than the LLM-based approach but may be less 
        accurate.
        """
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
        """
        Performs a high-quality, LLM-based reduction of the content.

        This method chunks the content, generates a summary for each chunk using 
        an LLM, and then combines the partial summaries into a final, coherent 
        summary.
        """
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
        """
        Manages the lifecycle of the LLM client, ensuring it is initialized 
        and thread-safe.
        """
        if self._llm is not None:
            return self._llm
        async with self._llm_lock:
            if self._llm is None:
                self._llm = init_chat_model(self.model_name)
        return self._llm

    def _chunk_content(self, content: str) -> Iterable[str]:
        """Splits a string of content into chunks of a specified size."""
        words = content.split()
        chunk_size = self.chunk_tokens
        for start in range(0, len(words), chunk_size):
            yield " ".join(words[start : start + chunk_size])

    def _estimate_tokens(self, text: str) -> int:
        """Estimates the number of tokens in a string of text."""
        return max(1, len(text.split()))
