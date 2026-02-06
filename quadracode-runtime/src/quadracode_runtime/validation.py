"""
Validation middleware for ensuring the integrity of runtime messages.

This module provides functions to validate incoming `MessageEnvelope` objects,
particularly those originating from specialized agents like the supervisor.
By validating message payloads against their expected schemas before they
enter the core processing logic, the system can prevent malformed data from
causing downstream errors.

The primary function, `validate_supervisor_envelope`, checks messages sent
by the supervisor agent to ensure they conform to the `HumanCloneTrigger` schema.
If validation fails, it generates a detailed error response that can be sent
back to the originator, facilitating correction and resubmission.
"""

from __future__ import annotations

from typing import Tuple

from pydantic import ValidationError

from quadracode_contracts import (
    HUMAN_CLONE_RECIPIENT,
    MessageEnvelope,
)

from .prp import parse_human_clone_trigger


def validate_supervisor_envelope(
    envelope: MessageEnvelope,
) -> Tuple[bool, MessageEnvelope | None]:
    """
    Validates the payload of a `MessageEnvelope` sent by the supervisor agent.

    This function specifically targets messages where the sender is identified as
    `HUMAN_CLONE_RECIPIENT`. It attempts to parse the message content as a
    `HumanCloneTrigger`. If parsing fails due to a `ValueError` or
    `pydantic.ValidationError`, it signifies a malformed payload.

    Args:
        envelope: The `MessageEnvelope` to validate.

    Returns:
        A tuple `(is_valid, response_envelope)`.
        - `is_valid`: A boolean that is `True` if the envelope is valid or not
          from the supervisor, and `False` if validation fails.
        - `response_envelope`: If validation fails (`is_valid` is `False`), this
          contains a new `MessageEnvelope` with a detailed error message,
          intended to be sent back to the supervisor. If validation succeeds,
          this is `None`.
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


# Backward-compatible alias
validate_human_clone_envelope = validate_supervisor_envelope
