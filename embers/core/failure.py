"""
Ember's Diaries — Failure (Feature #13: Failures Should Be First-Class Information)

A failure is not noise to be discarded — it is information other agents need.
The spec's §13 example is the whole feature in miniature:

    FAILED:   Approach X does not work with dataset Y.
    CAUSE:    Parser exceeds memory limit.
    EVIDENCE: test_run_8271

The problem it solves is repeated waste:

    Agent A tries X → fails
    Agent B unknowingly tries X → fails
    Agent C unknowingly tries X → fails

With failures first-class, Agent A's failure is visible, and Agent B checks
before it spends the same effort — it tries Y instead.

Which makes the FAILURE record's most important field `approach`: the reusable
key by which a *later* agent asks "has this already been tried and failed?"
`approach_fingerprint()` normalizes it (case, whitespace) so trivially different
phrasings of the same attempt still match.

A failure carries evidence for the same reason a proposal does (§5): "approach X
failed" backed by `test_run_8271` is a CLAIM → EVIDENCE chain; the same sentence
with nothing behind it is a bare assertion, and a later agent should be able to
tell the two apart before trusting it.

PROMOTION (§13's last line: "Failures can later be promoted into durable memory
if sufficiently valuable"). A failure is not automatically durable knowledge —
one crash is an event, a repeated structural limitation is a lesson. So a
failure is promoted the same way any other discovery is: it becomes a
MemoryProposal (carrying the failure's own evidence, derived from the failure
record) and goes through the Promotion Engine (§12). `promoted_to` records the
durable memory that resulted. Nothing is ever deleted along the way — the
failure record remains, whether or not it was ever promoted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import re
import uuid

from .evidence import Evidence
from .integrity import content_hash


@dataclass
class Failure:
    """A failed approach, recorded so other agents don't repeat it (spec §13)."""

    # ── Identity ───────────────────────────────────────────────────────────────
    failure_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    namespace: str = "default"

    # ── The §13 shape ──────────────────────────────────────────────────────────
    approach: str = ""      # WHAT was attempted — the lookup key for other agents
    failed: str = ""        # FAILED:   what did not work
    cause: str = ""         # CAUSE:    why it did not work
    evidence: list = field(default_factory=list)   # EVIDENCE: list[Evidence] (§5)

    # ── Attribution (provenance, #3 / sessions, #9) ─────────────────────────────
    agent_id: str = "system"
    session_id: str | None = None
    occurred_at: datetime = field(default_factory=datetime.utcnow)

    # ── Context & promotion ────────────────────────────────────────────────────
    context: dict = field(default_factory=dict)    # dataset, params, environment
    promoted_to: str | None = None                 # durable memory id, if promoted
    tags: list = field(default_factory=list)

    def __post_init__(self):
        # A caller may pass evidence as dicts (e.g. from a wire payload); coerce
        # to Evidence so the failure always holds real, sealable objects.
        self.evidence = [
            e if isinstance(e, Evidence) else Evidence.from_dict(e)
            for e in self.evidence
        ]
        # `approach` is the field other agents search on, so it must not be
        # empty — a failure nobody can look up cannot prevent a repeat.
        if not self.approach.strip():
            raise ValueError(
                "A failure needs an `approach` — it is the key other agents "
                "search on to avoid repeating it.")

    # ── The anti-repetition key ────────────────────────────────────────────────

    @staticmethod
    def normalize_approach(approach: str) -> str:
        """Normalize an approach string for matching: case-folded, punctuation-
        trimmed, whitespace-collapsed. So 'Approach X with dataset Y' and
        'approach x  with  dataset y.' are recognised as the same attempt."""
        text = approach.strip().lower()
        text = re.sub(r"\s+", " ", text)
        return text.strip(" .!?;:")

    def approach_fingerprint(self) -> str:
        """Stable hash of the NORMALIZED approach within a namespace — the key by
        which a later agent asks 'has this been tried and failed?'"""
        return content_hash({
            "namespace": self.namespace,
            "approach": self.normalize_approach(self.approach),
        })

    def is_grounded(self) -> bool:
        """True if the failure rests on at least one piece of evidence — i.e. it
        is a CLAIM → EVIDENCE chain ('X failed, see test_run_8271'), not a bare
        assertion. A later agent should weigh the two differently."""
        return len(self.evidence) > 0

    def seal_evidence(self) -> None:
        """Seal every attached evidence object (idempotent), so each piece keeps
        the identity/hash it will carry if the failure is later promoted."""
        for e in self.evidence:
            if e.content_hash is None:
                e.seal()

    # ── Serialization ──────────────────────────────────────────────────────────

    def summary(self) -> str:
        """The §13 human-readable form agents share with each other."""
        lines = [f"FAILED: {self.failed or self.approach}"]
        if self.cause:
            lines.append(f"CAUSE: {self.cause}")
        if self.evidence:
            refs = ", ".join(e.source or e.reference or e.evidence_id
                             for e in self.evidence)
            lines.append(f"EVIDENCE: {refs}")
        return "\n".join(lines)

    def to_record_payload(self) -> dict:
        """The `data` payload stored on the FAILURE record. The whole failure
        account — approach, cause, and its evidence — travels into the record's
        content hash, so it cannot be silently rewritten after the fact."""
        return {
            "failure_id": self.failure_id,
            "approach": self.approach,
            "approach_fingerprint": self.approach_fingerprint(),
            "failed": self.failed,
            "cause": self.cause,
            "evidence": [e.to_dict() for e in self.evidence],
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "occurred_at": self.occurred_at.isoformat(),
            "context": dict(self.context),
            "promoted_to": self.promoted_to,
        }

    def to_dict(self) -> dict:
        d = self.to_record_payload()
        d["namespace"] = self.namespace
        d["tags"] = list(self.tags)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Failure":
        return cls(
            failure_id  = d.get("failure_id", str(uuid.uuid4())),
            namespace   = d.get("namespace", "default"),
            approach    = d["approach"],
            failed      = d.get("failed", ""),
            cause       = d.get("cause", ""),
            evidence    = [Evidence.from_dict(e) for e in d.get("evidence", [])],
            agent_id    = d.get("agent_id", "system"),
            session_id  = d.get("session_id"),
            occurred_at = datetime.fromisoformat(d["occurred_at"]) if d.get("occurred_at") else datetime.utcnow(),
            context     = d.get("context", {}) or {},
            promoted_to = d.get("promoted_to"),
            tags        = d.get("tags", []),
        )
