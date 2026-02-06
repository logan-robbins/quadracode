"""
This module provides shared helper functions for integrations with the Perpetual
Refinement Protocol (PRP).

The PRP is a core concept in the Quadracode system, representing the continuous
cycle of planning, refining, and executing tasks. This module contains utilities
that are used by various components of the runtime to interact with the PRP, such
as the `parse_human_clone_trigger` function, which is responsible for parsing the
structured feedback from the supervisor.
"""

from __future__ import annotations

import json

import yaml

from quadracode_contracts import HumanCloneTrigger


def _strip_markdown_fence(content: str) -> str:
    """
    Strips the markdown code fence from a string of content, if present.
    """
    text = content.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return text


def parse_human_clone_trigger(raw: str) -> HumanCloneTrigger:
    """
    Parses a raw string, which may be JSON or YAML, into a `HumanCloneTrigger` 
    object.

    This function is designed to be a robust parser for the structured feedback 
    emitted by the HumanClone. It handles the stripping of markdown code fences 
    and can parse both JSON and YAML, making it resilient to variations in the 
    LLM's output format.

    Args:
        raw: The raw string payload from the HumanClone.

    Returns:
        A `HumanCloneTrigger` object.

    Raises:
        ValueError: If the payload is empty or cannot be parsed into a valid 
                    `HumanCloneTrigger`.
    """

    cleaned = _strip_markdown_fence(raw)
    if not cleaned:
        raise ValueError("Empty HumanClone trigger payload.")

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        data = yaml.safe_load(cleaned)

    if not isinstance(data, dict):
        raise ValueError("HumanClone trigger payload must parse to an object.")

    return HumanCloneTrigger(**data)
