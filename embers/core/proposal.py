"""
Ember's Diaries — Memory Proposal (Feature #4: Discovery Before Memory)

An agent should not leap straight from `research → write memory`. The spec
inserts a staging step:

    research → discovery → evidence → reasoning → PROPOSAL → validation → memory

A `MemoryProposal` is that staging object. It bundles a discovery with the
structured justification for preserving it, so validation (human or automated)
can judge it BEFORE it becomes durable memory — and so a rejected proposal
stays on the record, distinguishable from anything that was committed.

A proposal carries (spec §4):

    discovery    — what the agent discovered (the payload of the future memory)
    reason       — why it is worth preserving (concise, structured; NOT
                   chain-of-thought)
    evidence     — list[Evidence]: what supports the discovery (§5)
    sources      — where the evidence came from (derived from the evidence, but
                   also settable directly for quick provenance)
    confidence   — how confident the agent is (0.0–1.0)
    derivation   — ids of prior memories/observations the conclusion was drawn
                   from (becomes the memory's `derived_from`)

CHAIN-OF-THOUGHT BOUNDARY (spec §4): `reason` is a short structured statement
("Two independent tests produced the same result."), never a private reasoning
transcript. The proposal preserves enough to understand HOW a conclusion was
reached — evidence + sources + confidence + derivation — without storing hidden
thought.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
import uuid

from .types import RecordType, ProposalStatus
from .evidence import Evidence
from .integrity import content_hash


@dataclass
class MemoryProposal:
    """A discovery awaiting validation. Not yet a durable memory."""

    # ── Identity ───────────────────────────────────────────────────────────────
    proposal_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    namespace: str = "default"

    # ── The discovery (spec §4) ────────────────────────────────────────────────
    discovery: Any = None                              # the future memory's data
    reason: str = ""                                   # WHY preserve it (structured)
    evidence: list = field(default_factory=list)       # list[Evidence]
    sources: list = field(default_factory=list)        # where evidence came from
    confidence: float = 0.5                            # 0.0 – 1.0
    derivation: list = field(default_factory=list)     # prior memory/observation ids

    # ── Authorship ─────────────────────────────────────────────────────────────
    written_by: str = "system"
    agent_id: str | None = None
    session_id: str | None = None
    created_at: datetime = field(default_factory=datetime.utcnow)

    # ── Lifecycle ──────────────────────────────────────────────────────────────
    status: ProposalStatus = ProposalStatus.PENDING
    tags: list = field(default_factory=list)

    def __post_init__(self):
        # A caller may pass evidence as dicts (e.g. from a wire payload); coerce
        # to Evidence so the proposal always holds real, sealable objects.
        self.evidence = [
            e if isinstance(e, Evidence) else Evidence.from_dict(e)
            for e in self.evidence
        ]
        # If sources weren't given explicitly, derive them from the evidence so
        # "where did this come from?" is answerable straight from a proposal.
        if not self.sources and self.evidence:
            seen, derived = set(), []
            for e in self.evidence:
                if e.source and e.source not in seen:
                    seen.add(e.source)
                    derived.append(e.source)
            self.sources = derived

    def add_evidence(self, ev: Evidence) -> "MemoryProposal":
        """Attach a piece of evidence (sealing it if it isn't already)."""
        if ev.content_hash is None:
            ev.seal()
        self.evidence.append(ev)
        if ev.source and ev.source not in self.sources:
            self.sources.append(ev.source)
        return self

    def seal_evidence(self) -> None:
        """Seal every attached evidence object (idempotent)."""
        for e in self.evidence:
            if e.content_hash is None:
                e.seal()

    def is_grounded(self) -> bool:
        """True if the proposal rests on at least one piece of evidence — i.e.
        it is a CLAIM → EVIDENCE chain, not a bare CLAIM → agent assertion."""
        return len(self.evidence) > 0

    def to_record_payload(self) -> dict:
        """The `data` payload stored on the PROPOSAL record.

        This is what makes a proposal a first-class, hashed, append-only object
        in its own right — the whole discovery-with-justification travels into
        the record's content hash (so evidence can't be silently swapped after
        the fact). On promotion, `discovery` becomes the durable memory's data;
        the rest becomes its provenance."""
        return {
            "proposal_id": self.proposal_id,
            "discovery": self.discovery,
            "reason": self.reason,
            "evidence": [e.to_dict() for e in self.evidence],
            "sources": list(self.sources),
            "confidence": self.confidence,
            "derivation": list(self.derivation),
            "status": self.status.value,
        }

    def content_fingerprint(self) -> str:
        """A stable hash of the proposal's substantive content (excludes the
        mutable `status`), useful for de-duplicating identical proposals."""
        payload = self.to_record_payload()
        payload.pop("status", None)
        return content_hash(payload)

    def to_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "namespace": self.namespace,
            "discovery": self.discovery,
            "reason": self.reason,
            "evidence": [e.to_dict() for e in self.evidence],
            "sources": list(self.sources),
            "confidence": self.confidence,
            "derivation": list(self.derivation),
            "written_by": self.written_by,
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "created_at": self.created_at.isoformat(),
            "status": self.status.value,
            "tags": list(self.tags),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryProposal":
        return cls(
            proposal_id = d["proposal_id"],
            namespace   = d.get("namespace", "default"),
            discovery   = d.get("discovery"),
            reason      = d.get("reason", ""),
            evidence    = [Evidence.from_dict(e) for e in d.get("evidence", [])],
            sources     = d.get("sources", []),
            confidence  = d.get("confidence", 0.5),
            derivation  = d.get("derivation", []),
            written_by  = d.get("written_by", "system"),
            agent_id    = d.get("agent_id"),
            session_id  = d.get("session_id"),
            created_at  = datetime.fromisoformat(d["created_at"]) if d.get("created_at") else datetime.utcnow(),
            status      = ProposalStatus(d.get("status", "pending")),
            tags        = d.get("tags", []),
        )
