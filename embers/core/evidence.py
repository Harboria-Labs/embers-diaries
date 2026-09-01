"""
Ember's Diaries — Evidence (Feature #5: Evidence Integrity)

Evidence attached to a memory has its OWN identity. This is the difference
between the two chains the spec insists we be able to tell apart:

    CLAIM → EVIDENCE → SOURCE       (grounded — traceable to something observed)
    CLAIM → agent assertion         (an agent simply says so)

A bare assertion carries no Evidence; a grounded claim carries one or more
Evidence objects, each naming WHERE it came from (`source`), HOW it was obtained
(`source_type`), WHEN it was observed, and WHO/which session observed it — plus
its own `content_hash` so the evidence itself is tamper-evident and can be
de-duplicated and cited by hash.

Evidence is deliberately NOT free-form text. It is a structured type so that a
future agent can answer "was this directly observed, or merely reported by
another agent?" without parsing prose (spec §5).

Note on chain-of-thought (spec §4): evidence records the OBSERVABLE support for
a claim — a tool result, a document, a measurement — never the agent's private
reasoning. The `reference` is a pointer to the artifact, not a transcript.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import uuid

from .types import SourceType
from .integrity import content_hash


@dataclass
class Evidence:
    """A single, identity-bearing piece of support for a claim.

    Fields mirror spec §5 exactly (evidence_id, source, source_type,
    observed_at, content_hash, agent_id, session_id) plus two practical
    additions: `reference` (a pointer — URI, tool-call id, file path — to the
    underlying artifact) and `description` (a concise human label). The
    `content_hash` is computed over the immutable fields and lets identical
    evidence be recognized across agents and sessions.
    """

    # ── Identity ───────────────────────────────────────────────────────────────
    evidence_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # ── What it is ───────────────────────────────────────────────────────────
    source: str = ""                                   # WHERE it came from
    source_type: SourceType = SourceType.DIRECTLY_OBSERVED  # HOW it was obtained
    reference: str = ""                                # pointer to the artifact
    description: str = ""                              # concise human label

    # ── When / who ───────────────────────────────────────────────────────────
    observed_at: datetime = field(default_factory=datetime.utcnow)
    agent_id: str | None = None
    session_id: str | None = None

    # ── Integrity ──────────────────────────────────────────────────────────────
    content_hash: str | None = None                    # set by seal()

    def canonical_hash_payload(self) -> dict:
        """The immutable fields that define this evidence's identity.

        `observed_at`, `source`, `source_type` and `reference` are the facts of
        the observation and cannot change once sealed. `agent_id`/`session_id`
        are included when present so two agents observing the "same" thing
        produce DISTINCT evidence (each observation is its own act). They are
        omitted when unset so evidence carrying no attribution hashes stably.
        """
        payload = {
            "evidence_id": self.evidence_id,
            "source": self.source,
            "source_type": self.source_type.value,
            "reference": self.reference,
            "description": self.description,
            "observed_at": self.observed_at.isoformat(),
        }
        if self.agent_id is not None:
            payload["agent_id"] = self.agent_id
        if self.session_id is not None:
            payload["session_id"] = self.session_id
        return payload

    def compute_content_hash(self) -> str:
        return content_hash(self.canonical_hash_payload())

    def seal(self) -> str:
        """Compute and attach the immutable content identity.

        Idempotent, and refuses to re-seal over a mismatch — the same guard
        EmberRecord.seal() uses, so tampering after sealing is caught."""
        computed = self.compute_content_hash()
        if self.content_hash is not None and self.content_hash != computed:
            raise ValueError(
                f"Evidence {self.evidence_id} content does not match its hash")
        self.content_hash = computed
        return computed

    def verify_integrity(self) -> bool:
        """True iff the sealed hash still matches the current content."""
        if self.content_hash is None:
            return False
        return self.compute_content_hash() == self.content_hash

    def to_dict(self) -> dict:
        return {
            "evidence_id": self.evidence_id,
            "source": self.source,
            "source_type": self.source_type.value,
            "reference": self.reference,
            "description": self.description,
            "observed_at": self.observed_at.isoformat(),
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "content_hash": self.content_hash,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Evidence":
        return cls(
            evidence_id  = d["evidence_id"],
            source       = d.get("source", ""),
            source_type  = SourceType(d.get("source_type", "directly_observed")),
            reference    = d.get("reference", ""),
            description  = d.get("description", ""),
            observed_at  = datetime.fromisoformat(d["observed_at"]),
            agent_id     = d.get("agent_id"),
            session_id   = d.get("session_id"),
            content_hash = d.get("content_hash"),
        )
