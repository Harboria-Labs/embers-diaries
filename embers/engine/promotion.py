"""
Ember's Diaries — Promotion Engine (spec §10–§12)

The buffer between an agent's raw discovery and durable memory:

    Agent discovers X
        → Lobby (a discovery is shared)
        → Proposal (propose(): a hashed PROPOSAL record, §4/§5)
        → PROMOTION ENGINE  ← this module
        → {AUTOMATIC | CONSENSUS | HUMAN | HYBRID}
        → Ember durable memory

`propose` / `promote` / `reject` (Features #4/#5) are the *mechanism*. This
engine is the *policy* in front of them: given a pending proposal, it DECIDES
whether — and by which method — the proposal should become a durable memory.
It does not decide epistemic truth. It never writes on its own; it calls back into the
existing `EmberDB.promote()` so all the append-only / hashing / evidence-edge
guarantees still hold.

THE ONE SEMANTIC THAT MUST NOT BE LOST (the user's words):

    "Promotion doesn't mean 'this is definitely true.' It means: 'This proposal
     met the criteria for entering durable memory.'"

Promotion is therefore an admission decision only. Every promoted memory enters
the ELLA era with a PROVISIONAL canonical epistemic state unless and until accepted
claim-specific evidence assessments move it. Proposal confidence remains an
admission/risk signal and never becomes an ELLA likelihood contribution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..core.types import (
    PromotionMode, PromotionMethod, MemoryStatus, ProposalStatus,
)


class PromotionOutcome(str, Enum):
    """What the engine decided to do with a proposal — the routing verdict.

    PROMOTE: admit it to durable memory now. HOLD: do nothing (leave it PENDING)
    — it did not meet the criteria yet, e.g. awaiting more evidence or a human.
    REJECT: the engine actively judged it unfit (reserved; the current policies
    HOLD rather than auto-reject, since a hold is append-only and reversible
    while a reject is a recorded terminal verdict)."""
    PROMOTE = "promote"
    HOLD    = "hold"
    REJECT  = "reject"


@dataclass
class PromotionPolicy:
    """The tunable gates the engine applies. Deliberately small and explicit;
    every field maps to one of the user's automatic-mode criteria.

        min_confidence      confidence a proposal needs to be admitted at all
        verified_confidence deprecated compatibility setting; retained so old
                            configuration still parses, but ignored for truth
        require_evidence    an ungrounded proposal (no Evidence) cannot
                            auto-promote — a bare assertion is not enough
        minimum_evidence_items number of evidence items needed when evidence
                            is required
        consensus_threshold distinct corroborating agents needed in CONSENSUS
        trusted_agents      None ⇒ trust every agent; otherwise a proposal's
                            agent_id must be in this set to auto-promote
        risk_confidence     HYBRID: at/below this a proposal is "high-risk" and
                            is routed to the human gate instead of auto-promoted
    """
    min_confidence: float = 0.7
    verified_confidence: float = 0.85  # Deprecated compatibility input; ignored for admission/truth.
    require_evidence: bool = True
    minimum_evidence_items: int = 1
    consensus_threshold: int = 2
    trusted_agents: set | None = None
    risk_confidence: float = 0.5

    def is_trusted(self, agent_id: str | None) -> bool:
        """A proposal is trusted if no allow-list is configured, or its agent is
        on it. An unattributed proposal (agent_id None) is trusted only when no
        allow-list is set — an allow-list means 'only these named agents'."""
        if self.trusted_agents is None:
            return True
        return agent_id in self.trusted_agents


@dataclass
class PromotionDecision:
    """The engine's verdict for a proposal — a PURE decision, no side effects.

    `route()` returns this without writing anything, so it doubles as a dry-run
    ('what would happen if I submitted this?'). `submit()` computes the same
    decision and then acts on it."""
    proposal_id: str
    outcome: PromotionOutcome
    mode: PromotionMode
    method: PromotionMethod | None = None   # how it would be promoted (if PROMOTE)
    status: MemoryStatus | None = None      # deprecated response slot; ELLA owns truth
    reasons: list = field(default_factory=list)  # human-readable gate results

    @property
    def will_promote(self) -> bool:
        return self.outcome == PromotionOutcome.PROMOTE

    def to_dict(self) -> dict:
        return {
            "proposal_id": self.proposal_id,
            "outcome": self.outcome.value,
            "mode": self.mode.value,
            "method": self.method.value if self.method else None,
            "status": self.status.value if self.status else None,
            "reasons": list(self.reasons),
        }


@dataclass
class PromotionResult:
    """The outcome of an actual `submit()` — the decision plus what it wrote.

    `memory_id` is set only when the proposal was promoted; on a HOLD it is None
    and the proposal remains PENDING (nothing was written — append-only means a
    non-decision leaves no trace)."""
    decision: PromotionDecision
    memory_id: str | None = None

    @property
    def promoted(self) -> bool:
        return self.memory_id is not None


class PromotionEngine:
    """Routes pending proposals to durable memory according to a configured mode.

    Holds no state of its own beyond the mode + policy; it reads proposals and
    their evidence through the owning EmberDB and calls back into
    `db.promote()`. Swapping the mode swaps the decision procedure with no change
    to storage semantics."""

    def __init__(self, db, mode: PromotionMode = PromotionMode.AUTOMATIC,
                 policy: PromotionPolicy | None = None):
        self._db = db
        self.mode = mode
        self.policy = policy or PromotionPolicy()

    # ── Decision (pure) ────────────────────────────────────────────────────────

    def route(self, proposal_id: str, actor_id: str | None = None) -> PromotionDecision:
        """Decide what would happen to this proposal WITHOUT writing anything.

        Dispatches on the configured mode. Raises KeyError if the proposal does
        not exist; a proposal that is not currently PENDING always HOLDs (only a
        pending proposal is a candidate for promotion)."""
        with self._db._store.lock:
            return self._route_locked(proposal_id, actor_id)

    def _route_locked(self, proposal_id: str,
                      actor_id: str | None) -> PromotionDecision:
        proposal = self._db.get_proposal(proposal_id)
        if proposal is None:
            raise KeyError(f"Proposal {proposal_id} not found.")
        if actor_id is not None:
            self._db.require_namespace_access(proposal.namespace, actor_id, "read")

        if proposal.status != ProposalStatus.PENDING:
            return PromotionDecision(
                proposal_id, PromotionOutcome.HOLD, self.mode,
                reasons=[f"proposal is {proposal.status.value}, not pending"])

        if self.mode == PromotionMode.HUMAN:
            return self._route_human(proposal, actor_id)
        if self.mode == PromotionMode.CONSENSUS:
            return self._route_consensus(proposal, actor_id)
        if self.mode == PromotionMode.HYBRID:
            return self._route_hybrid(proposal, actor_id)
        return self._route_automatic(proposal, actor_id)

    # ── Action ───────────────────────────────────────────────────────────────

    def submit(self, proposal_id: str, validated_by: str = "promotion-engine"
               ) -> PromotionResult:
        """Route the proposal and act on the decision.

        On PROMOTE, calls `db.promote()` with the decided admission method and
        returns the new memory id. On HOLD, writes nothing and leaves the
        proposal PENDING (it can be submitted again later once, e.g., more
        evidence has accumulated)."""
        with self._db._store.lock:
            proposal = self._db.get_proposal(proposal_id)
            if proposal is None:
                raise KeyError(f"Proposal {proposal_id} not found.")
            self._db.require_namespace_access(
                proposal.namespace, validated_by, "write")
            decision = self._route_locked(proposal_id, validated_by)
            if not decision.will_promote:
                return PromotionResult(decision, memory_id=None)
            memory_id, _ = self._db.promote(
                proposal_id, validated_by=validated_by,
                promotion_method=decision.method)
            return PromotionResult(decision, memory_id=memory_id)

    # ── Mode policies ──────────────────────────────────────────────────────────

    def _automatic_gates(self, proposal, actor_id=None) -> list[tuple[bool, str]]:
        """The AUTOMATIC-mode criteria, each as (passed, explanation). Shared by
        automatic and hybrid so the gate logic lives in exactly one place."""
        p = self.policy
        conflict_issues = self.conflict_hold_reasons(proposal, actor_id)
        evidence_count = len(proposal.evidence)
        evidence_reason = (
            f"{evidence_count} evidence item(s), need {p.minimum_evidence_items}"
            if evidence_count else
            f"no evidence (need {p.minimum_evidence_items} item(s))"
        )
        return [
            (not p.require_evidence
             or evidence_count >= p.minimum_evidence_items,
             evidence_reason),
            (proposal.confidence >= p.min_confidence,
             f"confidence {proposal.confidence:.2f} "
             f"{'≥' if proposal.confidence >= p.min_confidence else '<'} "
             f"min {p.min_confidence:.2f}"),
            (p.is_trusted(proposal.agent_id),
             f"agent {proposal.agent_id!r} "
             f"{'trusted' if p.is_trusted(proposal.agent_id) else 'not trusted'}"),
            (not conflict_issues,
             "; ".join(conflict_issues) if conflict_issues else "no conflicting memory"),
        ]

    def _route_automatic(self, proposal, actor_id=None) -> PromotionDecision:
        gates = self._automatic_gates(proposal, actor_id)
        reasons = [why for _, why in gates]
        if all(ok for ok, _ in gates):
            return PromotionDecision(
                proposal.proposal_id, PromotionOutcome.PROMOTE, self.mode,
                method=PromotionMethod.AUTOMATIC,
                
                reasons=reasons)
        return PromotionDecision(
            proposal.proposal_id, PromotionOutcome.HOLD, self.mode,
            reasons=reasons)

    def _route_consensus(self, proposal, actor_id=None) -> PromotionDecision:
        """Admit once enough DISTINCT agents satisfy the consensus admission gate.

        This is governance for durable-memory admission only. It is not ELLA
        independence, does not create likelihood mass, and never makes a claim
        VERIFIED. Evidence with no agent_id counts as one anonymous admission
        contributor for backwards-compatible consensus policy."""
        agents = {ev.agent_id for ev in proposal.evidence}
        n = len(agents)
        threshold = self.policy.consensus_threshold
        reasons = [f"{n} distinct corroborating agent(s), need {threshold}"]
        # Even under consensus, an ungrounded proposal cannot promote.
        evidence_count = len(proposal.evidence)
        if (self.policy.require_evidence
                and evidence_count < self.policy.minimum_evidence_items):
            reasons.append((
                f"{evidence_count} evidence item(s), need "
                f"{self.policy.minimum_evidence_items}"
            ) if evidence_count else (
                f"no evidence (need {self.policy.minimum_evidence_items} item(s))"
            ))
            return PromotionDecision(
                proposal.proposal_id, PromotionOutcome.HOLD, self.mode,
                reasons=reasons)
        conflict_issues = self.conflict_hold_reasons(proposal, actor_id)
        if conflict_issues:
            reasons.extend(conflict_issues)
            return PromotionDecision(
                proposal.proposal_id, PromotionOutcome.HOLD, self.mode,
                reasons=reasons)
        if n >= threshold:
            return PromotionDecision(
                proposal.proposal_id, PromotionOutcome.PROMOTE, self.mode,
                method=PromotionMethod.CONSENSUS,
                
                reasons=reasons)
        return PromotionDecision(
            proposal.proposal_id, PromotionOutcome.HOLD, self.mode,
            reasons=reasons)

    def _route_human(self, proposal, actor_id=None) -> PromotionDecision:
        """Never auto-promote. A human decides by calling db.promote() directly
        (which records promotion_method=HUMAN)."""
        return PromotionDecision(
            proposal.proposal_id, PromotionOutcome.HOLD, self.mode,
            reasons=["human approval required"] +
            self.conflict_hold_reasons(proposal, actor_id))

    def _route_hybrid(self, proposal, actor_id=None) -> PromotionDecision:
        """Route by risk. A high-risk proposal — low confidence, an existing
        conflicting memory, or an untrusted agent — is held for a human. Anything
        else is judged by the ordinary automatic gates."""
        p = self.policy
        risks = []
        if proposal.confidence <= p.risk_confidence:
            risks.append(f"low confidence {proposal.confidence:.2f} "
                         f"≤ risk {p.risk_confidence:.2f}")
        risks.extend(self.conflict_hold_reasons(proposal, actor_id))
        if not p.is_trusted(proposal.agent_id):
            risks.append(f"agent {proposal.agent_id!r} not trusted")
        if risks:
            return PromotionDecision(
                proposal.proposal_id, PromotionOutcome.HOLD, self.mode,
                reasons=["high-risk → human review"] + risks)
        # Not high-risk: fall through to the automatic gates, but label the
        # method AUTOMATIC (hybrid promoted it without a human).
        decision = self._route_automatic(proposal, actor_id)
        decision.mode = self.mode
        return decision

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _has_conflicting_memory(self, proposal) -> bool:
        """§12 conflict detection. A proposal derived_from existing memories that
        are themselves entangled in a `contradicts` relation should not silently
        auto-commit — the conflict must be surfaced (held), never overwritten
        (§7: 'do not silently delete conflicting memories').

        We can only check what the proposal points at before it exists: its
        derivation ids. If any derived-from memory currently has a mapped
        conflict, treat the proposal as conflicting."""
        return bool(self.conflict_hold_reasons(proposal))

    def conflict_hold_reasons(self, proposal, actor_id=None) -> list[str]:
        """Evaluate current mapped conflict heads and unmapped graph hints."""
        from ..core.types import ConflictStatus, RecordType

        issues = []
        for src_id in proposal.derivation:
            source = self._db._reader.get(
                src_id, include_deprecated=True, include_superseded=True)
            # Failure-to-lesson promotion already derives from a FAILURE record.
            # Accept that source without making failures durable memories or
            # bypassing the ordinary evidence, namespace and conflict gates.
            valid_source_types = self._db._DURABLE_MEMORY_TYPES | {RecordType.FAILURE}
            if source is None or source.record_type not in valid_source_types:
                issues.append(f"missing or invalid derivation {src_id}; review required")
                continue
            if source.namespace != proposal.namespace:
                issues.append(f"cross-namespace derivation {src_id}; review required")
                continue
            if actor_id is not None:
                self._db.require_namespace_access(source.namespace, actor_id, "read")
            mapped = self._db.conflicts_for(
                src_id, include_closed=True, caller=actor_id)
            mapped_peers = set()
            for conflict in mapped:
                peer = (conflict.memory_b if conflict.memory_a == src_id
                        else conflict.memory_a)
                mapped_peers.add(peer)
                if conflict.status in (ConflictStatus.OPEN, ConflictStatus.INVESTIGATING):
                    issues.append(f"conflicting memory exists: {conflict.conflict_id} is {conflict.status.value}")
                elif conflict.status == ConflictStatus.RESOLVED:
                    if conflict.winner_id not in (conflict.memory_a, conflict.memory_b):
                        issues.append(f"conflict {conflict.conflict_id} has ambiguous resolution; review required")
                    elif conflict.winner_id != src_id:
                        issues.append(f"conflict {conflict.conflict_id} rejected derivation; review required")
            for peer in self._db._graph_index.connected(src_id, "contradicts"):
                if peer not in mapped_peers:
                    issues.append(f"conflicting memory exists: unmapped contradicts relationship for {src_id}; investigation required")
        return issues
