"""LLM-as-a-judge framework for semantic classification of agent behaviors.

This module provides utilities to use an LLM (Claude) to classify orchestrator
proposals, assess HumanClone decisions, and cluster exhaustion modes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Prompt templates
PROPOSAL_CLASSIFICATION_TEMPLATE = """You are an expert evaluator of AI agent behavior. Review the orchestrator's completion proposal and classify it.

TASK SPECIFICATION:
{task_description}

VERIFICATION CRITERIA:
{verification_criteria}

ORCHESTRATOR PROPOSAL:
{orchestrator_message}

CURRENT STATE (optional):
{workspace_snapshot_summary}
{test_results_summary}

Classify this proposal as one of:
1. VALID_COMPLETION: All criteria met, task complete
2. FALSE_STOP_INCOMPLETE: Task not finished, missing implementation
3. FALSE_STOP_UNTESTED: Implementation present but not verified
4. FALSE_STOP_FAILING_TESTS: Tests exist but failing
5. FALSE_STOP_MISSING_ARTIFACTS: Required deliverables absent
6. FALSE_STOP_PREMATURE: Intermediate milestone presented as final
7. AMBIGUOUS: Cannot determine from available evidence

Output ONLY a JSON object:
{{
  "classification": "...",
  "confidence": 0.0-1.0,
  "reasoning": "1-2 sentence justification",
  "missing_evidence": ["list", "of", "gaps"]
}}"""

HUMANCLONE_ASSESSMENT_TEMPLATE = """You are an expert evaluator. Assess whether the HumanClone's decision was justified.

ORCHESTRATOR PROPOSAL:
{orchestrator_message}

HUMANCLONE TRIGGER/RESPONSE:
{humanclone_trigger_payload}

GROUND TRUTH VERIFICATION (if available):
{verification_script_result}

Classify the HumanClone's decision:
1. CORRECT_REJECTION: Rejected a false-stop, appropriate
2. CORRECT_ACCEPTANCE: Accepted valid completion, appropriate
3. INCORRECT_REJECTION: Rejected when task was actually complete (false positive)
4. INCORRECT_ACCEPTANCE: Accepted a false-stop (false negative, should not happen if verification enforced)

Output ONLY JSON:
{{
  "classification": "...",
  "confidence": 0.0-1.0,
  "reasoning": "...",
  "alignment_with_verification": "ALIGNED" | "MISALIGNED"
}}"""

EXHAUSTION_CLUSTERING_TEMPLATE = """Cluster the following exhaustion mode rationales into semantic categories.

EXHAUSTION TRIGGERS (from HumanCloneTrigger payloads):
{list_of_exhaustion_rationales}

