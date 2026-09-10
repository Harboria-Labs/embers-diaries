"""
Ember's Diaries — Outcome Feedback

The missing half of the loop the architecture review identified:

    retrieve → agent reasons → agent acts → OUTCOME → feedback → memory

Ember has always been able to answer "what do I know" (recall) and "how did
this come to be known" (evidence, history). It could not previously answer
"did this actually help, and if not, why" — nothing recorded what happened
after a memory was used.

Feedback is deliberately NOT a mechanism for Ember to auto-modify a memory.
The architecture principle stands: the agent controls memory, Ember provides
infrastructure. A Feedback record is a structured signal an agent chooses to
write down; what happens next (deprecating a memory, mapping a conflict,
proposing a correction) is still a deliberate, separate act by an agent —
never an automatic side effect of writing feedback.

Two things are captured, matching the architecture review's §8/§9 split:

  outcome      — WHAT happened (useful, incorrect, stale, ...). Always
                 present.
  attribution  — WHY, when the outcome was negative. Optional, and always
                 agent-diagnosed: Ember has no mechanism to infer whether a
                 bad outcome was a retrieval failure, a wrong memory, a
                 reasoning error, or a stale fact — only the agent (or a
                 human) that saw the actual failure is in a position to
                 know that. Ember's job is to give that diagnosis somewhere
                 durable to live, not to guess it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import uuid


class FeedbackOutcome(str, Enum):
    """What happened after a memory was retrieved and used."""
    USEFUL       = "useful"
    IRRELEVANT   = "irrelevant"
    CORRECT      = "correct"
    INCORRECT    = "incorrect"
    CONFIRMED    = "confirmed"
    CONTRADICTED = "contradicted"
    STALE        = "stale"
    MISLEADING   = "misleading"
    SUCCESSFUL   = "successful"
    UNSUCCESSFUL = "unsuccessful"


class FeedbackAttribution(str, Enum):
    """Why an unsuccessful outcome happened. Always set by the reporting
    agent (or a human), never inferred by Ember."""
    RETRIEVAL_FAILURE = "retrieval_failure"  # the right memory existed but wasn't retrieved
    MEMORY_FAILURE    = "memory_failure"     # the retrieved memory was itself incorrect
    STALE_MEMORY      = "stale_memory"       # was correct once, no longer current
    REASONING_FAILURE = "reasoning_failure"  # correct memory, agent reasoned incorrectly
    CONTEXT_FAILURE   = "context_failure"    # correct memory, insufficient for the situation
    USER_CORRECTION   = "user_correction"    # the user explicitly corrected it


@dataclass
class Feedback:
    """One outcome report tied to a specific memory.

    usefulness/accuracy are optional 0.0-1.0 scores a caller can supply
    alongside the categorical `outcome` for finer-grained signal — neither
    is required, since the categorical outcome alone is enough to be
    useful, and forcing a numeric score on every report would push callers
    toward making up precision they don't have.
    """
    memory_id: str
    agent_id: str
    outcome: FeedbackOutcome
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    usefulness: float | None = None
    accuracy: float | None = None
    attribution: FeedbackAttribution | None = None
    note: str = ""
    session_id: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "id":          self.id,
            "memory_id":   self.memory_id,
            "agent_id":    self.agent_id,
            "outcome":     (self.outcome.value if isinstance(self.outcome, FeedbackOutcome)
                            else self.outcome),
            "usefulness":  self.usefulness,
            "accuracy":    self.accuracy,
            "attribution": (self.attribution.value
                            if isinstance(self.attribution, FeedbackAttribution)
                            else self.attribution),
            "note":        self.note,
            "session_id":  self.session_id,
            "timestamp":   self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Feedback":
        ts = d.get("timestamp")
        return cls(
            memory_id   = d["memory_id"],
            agent_id    = d["agent_id"],
            outcome     = FeedbackOutcome(d["outcome"]),
            id          = d.get("id", str(uuid.uuid4())),
            usefulness  = d.get("usefulness"),
            accuracy    = d.get("accuracy"),
            attribution = (FeedbackAttribution(d["attribution"])
                           if d.get("attribution") else None),
            note        = d.get("note", ""),
            session_id  = d.get("session_id"),
            timestamp   = datetime.fromisoformat(ts) if ts else datetime.now(timezone.utc),
        )
