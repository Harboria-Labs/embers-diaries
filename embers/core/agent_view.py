"""
Ember's Diaries — Agent View (Feature #8: Multi-Agent Memory)

The spec's §8 requirement, in one line: "Every memory write should be
attributable to an agent." An AgentView is a per-agent handle onto the shared
substrate — it carries a single agent identity (and, optionally, a session)
so that agent's writes are stamped automatically, without the agent having to
pass agent_id / session_id / written_by to every call.

Why a *separate* facade rather than global state: EmberDB is shared by every
agent in a process. If agent identity were a field on EmberDB, two agents
would swap each other's attribution the moment one changed it. By deriving a
view *per agent*, the caller that holds `agent_b = db.as_agent("coder-02")`
can write as coder-02 while another caller holds `agent_a` and writes as
researcher-01 — concurrently, against the same store, with no cross-talk.

This complements (does not replace) #21's AgentRegistry. The registry is the
identity *issuer* at the API boundary: it mints agent_id + a hashed token and
`authenticate()`s callers. The AgentView is the storage-boundary *stamper*:
once an agent is authenticated, it wraps the db and attributes that agent's
writes. #21 answers "is this caller who they claim to be?"; #8 answers "does
every durable write carry that agent's name?" Both are needed.

Scope note — what this deliberately does NOT do:
    * It does not add a symmetric architecture (agent ↔ private store). The
      spec's last line — "adding more agents does not require redesigning the
      storage layer" — is satisfied precisely because agents are a *view*
      onto one substrate, not a separate one. Two agents = two AgentViews =
      zero storage changes.
    * It does not enforce attribution by default. `write()` historically
      defaults to written_by="system" / agent_id=None, and §15 forbids
      silently breaking existing stores. Enforcement is opt-in via
      `EmberDB(enforce_attribution=True)` (or a per-db `.enforce_attribution`).
"""

from __future__ import annotations

from copy import copy
from typing import Any

from .evidence import Evidence
from .failure import Failure
from .proposal import MemoryProposal


