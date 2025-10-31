from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List

from quadracode_contracts import HUMAN_RECIPIENT, ORCHESTRATOR_RECIPIENT

from .prompts import BASE_PROMPT


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


ORCHESTRATOR_PROFILE = RuntimeProfile(
    name="orchestrator",
    default_identity=ORCHESTRATOR_RECIPIENT,
    policy=OrchestratorRecipientPolicy(
        fallback=HUMAN_RECIPIENT,
        include_sender=False,
    ),
)

AGENT_PROFILE = RuntimeProfile(
    name="agent",
    default_identity="agent",
    policy=AgentRecipientPolicy(
        fallback=ORCHESTRATOR_RECIPIENT,
        include_sender=True,
        force=(ORCHESTRATOR_RECIPIENT,),
    ),
)


def load_profile(name: str) -> RuntimeProfile:
    if name == "orchestrator":
        return ORCHESTRATOR_PROFILE
    if name == "agent":
        return AGENT_PROFILE
    raise ValueError(f"Unknown runtime profile: {name}")
