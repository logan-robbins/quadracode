"""
This module provides the `ContextReducer`, a utility for summarizing and 
condensing large context segments exclusively via LLM compression.

The `ContextReducer` is a key component of the context engine's optimization 
process. It is designed to reduce the token count of verbose context segments 
while preserving their essential information. Unlike earlier versions that 
allowed heuristic fallbacks, the reducer now always routes through a configured 
LLM to ensure consistent, high-quality reductions and observability.
"""

from __future__ import annotations

import asyncio
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
    Reduces the size of long context segments using an LLM summarization pass.

    This class provides the `reduce` method, which is the main entry point for 
    the reduction process. It chunkifies input, prompts the configured LLM, and
    stitches the resulting summaries into a final payload sized for the context
    window.

    Attributes:
        config: The configuration for the context engine.
        model_name: The name of the LLM to use for reduction.
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
        if not self.model_name:
            raise ValueError(
                "ContextReducer requires `config.reducer_model` to reference a valid LLM."
            )
        if self.model_name.strip().lower() == "heuristic":
            raise ValueError(
                "Heuristic compression has been removed. "
                "Set QUADRACODE_REDUCER_MODEL to an LLM identifier."
            )
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

        summary = await self._reduce_with_llm(content, focus=focus)
        return ReducerResult(content=summary, token_count=self._estimate_tokens(summary))

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
        
        # Use configurable prompts
        prompts = self.config.prompt_templates
        system_prompt = prompts.reducer_system_prompt
        
        # Check if we should use domain-specific templates
        domain = self._detect_domain(content, focus)
        if domain:
            system_prompt = prompts.customize_for_domain(system_prompt, domain)
        
        # Get compression profile based on context pressure
        compression_profile = prompts.get_compression_profile(self.config.compression_profile)
        
        for chunk in chunks:
            # Build focus clause if needed
            focus_clause = prompts.reducer_focus_clause.format(focus=focus) if focus else ""
            
            # Format the chunk prompt
            prompt = prompts.get_prompt(
                "reducer_chunk_prompt",
                focus_clause=focus_clause,
                target_tokens=self.target_tokens,
                chunk=chunk
            )
            
            response = await asyncio.to_thread(
                llm.invoke,
                [SystemMessage(content=system_prompt), HumanMessage(content=prompt)],
            )
            partial_summaries.append(str(response.content).strip())
        
        combined = "\n\n".join(partial_summaries)
        if len(partial_summaries) == 1:
            return combined
        
        # Use configurable combine prompt
        final_prompt = prompts.get_prompt(
            "reducer_combine_prompt",
            combined=combined
        )
        
        response = await asyncio.to_thread(
            llm.invoke,
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=final_prompt),
            ],
        )
        return str(response.content).strip()
    
    def _detect_domain(self, content: str, focus: str | None = None) -> str | None:
        """Detect the domain of the content for domain-specific compression."""
        if focus:
            focus_lower = focus.lower()
            if "code" in focus_lower or "function" in focus_lower or "class" in focus_lower:
                return "code"
            if "doc" in focus_lower or "readme" in focus_lower:
                return "documentation"
            if "test" in focus_lower:
                return "test_results"
            if "tool" in focus_lower:
                return "tool_output"
        
        # Simple heuristic based on content patterns
        if "def " in content or "class " in content or "import " in content:
            return "code"
        if "# " in content and "##" in content:  # Markdown headers
            return "documentation"
        if "PASSED" in content or "FAILED" in content or "test_" in content:
            return "test_results"
        
        return None

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