class AgentView:
    """A per-agent handle onto a shared EmberDB.

    Every write through an AgentView is stamped with its `agent_id`
    ("who wrote this?"), its `session_id` when one is attached ("in which
    session?"), and `written_by` (the same identity, so authoring is
    attributable in both spellings the codebase uses). With the source object
    stamped before it is sealed, the provenance is folded into the record's
    content hash — so it cannot be silently rewritten after the fact.

    The methods mirror the narrow set of *durable-write* operations on
    EmberDB that take a source object (a record, a proposal, a failure).
    `submit()` / `promote()` are intentionally NOT re-stamped: they operate on
    a proposal that already carries its own agent from creation — the person
    who *vouches* for a memory at promotion time is recorded as `promoted_by`
    / changed_by on the promotion, which is a different question from who
    *wrote* the proposal.
    """

    def __init__(self, db, agent_id: str, session_id: str | None = None):
        if not agent_id or not str(agent_id).strip():
            raise ValueError("An AgentView needs a non-empty agent_id.")
        self._db = db
        self.agent_id = str(agent_id)
        self.session_id = session_id

    def __repr__(self) -> str:
        if self.session_id:
            return f"AgentView(agent={self.agent_id!r}, session={self.session_id!r})"
        return f"AgentView(agent={self.agent_id!r})"

    # ── Session scoping ────────────────────────────────────────────────────────

    def in_session(self, session_id: str) -> "AgentView":
        """A new view for the same agent bound to a specific session.

        Written as agent_id; used for the §9 use case where one agent has many
        bounded sessions."""
        return AgentView(self._db, self.agent_id, session_id)

    def session(self, session_id: str) -> "AgentView":
        """Alias for in_session() — session-scoped handle for a task."""
        return self.in_session(session_id)

    # ── Durable writes (stamped) ──────────────────────────────────────────────

    def write(self, record) -> str:
        """Write a record stamped with this agent's identity and session.

        The caller's record object is NOT mutated (it may be shared); a shallow
        copy is stamped and written instead. A plain `copy.copy` (rather than
        reconstructing the object) keeps post-init attributes like `embedding`
        and `connections` intact."""
        stamped = copy(record)
        stamped.agent_id = self.agent_id
        stamped.session_id = self.session_id
        stamped.written_by = self.agent_id
        return self._db.write(stamped)

    def update(self, record_id: str, new_data: dict,
               creation_reason: str | None = None,
               derived_from: list | None = None,
               expected_hash: str | None = None) -> tuple[str, str]:
        """Supersede a record as this agent — provenance on the new version."""
        return self._db.update(
            record_id, new_data, written_by=self.agent_id,
            agent_id=self.agent_id, session_id=self.session_id,
            creation_reason=creation_reason, derived_from=derived_from,
            expected_hash=expected_hash)

    def propose(self, proposal: MemoryProposal) -> str:
        """Record a discovery as this agent's proposal (stamped in place)."""
        proposal.written_by = self.agent_id
        proposal.agent_id = self.agent_id
        proposal.session_id = self.session_id
        return self._db.propose(proposal)

    def report_failure(self, failure: Failure) -> str:
        """Record a failed approach as this agent's (stamped in place)."""
        failure.agent_id = self.agent_id
        if self.session_id:
            failure.session_id = self.session_id
        return self._db.report_failure(failure)

    # ── Convenience: a discovery → proposal → durable-memory pipeline          ─

    def remember(self, discovery: Any, reason: str = "",
                 evidence: list | None = None, confidence: float = 0.5,
                 derivation: list | None = None, namespace: str = "default",
                 tags: list | None = None) -> str:
        """Propose a durable memory as this agent, in one call.

        Builds the MemoryProposal (stamping agent_id / session_id / written_by),
        then hands it to the Promotion Engine via db.propose(). Promotion itself
        is decided by the engine (§12); this only records the discovery.
        """
        proposal = MemoryProposal(
            namespace=namespace,
            discovery=discovery,
            reason=reason,
            evidence=[e if isinstance(e, Evidence) else Evidence.from_dict(e)
                      for e in (evidence or [])],
            confidence=confidence,
            derivation=list(derivation or []),
            tags=list(tags or []),
        )
        return self.propose(proposal)

    # ── Per-agent context queries (spec §8: "agent-specific context") ─────────

    def memories(self, **kw) -> list:
        """Every durable memory this agent wrote (includes superseded, so the
        agent's full contribution is audit-visible)."""
        return self._db.get_by_agent(self.agent_id, **kw)

    def proposals(self, include_deprecated: bool = True) -> list:
        """Every proposal this agent authored (any status)."""
        return [r for r in self._db.get_by_agent(
                    self.agent_id, include_deprecated=include_deprecated)
                if r.record_type.value == "proposal"]

    def failures(self, include_deprecated: bool = True) -> list:
        """Every failure this agent reported (promoted or not)."""
        return [r for r in self._db.get_by_agent(
                    self.agent_id, include_deprecated=include_deprecated)
                if r.record_type.value == "failure"]

    def context(self) -> dict:
        """A compact report of this agent's footprint in the shared store.

        Answers, for one agent: who am I, what have I written (by kind), and
        which of those writes are currently the live head of their lineage."""
        wrote = self._db.get_by_agent(self.agent_id)
        live = [r for r in wrote if self._db.get_current(r.id) is not None
                and self._db.get_current(r.id).id == r.id]
        by_type: dict[str, int] = {}
        for r in wrote:
            by_type[r.record_type.value] = by_type.get(r.record_type.value, 0) + 1
        return {
            "agent_id": self.agent_id,
            "session_id": self.session_id,
            "total_written": len(wrote),
            "live_heads": len(live),
            "by_type": by_type,
        }
