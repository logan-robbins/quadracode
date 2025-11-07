"""Shared helpers for Perpetual Refinement Protocol integrations."""

from __future__ import annotations

import json

import yaml

from quadracode_contracts import HumanCloneTrigger


def _strip_markdown_fence(content: str) -> str:
    text = content.strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return text


def parse_human_clone_trigger(raw: str) -> HumanCloneTrigger:
    """Parse a HumanClone trigger payload encoded as JSON or YAML."""

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