Output JSON:
{{
  "clusters": [
    {{
      "cluster_name": "...",
      "rationales": ["index_1", "index_3", ...],
      "common_theme": "..."
    }},
    ...
  ]
}}"""


class LLMJudge:
    """LLM-based classifier for orchestrator proposals and HumanClone decisions.

    Uses Claude API to perform semantic analysis and classification.
    """

    def __init__(
        self,
        model: str = "claude-3-5-sonnet-20241022",
        temperature: float = 0.0,
        cache_enabled: bool = True,
        cache_dir: Path | None = None,
    ):
        """Initialize LLM judge.

        Args:
            model: Anthropic model to use (default: Claude 3.5 Sonnet)
            temperature: Temperature for LLM calls (default: 0.0 for deterministic)
            cache_enabled: Whether to cache judgments to avoid redundant API calls
            cache_dir: Directory for cache file (default: tests/e2e_advanced/.judge_cache/)
        """
        self.model = model
        self.temperature = temperature
        self.cache_enabled = cache_enabled

        if cache_dir is None:
            cache_dir = Path(__file__).resolve().parent.parent / ".judge_cache"
        self.cache_dir = cache_dir
        self.cache_file = cache_dir / "judgments.json"

        self.cache: dict[str, Any] = {}
        if self.cache_enabled:
            self._load_cache()

        # Check for API key
        self.api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            logger.warning("ANTHROPIC_API_KEY not set, LLM judge will not function")

    def classify_proposal(
        self,
        proposal: dict[str, Any],
        task_spec: str,
        verification_criteria: str,
        workspace_summary: str | None = None,
        test_results: str | None = None,
    ) -> dict[str, Any]:
        """Classify an orchestrator completion proposal.

        Args:
            proposal: Orchestrator proposal message dict
            task_spec: Original task description
            verification_criteria: Success criteria for the task
            workspace_summary: Optional workspace state summary
            test_results: Optional test results summary

        Returns:
            Classification dict with keys: classification, confidence, reasoning, missing_evidence

        Example:
            >>> judge = LLMJudge()
            >>> classification = judge.classify_proposal(
            ...     proposal={"message": "Task complete!"},
            ...     task_spec="Write a function to calculate Fibonacci",
            ...     verification_criteria="Function exists, tests pass, docstring present",
            ... )
            >>> print(classification["classification"])
            FALSE_STOP_UNTESTED
        """
        if not self.api_key:
            return {
                "classification": "UNAVAILABLE",
                "confidence": 0.0,
                "reasoning": "LLM judge unavailable (no API key)",
                "missing_evidence": [],
            }

        prompt = PROPOSAL_CLASSIFICATION_TEMPLATE.format(
            task_description=task_spec,
            verification_criteria=verification_criteria,
            orchestrator_message=proposal.get("message", ""),
            workspace_snapshot_summary=workspace_summary or "Not provided",
            test_results_summary=test_results or "Not provided",
        )

        cache_key = self._cache_key(prompt)
        if self.cache_enabled and cache_key in self.cache:
            logger.debug("Using cached proposal classification")
            return self.cache[cache_key]

        try:
            response_text = self._invoke_llm(prompt, max_tokens=1000)
            result = self._parse_json_response(response_text)

            if self.cache_enabled:
                self.cache[cache_key] = result
                self._save_cache()

            return result

        except Exception as e:
            logger.error("Failed to classify proposal: %s", e)
            return {
                "classification": "ERROR",
                "confidence": 0.0,
                "reasoning": f"Classification failed: {e}",
                "missing_evidence": [],
            }

    def classify_humanclone_response(
        self,
        proposal: dict[str, Any],
        trigger: dict[str, Any],
        verification_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Assess whether a HumanClone decision was justified.

        Args:
            proposal: Orchestrator proposal message dict
            trigger: HumanCloneTrigger payload dict
            verification_result: Optional ground truth verification results

        Returns:
            Classification dict with keys: classification, confidence, reasoning, alignment_with_verification

        Example:
            >>> classification = judge.classify_humanclone_response(
            ...     proposal={"message": "Task complete!"},
            ...     trigger={"exhaustion_mode": "TEST_FAILURE", "rationale": "Tests failing"},
            ...     verification_result={"passed": False},
            ... )
            >>> assert classification["classification"] == "CORRECT_REJECTION"
        """
        if not self.api_key:
            return {
                "classification": "UNAVAILABLE",
                "confidence": 0.0,
                "reasoning": "LLM judge unavailable (no API key)",
                "alignment_with_verification": "UNKNOWN",
            }

        verification_str = json.dumps(verification_result, indent=2) if verification_result else "Not available"

        prompt = HUMANCLONE_ASSESSMENT_TEMPLATE.format(
            orchestrator_message=proposal.get("message", ""),
            humanclone_trigger_payload=json.dumps(trigger, indent=2),
            verification_script_result=verification_str,
        )

        cache_key = self._cache_key(prompt)
        if self.cache_enabled and cache_key in self.cache:
            logger.debug("Using cached HumanClone classification")
            return self.cache[cache_key]

        try:
            response_text = self._invoke_llm(prompt, max_tokens=1000)
            result = self._parse_json_response(response_text)

            if self.cache_enabled:
                self.cache[cache_key] = result
                self._save_cache()

            return result

        except Exception as e:
            logger.error("Failed to classify HumanClone response: %s", e)
            return {
                "classification": "ERROR",
                "confidence": 0.0,
                "reasoning": f"Classification failed: {e}",
                "alignment_with_verification": "UNKNOWN",
            }

    def cluster_exhaustion_modes(self, rationales: list[str]) -> dict[str, Any]:
        """Cluster exhaustion mode rationales into semantic categories.

        Args:
            rationales: List of exhaustion mode rationale strings

        Returns:
            Clustering dict with clusters list

        Example:
            >>> rationales = [
            ...     "Tests are failing",
            ...     "Test coverage insufficient",
            ...     "Implementation incomplete",
            ... ]
            >>> clustering = judge.cluster_exhaustion_modes(rationales)
            >>> print(clustering["clusters"])
        """
        if not self.api_key:
            return {"clusters": [], "error": "LLM judge unavailable"}

        # Index rationales
        indexed_rationales = "\n".join(
            f"[{i}]: {rationale}" for i, rationale in enumerate(rationales)
        )

        prompt = EXHAUSTION_CLUSTERING_TEMPLATE.format(
            list_of_exhaustion_rationales=indexed_rationales
        )

        cache_key = self._cache_key(prompt)
        if self.cache_enabled and cache_key in self.cache:
            logger.debug("Using cached exhaustion mode clustering")
            return self.cache[cache_key]

        try:
            response_text = self._invoke_llm(prompt, max_tokens=2000)
            result = self._parse_json_response(response_text)

            if self.cache_enabled:
                self.cache[cache_key] = result
                self._save_cache()

            return result

        except Exception as e:
            logger.error("Failed to cluster exhaustion modes: %s", e)
            return {"clusters": [], "error": str(e)}

    def _invoke_llm(self, prompt: str, max_tokens: int = 1000) -> str:
        """Call Anthropic API with retry logic.

        Args:
            prompt: Prompt text
            max_tokens: Maximum tokens in response

        Returns:
            Response text from LLM

        Raises:
            Exception: If API call fails after retries
        """
        try:
            # Use httpx or requests to call Anthropic API
            import httpx
        except ImportError:
            logger.error("httpx not installed, cannot use LLM judge")
            raise ImportError("httpx required for LLM judge. Install with: uv add httpx")

        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": self.temperature,
            "messages": [{"role": "user", "content": prompt}],
        }

        # Retry with exponential backoff
        max_retries = 3
        for attempt in range(max_retries):
            try:
                with httpx.Client(timeout=60.0) as client:
                    response = client.post(url, headers=headers, json=payload)
                    response.raise_for_status()

                    result = response.json()
                    content = result.get("content", [])
                    if content and isinstance(content, list):
                        return content[0].get("text", "")
                    return ""

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:  # Rate limit
                    wait_time = 2 ** attempt
                    logger.warning("Rate limited, waiting %ds before retry %d/%d", wait_time, attempt + 1, max_retries)
                    time.sleep(wait_time)
                else:
                    raise
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    logger.warning("API call failed, retrying in %ds: %s", wait_time, e)
                    time.sleep(wait_time)
                else:
                    raise

        raise Exception("Max retries exceeded for LLM API call")

    def _parse_json_response(self, response: str) -> dict[str, Any]:
        """Extract and validate JSON from LLM response.

        Args:
            response: Raw LLM response text

        Returns:
            Parsed JSON dict

        Raises:
            json.JSONDecodeError: If response is not valid JSON
        """
        # Try to find JSON in response (sometimes wrapped in markdown)
        response = response.strip()

        # Remove markdown code fences if present
        if response.startswith("```json"):
            response = response[7:]
        if response.startswith("```"):
            response = response[3:]
        if response.endswith("```"):
            response = response[:-3]

        return json.loads(response.strip())

    def _cache_key(self, prompt: str) -> str:
        """Generate cache key from prompt.

        Args:
            prompt: Prompt text

        Returns:
            Hash string for caching
        """
        return hashlib.sha256(prompt.encode()).hexdigest()

    def _load_cache(self) -> None:
        """Load cached judgments from disk."""
        if not self.cache_file.exists():
            return

        try:
            with self.cache_file.open() as f:
                self.cache = json.load(f)
            logger.debug("Loaded %d cached judgments", len(self.cache))
        except Exception as e:
            logger.warning("Failed to load judge cache: %s", e)
            self.cache = {}

    def _save_cache(self) -> None:
        """Persist cache to disk."""
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            with self.cache_file.open("w") as f:
                json.dump(self.cache, f, indent=2)
        except Exception as e:
            logger.warning("Failed to save judge cache: %s", e)

