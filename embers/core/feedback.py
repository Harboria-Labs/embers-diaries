"""Outcome feedback. Agent-reported. Ember never infers or auto-acts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import uuid


class FeedbackOutcome(str, Enum):
    USEFUL = "useful"
    IRRELEVANT = "irrelevant"
    CORRECT = "correct"
    INCORRECT = "incorrect"
    CONFIRMED = "confirmed"
    CONTRADICTED = "contradicted"
    STALE = "stale"
    MISLEADING = "misleading"
    SUCCESSFUL = "successful"
    UNSUCCESSFUL = "unsuccessful"


class FeedbackAttribution(str, Enum):
    RETRIEVAL_FAILURE = "retrieval_failure"
    MEMORY_FAILURE = "memory_failure"
    STALE_MEMORY = "stale_memory"
    REASONING_FAILURE = "reasoning_failure"
    CONTEXT_FAILURE = "context_failure"
    USER_CORRECTION = "user_correction"


@dataclass
class Feedback:
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
            "id": self.id,
            "memory_id": self.memory_id,
            "agent_id": self.agent_id,
            "outcome": self.outcome.value if isinstance(self.outcome, FeedbackOutcome) else self.outcome,
            "usefulness": self.usefulness,
            "accuracy": self.accuracy,
            "attribution": (self.attribution.value
                            if isinstance(self.attribution, FeedbackAttribution)
                            else self.attribution),
            "note": self.note,
            "session_id": self.session_id,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Feedback":
        ts = d.get("timestamp")
        return cls(
            memory_id=d["memory_id"],
            agent_id=d["agent_id"],
            outcome=FeedbackOutcome(d["outcome"]),
            id=d.get("id", str(uuid.uuid4())),
            usefulness=d.get("usefulness"),
            accuracy=d.get("accuracy"),
            attribution=(FeedbackAttribution(d["attribution"])
                         if d.get("attribution") else None),
            note=d.get("note", ""),
            session_id=d.get("session_id"),
            timestamp=datetime.fromisoformat(ts) if ts else datetime.now(timezone.utc),
        )
