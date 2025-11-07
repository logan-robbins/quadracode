"""Validation middleware for runtime message handling."""

from __future__ import annotations

from typing import Tuple

from pydantic import ValidationError

from quadracode_contracts import (
    HUMAN_CLONE_RECIPIENT,
    MessageEnvelope,
)

from .prp import parse_human_clone_trigger


def validate_human_clone_envelope(
    envelope: MessageEnvelope,
) -> Tuple[bool, MessageEnvelope | None]:
    """Validate HumanClone payloads before orchestration processing.

    Returns a tuple indicating whether the envelope is valid and an optional
    response envelope that should be published when validation fails.
    """

    if envelope.sender != HUMAN_CLONE_RECIPIENT:
        return True, None

    content = envelope.message or ""
    try:
        parse_human_clone_trigger(content)
    except (ValueError, ValidationError) as exc:
        error_summary = str(exc)
        response = MessageEnvelope(
            sender=envelope.recipient,
            recipient=envelope.sender,
            message=(
                "HumanClone response failed schema validation. "
                "Resubmit using the prescribed JSON structure."
            ),
            payload={
                "schema_error": error_summary,
                "original_message": content,
            },
        )
        return False, response

    return True, None
