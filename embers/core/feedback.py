"""Outcome feedback. Agent-reported. Ember never infers or auto-acts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import uuid
import json
import math


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


class FeedbackChannel(str, Enum):
    RELEVANCE = "relevance"
    CORRECTNESS = "correctness"


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

    # Version 2 is explicit, report-only input. It does not authorize learning.
    schema_version: int = 1
    channel: FeedbackChannel | None = None
    outcome_id: str | None = None
    context_id: str | None = None
    context: dict | None = None
    signal: float | None = None
    supporting_refs: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in (1, 2):
            raise ValueError("unsupported feedback schema_version")
        if self.schema_version == 1:
            if any(x is not None for x in (
                self.channel, self.outcome_id, self.context_id, self.context,
                self.signal,
            )) or self.supporting_refs:
                raise ValueError("explicit feedback fields require schema_version=2")
            return
        for name in ("memory_id", "agent_id", "outcome_id", "context_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a nonempty string")
        try:
            channel = FeedbackChannel(self.channel)
            outcome = FeedbackOutcome(self.outcome)
        except (TypeError, ValueError) as exc:
            raise ValueError("valid channel and outcome required") from exc
        if not isinstance(self.context, dict):
            raise ValueError("context must be an object")
        try:
            json.dumps(self.context, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("context must contain finite JSON data") from exc
        if self.usefulness is not None or self.accuracy is not None:
            raise ValueError("v2 forbids legacy usefulness/accuracy; use explicit channel")
        if not isinstance(self.supporting_refs, list) or any(
            not isinstance(ref, str) or not ref.strip() for ref in self.supporting_refs
        ):
            raise ValueError("supporting_refs must be a list of nonempty strings")
        if channel == FeedbackChannel.RELEVANCE:
            if outcome not in (
                FeedbackOutcome.USEFUL, FeedbackOutcome.IRRELEVANT,
                FeedbackOutcome.MISLEADING,
            ):
                raise ValueError("relevance requires useful, irrelevant or misleading")
            if type(self.signal) not in (int, float) or not math.isfinite(self.signal):
                raise ValueError("relevance signal must be finite")
            if not -1 <= self.signal <= 1:
                raise ValueError("relevance signal must be within [-1, 1]")
            if outcome == FeedbackOutcome.USEFUL and self.signal <= 0:
                raise ValueError("useful requires positive signal")
            if outcome != FeedbackOutcome.USEFUL and self.signal > 0:
                raise ValueError("irrelevant/misleading cannot have positive signal")
        else:
            if outcome not in (
                FeedbackOutcome.CORRECT, FeedbackOutcome.INCORRECT,
                FeedbackOutcome.CONFIRMED, FeedbackOutcome.CONTRADICTED,
            ):
                raise ValueError("correctness requires an explicit correctness outcome")
            if self.signal is not None:
                raise ValueError("correctness cannot carry a relevance signal")
            if not self.supporting_refs:
                raise ValueError("correctness requires supporting_refs; references are not verification")

    def to_dict(self) -> dict:
        self.validate()
        result = {
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

        if self.schema_version == 2:
            result.update(
                schema_version=2,
                channel=FeedbackChannel(self.channel).value,
                outcome_id=self.outcome_id,
                context_id=self.context_id,
                context=self.context,
                signal=self.signal,
                supporting_refs=list(self.supporting_refs),
            )
        return result

    @classmethod
    def from_submission(cls, memory_id: str, agent_id: str, body: dict,
                        session_id: str | None = None) -> "Feedback":
        """Use authenticated caller identity; never accept body attribution as auth."""
        version = body.get("schema_version", 1)
        if "memory_id" in body and body["memory_id"] != memory_id:
            raise ValueError("body memory_id must match the target")
        if version == 2:
            allowed = {
                "memory_id", "agent_id", "token", "session_id", "outcome",
                "schema_version", "channel", "outcome_id", "context_id",
                "context", "signal", "supporting_refs", "note", "attribution",
                "usefulness", "accuracy",
            }
            unknown = set(body) - allowed
            if unknown:
                raise ValueError(f"unsupported v2 fields: {sorted(unknown)}")
        fb = cls(
            memory_id=memory_id, agent_id=agent_id,
            outcome=FeedbackOutcome(body.get("outcome")),
            usefulness=body.get("usefulness"), accuracy=body.get("accuracy"),
            attribution=(FeedbackAttribution(body["attribution"])
                         if body.get("attribution") else None),
            note=body.get("note", ""), session_id=session_id,
            schema_version=version, channel=body.get("channel"),
            outcome_id=body.get("outcome_id"), context_id=body.get("context_id"),
            context=body.get("context"), signal=body.get("signal"),
            supporting_refs=body.get("supporting_refs", []),
        )
        fb.validate()
        return fb

    @classmethod
    def from_dict(cls, d: dict) -> "Feedback":
        ts = d.get("timestamp")
        return cls(
            schema_version=d.get("schema_version", 1),
            channel=d.get("channel"),
            outcome_id=d.get("outcome_id"), context_id=d.get("context_id"),
            context=d.get("context"), signal=d.get("signal"),
            supporting_refs=d.get("supporting_refs", []),
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
