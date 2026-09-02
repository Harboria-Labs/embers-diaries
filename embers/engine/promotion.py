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
whether — and by which method — the proposal should become a durable memory, and
with what epistemic status. It never writes on its own; it calls back into the
existing `EmberDB.promote()` so all the append-only / hashing / evidence-edge
guarantees still hold.

THE ONE SEMANTIC THAT MUST NOT BE LOST (the user's words):

    "Promotion doesn't mean 'this is definitely true.' It means: 'This proposal
     met the criteria for entering durable memory.'"

So a promotion decision is two independent things:
  1. WHETHER to admit the proposal (the gates for the chosen mode), and
  2. with what STATUS it is admitted — VERIFIED vs PROVISIONAL — which reflects
     how strongly it is believed, decoupled from the mere fact of admission.
A proposal can be admitted (met the criteria) yet stored PROVISIONAL (not yet
strongly confirmed). That is the whole point of the checkpoint.
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
        verified_confidence at/above this it is admitted VERIFIED; between
                            min_confidence and here it is admitted PROVISIONAL
                            (this band is what encodes "promotion ≠ true")
        require_evidence    an ungrounded proposal (no Evidence) cannot
                            auto-promote — a bare assertion is not enough
        consensus_threshold distinct corroborating agents needed in CONSENSUS
        trusted_agents      None ⇒ trust every agent; otherwise a proposal's
                            agent_id must be in this set to auto-promote
        risk_confidence     HYBRID: at/below this a proposal is "high-risk" and
                            is routed to the human gate instead of auto-promoted
    """
    min_confidence: float = 0.7
    verified_confidence: float = 0.85
    require_evidence: bool = True
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
    status: MemoryStatus | None = None      # status it would be admitted with
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

    def route(self, proposal_id: str) -> PromotionDecision:
        """Decide what would happen to this proposal WITHOUT writing anything.

        Dispatches on the configured mode. Raises KeyError if the proposal does
        not exist; a proposal that is not currently PENDING always HOLDs (only a
        pending proposal is a candidate for promotion)."""
        proposal = self._db.get_proposal(proposal_id)
        if proposal is None:
            raise KeyError(f"Proposal {proposal_id} not found.")

        if proposal.status != ProposalStatus.PENDING:
            return PromotionDecision(
                proposal_id, PromotionOutcome.HOLD, self.mode,
                reasons=[f"proposal is {proposal.status.value}, not pending"])

        if self.mode == PromotionMode.HUMAN:
            return self._route_human(proposal)
        if self.mode == PromotionMode.CONSENSUS:
            return self._route_consensus(proposal)
        if self.mode == PromotionMode.HYBRID:
            return self._route_hybrid(proposal)
        return self._route_automatic(proposal)

    # ── Action ───────────────────────────────────────────────────────────────

    def submit(self, proposal_id: str, validated_by: str = "promotion-engine"
               ) -> PromotionResult:
        """Route the proposal and act on the decision.

        On PROMOTE, calls `db.promote()` with the decided method + status and
        returns the new memory id. On HOLD, writes nothing and leaves the
        proposal PENDING (it can be submitted again later once, e.g., more
        evidence has accumulated)."""
        decision = self.route(proposal_id)
        if not decision.will_promote:
            return PromotionResult(decision, memory_id=None)
        memory_id, _ = self._db.promote(
            proposal_id, validated_by=validated_by,
            status=decision.status, promotion_method=decision.method)
        return PromotionResult(decision, memory_id=memory_id)

    # ── Mode policies ──────────────────────────────────────────────────────────

    def _status_for(self, confidence: float) -> MemoryStatus:
        """Map confidence onto the admitted status. Above verified_confidence a
        memory is VERIFIED; in the [min, verified) band it is admitted but only
        PROVISIONAL — 'met the criteria to enter memory' ≠ 'known true'."""
        if confidence >= self.policy.verified_confidence:
            return MemoryStatus.VERIFIED
        return MemoryStatus.PROVISIONAL

    def _automatic_gates(self, proposal) -> list[tuple[bool, str]]:
        """The AUTOMATIC-mode criteria, each as (passed, explanation). Shared by
        automatic and hybrid so the gate logic lives in exactly one place."""
        p = self.policy
        conflict = self._has_conflicting_memory(proposal)
        return [
            (not p.require_evidence or proposal.is_grounded(),
             "grounded in evidence" if proposal.is_grounded()
             else "no evidence (bare assertion)"),
            (proposal.confidence >= p.min_confidence,
             f"confidence {proposal.confidence:.2f} "
             f"{'≥' if proposal.confidence >= p.min_confidence else '<'} "
             f"min {p.min_confidence:.2f}"),
            (p.is_trusted(proposal.agent_id),
             f"agent {proposal.agent_id!r} "
             f"{'trusted' if p.is_trusted(proposal.agent_id) else 'not trusted'}"),
            (not conflict,
             "conflicting memory exists" if conflict else "no conflicting memory"),
        ]

    def _route_automatic(self, proposal) -> PromotionDecision:
        gates = self._automatic_gates(proposal)
        reasons = [why for _, why in gates]
        if all(ok for ok, _ in gates):
            return PromotionDecision(
                proposal.proposal_id, PromotionOutcome.PROMOTE, self.mode,
                method=PromotionMethod.AUTOMATIC,
                status=self._status_for(proposal.confidence),
                reasons=reasons)
        return PromotionDecision(
            proposal.proposal_id, PromotionOutcome.HOLD, self.mode,
            reasons=reasons)

    def _route_consensus(self, proposal) -> PromotionDecision:
        """Promote once enough DISTINCT agents have corroborated the discovery.

        Corroboration is counted from distinct evidence authors — this is the
        multi-agent accumulation `attach_evidence` enables: Agent A proposes,
        Agents B and C independently attach supporting evidence, and at the
        threshold the proposal auto-promotes. Evidence with no agent_id counts
        as a single anonymous corroborator."""
        agents = {ev.agent_id for ev in proposal.evidence}
        n = len(agents)
        threshold = self.policy.consensus_threshold
        reasons = [f"{n} distinct corroborating agent(s), need {threshold}"]
        # Even under consensus, an ungrounded proposal cannot promote.
        if self.policy.require_evidence and not proposal.is_grounded():
            reasons.append("no evidence (bare assertion)")
            return PromotionDecision(
                proposal.proposal_id, PromotionOutcome.HOLD, self.mode,
                reasons=reasons)
        if self._has_conflicting_memory(proposal):
            reasons.append("conflicting memory exists")
            return PromotionDecision(
                proposal.proposal_id, PromotionOutcome.HOLD, self.mode,
                reasons=reasons)
        if n >= threshold:
            return PromotionDecision(
                proposal.proposal_id, PromotionOutcome.PROMOTE, self.mode,
                method=PromotionMethod.CONSENSUS,
                status=self._status_for(proposal.confidence),
                reasons=reasons)
        return PromotionDecision(
            proposal.proposal_id, PromotionOutcome.HOLD, self.mode,
            reasons=reasons)

    def _route_human(self, proposal) -> PromotionDecision:
        """Never auto-promote. A human decides by calling db.promote() directly
        (which records promotion_method=HUMAN)."""
        return PromotionDecision(
            proposal.proposal_id, PromotionOutcome.HOLD, self.mode,
            reasons=["human approval required"])

    def _route_hybrid(self, proposal) -> PromotionDecision:
        """Route by risk. A high-risk proposal — low confidence, an existing
        conflicting memory, or an untrusted agent — is held for a human. Anything
        else is judged by the ordinary automatic gates."""
        p = self.policy
        risks = []
        if proposal.confidence <= p.risk_confidence:
            risks.append(f"low confidence {proposal.confidence:.2f} "
                         f"≤ risk {p.risk_confidence:.2f}")
        if self._has_conflicting_memory(proposal):
            risks.append("conflicting memory exists")
        if not p.is_trusted(proposal.agent_id):
            risks.append(f"agent {proposal.agent_id!r} not trusted")
        if risks:
            return PromotionDecision(
                proposal.proposal_id, PromotionOutcome.HOLD, self.mode,
                reasons=["high-risk → human review"] + risks)
        # Not high-risk: fall through to the automatic gates, but label the
        # method AUTOMATIC (hybrid promoted it without a human).
        decision = self._route_automatic(proposal)
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
        for src_id in proposal.derivation:
            try:
                if self._db.conflicts(src_id):
                    return True
            except Exception:
                # A missing/unreadable derivation id is not a conflict signal.
                continue
        return False
