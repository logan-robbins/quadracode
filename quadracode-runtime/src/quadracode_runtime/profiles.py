"""
This module is responsible for defining and loading the runtime profiles for the
different components of the Quadracode system, such as the orchestrator, agents,
and the HumanClone.

A `RuntimeProfile` is a dataclass that encapsulates the core configuration for a
runtime component, including its name, default identity, system prompt, and a
`RecipientPolicy` that governs its message routing behavior. This module provides
a set of specialized `RecipientPolicy` classes and factory functions for creating
the different profiles, allowing for a clear and centralized definition of each
component's role and behavior within the system.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from typing import Iterable, List, Mapping, Optional


def _generate_agent_id() -> str:
    """Generate a unique agent ID in the format 'agent-{short_uuid}'."""
    return f"agent-{secrets.token_hex(4)}"

from quadracode_contracts import (
    HUMAN_CLONE_RECIPIENT,
    HUMAN_RECIPIENT,
    ORCHESTRATOR_RECIPIENT,
    AutonomousRoutingDirective,
)

from .prompts import BASE_PROMPT
_AUTONOMOUS_MODE_VALUES = {"autonomous", "human_obsolete"}
_AUTONOMOUS_FLAG_VALUES = {"1", "true", "yes", "on"}
_AUTONOMOUS_ENV_VARS = (
    "QUADRACODE_MODE",
    "QUADRACODE_AUTONOMOUS_MODE",
    "HUMAN_OBSOLETE_MODE",
)


def _supervisor_recipient() -> str:
    """Return the configured supervisor identity (human or human_clone).

    Defaults to HUMAN_RECIPIENT but can be overridden via environment to
    make HumanClone stand in for the human without any special-mode logic.
    """

    value = os.environ.get("QUADRACODE_SUPERVISOR_RECIPIENT") or os.environ.get(
        "QUADRACODE_SUPERVISOR"
    )
    normalized = (value or "").strip().lower()
    if normalized in {HUMAN_CLONE_RECIPIENT, "human_clone"}:
        return HUMAN_CLONE_RECIPIENT
    # Fallback to actual human by default
    return HUMAN_RECIPIENT


def is_autonomous_mode_enabled() -> bool:
    """
    Checks environment variables to determine if the system is running in 
    autonomous ("HUMAN_OBSOLETE") mode.
    """

    for env_var in _AUTONOMOUS_ENV_VARS:
        value = os.environ.get(env_var)
        if value is None:
            continue
        normalized = value.strip().lower()
        if env_var == "QUADRACODE_MODE" and normalized in _AUTONOMOUS_MODE_VALUES:
            return True
        if normalized in _AUTONOMOUS_FLAG_VALUES:
            return True

    return False


def _extract_autonomous_directive(
    payload: Mapping[str, object] | None,
) -> Optional[AutonomousRoutingDirective]:
    if payload is None:
        return None
    directive_payload = payload.get("autonomous")
    return AutonomousRoutingDirective.from_payload(directive_payload)


@dataclass(frozen=True)
class RecipientPolicy:
    """
    Defines the rules for resolving the recipients of a message.

    This is the base class for all recipient policies. It provides a default 
    `resolve` method that can be customized by subclasses to implement more 
    specialized routing logic.

    Attributes:
        fallback: The default recipient if no other recipients can be resolved.
        force: A tuple of recipients that should always be included.
        include_sender: Whether to include the sender as a recipient if no other 
                        recipients are specified.
        mirror_human_when_sender_not: Whether to automatically include the human as 
                                    a recipient if the sender is not the human.
    """
    fallback: str | None = None
    force: tuple[str, ...] = ()
    include_sender: bool = True
    mirror_human_when_sender_not: bool = False

    def resolve(self, envelope, payload) -> List[str]:
        recipients: List[str] = []

        replies = payload.get("reply_to")
        if isinstance(replies, str):
            recipients.append(replies)
        elif isinstance(replies, Iterable):
            recipients.extend(
                [r for r in replies if isinstance(r, str) and r]
            )

        if not recipients and self.include_sender:
            sender = envelope.sender
            if isinstance(sender, str) and sender:
                recipients.append(sender)

        if not recipients and self.fallback:
            recipients.append(self.fallback)

        # Deduplicate while preserving order
        seen = set()
        recipients = [r for r in recipients if not (r in seen or seen.add(r))]

        for forced in self.force:
            if forced not in recipients:
                recipients.append(forced)

        if (
            self.mirror_human_when_sender_not
            and envelope.sender != HUMAN_RECIPIENT
            and HUMAN_RECIPIENT not in recipients
        ):
            recipients.append(HUMAN_RECIPIENT)

        return recipients


@dataclass(frozen=True)
class OrchestratorRecipientPolicy(RecipientPolicy):
    """
    A specialized recipient policy for the orchestrator.

    This policy ensures that the orchestrator's responses are correctly routed 
    to either the intended agent(s) or back to the human, depending on the 
    context of the message.
    """

    def resolve(self, envelope, payload) -> List[str]:  # type: ignore[override]
        recipients = super().resolve(envelope, payload)

        # If a reply path is declared, route exclusively to those targets first.
        if payload.get("reply_to"):
            recipients = [r for r in recipients if r != HUMAN_RECIPIENT]

        # Always loop the human back in once non-human work is complete.
        if envelope.sender != HUMAN_RECIPIENT and HUMAN_RECIPIENT not in recipients:
            recipients.append(HUMAN_RECIPIENT)

        return recipients


@dataclass(frozen=True)
class AutonomousRecipientPolicy(RecipientPolicy):
    """
    A specialized recipient policy for the orchestrator when running in 
    autonomous mode.

    This policy uses the `AutonomousRoutingDirective` to determine whether to 
    include the human in the list of recipients. It is designed to keep the 
    communication within the autonomous loop unless an explicit notification or 
    escalation is required.
    """

    def resolve(self, envelope, payload) -> List[str]:  # type: ignore[override]
        recipients = super().resolve(envelope, payload)
        directive = _extract_autonomous_directive(payload)
        notify_human = bool(directive and directive.deliver_to_human)
        escalate = bool(directive and directive.escalate)
        include_human = notify_human or escalate

        non_human: List[str] = [r for r in recipients if r != HUMAN_RECIPIENT]

        if non_human:
            recipients = non_human
            if include_human:
                recipients.append(HUMAN_RECIPIENT)
        elif include_human:
            if HUMAN_RECIPIENT not in recipients:
                recipients.append(HUMAN_RECIPIENT)
        else:
            # No non-human recipients and no explicit need to contact human.
            recipients = [r for r in recipients if r != HUMAN_RECIPIENT]
            if not recipients:
                # Preserve fallback to human to avoid message loss.
                recipients = [HUMAN_RECIPIENT]

        seen: set[str] = set()
        deduped: List[str] = []
        for recipient in recipients:
            if recipient not in seen:
                deduped.append(recipient)
                seen.add(recipient)

        return deduped


@dataclass(frozen=True)
class AgentRecipientPolicy(RecipientPolicy):
    """
    A specialized recipient policy for agents.

    This policy ensures that agents always respond to the orchestrator and never 
    route messages directly to the human.
    """

    def resolve(self, envelope, payload) -> List[str]:  # type: ignore[override]
        recipients = super().resolve(envelope, payload)
        # Explicitly remove human recipient from agent responses
        recipients = [r for r in recipients if r != HUMAN_RECIPIENT]
        # Ensure orchestrator is always included
        if ORCHESTRATOR_RECIPIENT not in recipients:
            recipients.append(ORCHESTRATOR_RECIPIENT)
        return recipients

@dataclass(frozen=True)
class HumanCloneRecipientPolicy(RecipientPolicy):
    """
    A specialized recipient policy for the HumanClone.

    This policy ensures that the HumanClone's responses are always routed back 
    to the orchestrator.
    """

    def resolve(self, envelope, payload) -> List[str]:  # type: ignore[override]
        return [ORCHESTRATOR_RECIPIENT]

@dataclass(frozen=True)
class RuntimeProfile:
    """
    Encapsulates the core configuration for a runtime component.

    Attributes:
        name: The name of the profile.
        default_identity: The default identity of the component.
        system_prompt: The base system prompt for the component.
        policy: The `RecipientPolicy` to be used for message routing.
    """
    name: str
    default_identity: str
    system_prompt: str = BASE_PROMPT
    policy: RecipientPolicy = field(default_factory=RecipientPolicy)

    def resolve_recipients(self, envelope, payload) -> List[str]:
        """
        Resolves the recipients for a message using the profile's recipient 
        policy.
        """
        return self.policy.resolve(envelope, payload)


def _make_orchestrator_profile() -> RuntimeProfile:
    """
    Constructs the appropriate orchestrator profile based on the current mode 
    (autonomous or not).
    """

    if is_autonomous_mode_enabled():
        policy: RecipientPolicy = AutonomousRecipientPolicy(
            fallback=_supervisor_recipient(),
            include_sender=False,
        )
    else:
        policy = OrchestratorRecipientPolicy(
            fallback=_supervisor_recipient(),
            include_sender=False,
        )

    return RuntimeProfile(
        name="orchestrator",
        default_identity=ORCHESTRATOR_RECIPIENT,
        system_prompt=BASE_PROMPT,
        policy=policy,
    )


def _make_agent_profile() -> RuntimeProfile:
    """Constructs the default agent profile with a unique ephemeral ID."""
    return RuntimeProfile(
        name="agent",
        default_identity=_generate_agent_id(),
        system_prompt=BASE_PROMPT,
        policy=AgentRecipientPolicy(
            fallback=ORCHESTRATOR_RECIPIENT,
            include_sender=True,
            force=(ORCHESTRATOR_RECIPIENT,),
        ),
    )

HUMAN_CLONE_PROFILE_CACHE: RuntimeProfile | None = None

HUMAN_CLONE_PROFILE_CACHE: RuntimeProfile | None = None


def _make_human_clone_profile() -> RuntimeProfile:
    """Constructs the HumanClone profile."""
    try:
        from quadracode_orchestrator.prompts.human_clone import (
            HUMAN_CLONE_SYSTEM_PROMPT,
        )
    except ImportError as exc:  # pragma: no cover - orchestrator optional
        raise RuntimeError(
            "HumanClone profile requires quadracode_orchestrator package to be installed"
        ) from exc
    return RuntimeProfile(
        name="human_clone",
        default_identity=HUMAN_CLONE_RECIPIENT,
        system_prompt=HUMAN_CLONE_SYSTEM_PROMPT,
        policy=HumanCloneRecipientPolicy(),
    )


def get_human_clone_profile() -> RuntimeProfile:
    """
    Returns a cached instance of the HumanClone profile.
    """
    global HUMAN_CLONE_PROFILE_CACHE
    if HUMAN_CLONE_PROFILE_CACHE is None:
        HUMAN_CLONE_PROFILE_CACHE = _make_human_clone_profile()
    return HUMAN_CLONE_PROFILE_CACHE


ORCHESTRATOR_PROFILE = _make_orchestrator_profile()
AGENT_PROFILE = _make_agent_profile()


def load_profile(name: str) -> RuntimeProfile:
    """
    Loads a `RuntimeProfile` by name.

    This function is the main entry point for retrieving a profile. It acts as a 
    factory, returning the appropriate profile based on the provided name.

    Args:
        name: The name of the profile to load.

    Returns:
        The requested `RuntimeProfile`.

    Raises:
        ValueError: If the requested profile is not found.
    """
    if name == "orchestrator":
        return _make_orchestrator_profile()
    if name == "agent":
        return _make_agent_profile()
    if name == "human_clone":
        return get_human_clone_profile()
    raise ValueError(f"Unknown runtime profile: {name}")
