from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Iterable, List, Mapping, Optional

from quadracode_contracts import (
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


def is_autonomous_mode_enabled() -> bool:
    """Return True if HUMAN_OBSOLETE autonomous mode is enabled."""

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
    """Recipient resolution tuned for orchestrator delegation requirements."""

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
    """Recipient resolution for HUMAN_OBSOLETE autonomous orchestrator mode."""

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
    """Recipient resolution for agents: never route directly to human."""

    def resolve(self, envelope, payload) -> List[str]:  # type: ignore[override]
        recipients = super().resolve(envelope, payload)
        # Explicitly remove human recipient from agent responses
        recipients = [r for r in recipients if r != HUMAN_RECIPIENT]
        # Ensure orchestrator is always included
        if ORCHESTRATOR_RECIPIENT not in recipients:
            recipients.append(ORCHESTRATOR_RECIPIENT)
        return recipients

@dataclass(frozen=True)
class RuntimeProfile:
    name: str
    default_identity: str
    system_prompt: str = BASE_PROMPT
    policy: RecipientPolicy = field(default_factory=RecipientPolicy)

    def resolve_recipients(self, envelope, payload) -> List[str]:
        return self.policy.resolve(envelope, payload)


def _make_orchestrator_profile() -> RuntimeProfile:
    """Construct an orchestrator profile using the current mode configuration."""

    if is_autonomous_mode_enabled():
        policy: RecipientPolicy = AutonomousRecipientPolicy(
            fallback=HUMAN_RECIPIENT,
            include_sender=False,
        )
    else:
        policy = OrchestratorRecipientPolicy(
            fallback=HUMAN_RECIPIENT,
            include_sender=False,
        )

    return RuntimeProfile(
        name="orchestrator",
        default_identity=ORCHESTRATOR_RECIPIENT,
        system_prompt=BASE_PROMPT,
        policy=policy,
    )


def _make_agent_profile() -> RuntimeProfile:
    return RuntimeProfile(
        name="agent",
        default_identity="agent",
        system_prompt=BASE_PROMPT,
        policy=AgentRecipientPolicy(
            fallback=ORCHESTRATOR_RECIPIENT,
            include_sender=True,
            force=(ORCHESTRATOR_RECIPIENT,),
        ),
    )


ORCHESTRATOR_PROFILE = _make_orchestrator_profile()
AGENT_PROFILE = _make_agent_profile()


def load_profile(name: str) -> RuntimeProfile:
    if name == "orchestrator":
        return _make_orchestrator_profile()
    if name == "agent":
        return _make_agent_profile()
    raise ValueError(f"Unknown runtime profile: {name}")
