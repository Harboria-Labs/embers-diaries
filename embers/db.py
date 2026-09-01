"""
Ember's Diaries — EmberDB
The main connection interface. This is what users import and use.

Usage:
    from embers import EmberDB

    db = EmberDB.connect("./my_store")
    record_id = db.write(EmberRecord(namespace="memories", data={"content": "hello"}))
    record = db.get(record_id)
"""

import sys
from pathlib import Path
from datetime import datetime

from .core.record import EmberRecord
from .core.annotation import Annotation
from .core.types import (
    RecordType, DeprecationReason, AccessLevel, EdgeType, ProposalStatus,
)
from .core.edge import EdgeRef
from .core.evidence import Evidence
from .core.proposal import MemoryProposal
from .storage.store import PhysicalStore
from .engine.writer import WriteEngine
from .engine.reader import ReadEngine
from .index.master import MasterIndex
from .index.graph import GraphIndex
from .index.timeline import TimelineIndex
from .index.vector import VectorIndex
from .index.fulltext import FullTextIndex
from .query.engine import QueryEngine
from .namespace.manager import NamespaceManager


def _safe_print(text: str) -> None:
    """Print a decorative banner line without ever crashing the host process.

    The connection banner contains a 🔥 emoji and a "→" arrow. On consoles
    whose encoding cannot represent those glyphs (notably Windows' legacy
    cp1252 code page), a plain print() raises UnicodeEncodeError and takes down
    whatever called EmberDB.connect(). A DB library must never crash a program
    over cosmetic output, so we fall back to a lossy-but-safe rendering that
    keeps the banner readable wherever full Unicode isn't available.
    """
    try:
        print(text)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(text.encode(enc, errors="replace").decode(enc, errors="replace"))


class EmberDB:
    """
    Main interface for Ember's Diaries.
    One EmberDB instance = one store (a directory on disk).
    Thread-safe. Multiple agents can write concurrently.

    Integrates:
    - Physical storage (append-only, WAL-protected)
    - Index layer (master, graph, timeline, vector, full-text)
    - Query engine (unified across all indexes)
    - Namespace manager (logical partitions)
    """

    def __init__(self, store_path: str | Path):
        self._path = Path(store_path)
        self._store = PhysicalStore(self._path)
        self._writer = WriteEngine(self._store)
        self._reader = ReadEngine(self._store, self._writer)

        # Index layer
        self._master_index = MasterIndex(self._path)
        self._graph_index = GraphIndex(self._path)
        self._timeline_index = TimelineIndex(self._path)
        self._vector_index = VectorIndex(self._path)
        self._fulltext_index = FullTextIndex(self._path)

        # Query engine
        self._query_engine = QueryEngine(
            self._master_index, self._graph_index,
            self._timeline_index, self._vector_index,
            self._fulltext_index, self._reader,
        )

        # Namespace manager
        self._ns_manager = NamespaceManager(self._path)

        # Register write callbacks to keep indexes updated
        self._writer.register_callback(self._on_write)

        # Rebuild indexes from existing records if needed
        self._rebuild_indexes_if_needed()

        _safe_print(f"🔥 Ember's Diaries connected → {self._path}")
        stats = self._store.stats()
        _safe_print(f"   Records: {stats['record_count']} | WAL: {stats['wal_size_bytes']} bytes")

    @classmethod
    def connect(cls, store_path: str | Path) -> "EmberDB":
        """Connect to (or create) an Ember's Diaries store."""
        return cls(store_path)

    def _rebuild_indexes_if_needed(self):
        """On startup, rebuild indexes from store if they're empty."""
        store_count = self._store.record_count()
        index_count = self._master_index.record_count()
        if store_count > 0 and index_count == 0:
            print(f"   Rebuilding indexes for {store_count} records...")
            for rid in self._store.all_ids():
                record = self._store.read(rid)
                if record:
                    self._index_record(record)

    def _on_write(self, record: EmberRecord, operation: str):
        """Callback after every write — keeps indexes in sync."""
        if operation in ("write", "update"):
            self._index_record(record)

    def _index_record(self, record: EmberRecord):
        """Add a record to all relevant indexes."""
        # Master index
        self._master_index.index_record(
            record.id, record.namespace, record.record_type.value,
            record.created_at.isoformat(), record.tags,
            written_by=record.written_by,
            agent_id=record.agent_id,
            session_id=record.session_id,
            supersedes=record.supersedes)

        # Timeline index
        self._timeline_index.add(
            record.id, record.namespace, record.created_at.isoformat())

        # Full-text index
        self._fulltext_index.add(
            record.id, record.data, record.namespace,
            extra_text=" ".join(record.tags))

        # Vector index (if record has embedding)
        if record.embedding:
            self._vector_index.add(record.id, record.embedding, record.namespace)

        # Graph index (if record has connections)
        for edge in record.connections:
            self._graph_index.add_edge(
                record.id, edge.target_id,
                edge.edge_type.value, edge.weight, edge.edge_id)

        # Causal-derivation edges (Features #2/#3): make each record's
        # `derived_from` provenance a first-class edge in the graph, so
        # derivation is queryable in BOTH directions — "what did this derive
        # from?" (outgoing) and "what was derived from this?" (incoming). The
        # edge points from the new record to its source, matching the
        # backward-in-time convention of `supersedes`. The edge_id is
        # deterministic per (child, source) so the link is identifiable; edges
        # are created once per write (rebuild only runs against an empty graph).
        for target_id in record.derived_from:
            self._graph_index.add_edge(
                record.id, target_id, EdgeType.DERIVED_FROM.value,
                edge_id=f"df:{record.id}:{target_id}", label="derived_from")

    # ── Write ─────────────────────────────────────────────────────────────────

    def write(self, record: EmberRecord) -> str:
        """
        Write a new record. Returns the record ID.
        The record is permanent — it can never be deleted.
        """
        return self._writer.write(record)

    def update(self, record_id: str, new_data: dict,
               written_by: str = "system",
               agent_id: str | None = None,
               session_id: str | None = None,
               creation_reason: str | None = None,
               derived_from: list | None = None) -> tuple[str, str]:
        """
        Create a new version of an existing record.
        Old record is preserved (superseded, never deleted).
        Returns (new_id, old_id).

        Provenance (Feature #3) — agent_id / session_id / creation_reason /
        derived_from — attributes THIS version to its author; it is recorded on
        the new record and folded into its content hash.
        """
        result = self._writer.update(
            record_id, new_data, written_by,
            agent_id=agent_id, session_id=session_id,
            creation_reason=creation_reason, derived_from=derived_from)
        self._master_index.mark_superseded(record_id, result[0])
        return result

    def annotate(self, record_id: str, annotation: Annotation) -> str:
        """
        Add an annotation to a record without modifying it.
        Returns the annotation ID.
        """
        return self._writer.annotate(record_id, annotation)

    def deprecate(self, record_id: str,
                  reason: DeprecationReason = DeprecationReason.MANUAL,
                  note: str = "",
                  written_by: str = "system") -> bool:
        """
        Mark a record as deprecated. It remains in the store forever.
        Returns True if successful.
        """
        result = self._writer.deprecate(record_id, reason, note, written_by)
        if result:
            self._master_index.mark_deprecated(record_id)
        return result

    # ── Read ──────────────────────────────────────────────────────────────────

    def get(self, record_id: str,
            include_deprecated: bool = False,
            include_superseded: bool = False) -> EmberRecord | None:
        """Get a record by ID. Returns None if not found or filtered."""
        return self._reader.get(record_id, include_deprecated, include_superseded)

    def get_current(self, record_id: str) -> EmberRecord | None:
        """Follow supersession chain to get the latest version."""
        return self._reader.get_current(record_id)

    def get_history(self, record_id: str) -> list[EmberRecord]:
        """Full supersession chain, oldest to newest."""
        return self._reader.get_history(record_id)

    def get_at(self, record_id: str, timestamp: datetime) -> EmberRecord | None:
        """State of a record at a specific point in time."""
        return self._reader.get_at(record_id, timestamp)

    def get_namespace(self, namespace: str,
                      include_deprecated: bool = False,
                      limit: int | None = None) -> list[EmberRecord]:
        """All records in a namespace."""
        return self._reader.get_namespace(namespace, include_deprecated, limit=limit)

    def exists(self, record_id: str) -> bool:
        return self._reader.exists(record_id)

    # ── Provenance (Feature #3 — WHO / WHEN / WHERE / WHY) ─────────────────────

    def get_by_agent(self, agent_id: str,
                     include_deprecated: bool = False,
                     include_superseded: bool = True) -> list[EmberRecord]:
        """Every record written by a specific agent identity.

        Answers the spec's 'which agent wrote it?' as an audit query, so
        superseded versions are included by default — the full trail of what
        that agent contributed, not just the current heads.
        """
        return self._resolve_ids(
            self._master_index.get_by_agent(agent_id),
            include_deprecated, include_superseded)

    def get_by_session(self, session_id: str,
                       include_deprecated: bool = False,
                       include_superseded: bool = True) -> list[EmberRecord]:
        """Every record written during a specific session (session → memories).

        Superseded versions are included by default for the same audit reason
        as get_by_agent().
        """
        return self._resolve_ids(
            self._master_index.get_by_session(session_id),
            include_deprecated, include_superseded)

    def get_provenance(self, record_id: str) -> dict | None:
        """Structured provenance (author/agent/session/timestamp/reason/source/
        derived_from) for a record, or None if it does not exist."""
        record = self._reader.get(record_id, include_deprecated=True,
                                  include_superseded=True)
        return record.provenance() if record else None

    def _resolve_ids(self, ids, include_deprecated: bool,
                     include_superseded: bool) -> list[EmberRecord]:
        """Resolve record IDs to records, honoring the visibility filters and
        skipping any that resolve to None (filtered out or missing)."""
        out = []
        for rid in ids:
            r = self._reader.get(rid, include_deprecated, include_superseded)
            if r is not None:
                out.append(r)
        return out

    # ── Versioning + Causal Graph (Feature #2) ─────────────────────────────────
    # The append-only store keeps every version, so a record can be superseded
    # in more than one direction (V1 → {V2-A, V2-B}). The linear get_current() /
    # get_history() / get_at() above follow a single line of descent and are
    # preserved unchanged; the methods here expose the FULL branching version
    # graph plus the typed causal edges between memories, answering the spec's
    # §2 questions. Version methods resolve with include_superseded=True because
    # intermediate versions are, by definition, superseded — they must still be
    # retrievable for lineage and audit.

    def version_children(self, record_id: str) -> list[EmberRecord]:
        """Direct next versions of a record. Two or more means the version
        history BRANCHES here — each child is an independent successor."""
        return self._resolve_ids(
            self._master_index.get_version_children(record_id), True, True)

    def version_parent(self, record_id: str) -> EmberRecord | None:
        """The immediate prior version (the record this one superseded), or
        None if this is an original."""
        pid = self._master_index.get_version_parent(record_id)
        return self._reader.get(pid, True, True) if pid else None

    def version_ancestors(self, record_id: str) -> list[EmberRecord]:
        """Every prior version, nearest first, back to the original."""
        out = []
        for i in self._master_index.get_version_ancestors(record_id):
            r = self._reader.get(i, True, True)
            if r is not None:
                out.append(r)
        return out

    def version_descendants(self, record_id: str) -> list[EmberRecord]:
        """Every later version across all branches of this lineage."""
        return self._resolve_ids(
            self._master_index.get_version_descendants(record_id), True, True)

    def current_versions(self, record_id: str) -> list[EmberRecord]:
        """The live head(s) of the lineage — leaves with no successor. More than
        one means the history forked into branches that were never reunited.
        (get_current() returns only the single head of the linear chain.)"""
        return self._resolve_ids(
            self._master_index.get_version_heads(record_id), True, True)

    def branch_points(self, record_id: str) -> list[EmberRecord]:
        """The records in this lineage where the version history forks (were
        superseded in more than one direction)."""
        return self._resolve_ids(
            self._master_index.get_branch_points(record_id), True, True)

    def version_tree(self, record_id: str) -> dict[str, list[str]]:
        """The whole lineage as an adjacency map ``{record_id: [child ids]}``,
        rooted at the original version — the raw branch structure."""
        return self._master_index.get_version_tree(record_id)

    def what_came_before(self, record_id: str) -> list[EmberRecord]:
        """Predecessors of a memory: its prior VERSIONS plus the memories it was
        explicitly DERIVED FROM (provenance `derived_from`). Answers §2's 'what
        came before this memory?' across both the version and derivation axes."""
        ids = set(self._master_index.get_version_ancestors(record_id))
        ids.update(self._graph_index.connected(
            record_id, EdgeType.DERIVED_FROM.value))
        ids.discard(record_id)
        return self._resolve_ids(ids, True, True)

    def what_was_derived_from(self, record_id: str) -> list[EmberRecord]:
        """The inverse of what_came_before: later VERSIONS of this memory plus
        memories that cite it as a derivation source (incoming `derived_from`).
        Answers §2's 'what memories were derived from it?'"""
        ids = set(self._master_index.get_version_descendants(record_id))
        for e in self._graph_index.get_edges(record_id, direction="incoming"):
            if e["edge_type"] == EdgeType.DERIVED_FROM.value:
                ids.add(e["target"])
        ids.discard(record_id)
        return self._resolve_ids(ids, True, True)

    def caused_by(self, record_id: str) -> list[EmberRecord]:
        """Memories recorded as causes of this one — outgoing `caused_by` edges,
        created via ``link(effect, cause, 'caused_by')``. Answers half of §2's
        'which memory caused another?'"""
        return self._resolve_ids(
            self._graph_index.connected(record_id, EdgeType.CAUSED_BY.value),
            True, True)

    def led_to(self, record_id: str) -> list[EmberRecord]:
        """Memories this one caused or contributed to — outgoing `led_to` edges,
        created via ``link(cause, effect, 'led_to')``. The forward-in-time
        counterpart to caused_by()."""
        return self._resolve_ids(
            self._graph_index.connected(record_id, EdgeType.LED_TO.value),
            True, True)

    def conflicts(self, record_id: str) -> list[EmberRecord]:
        """Memories that contradict this one, in EITHER direction — conflict is
        symmetric. Supports the 'map both competing claims, never overwrite'
        model: record X=true and X=false, ``link`` them `contradicts`, and let
        the conflict engine reconcile later without destroying either."""
        ids = set(self._graph_index.connected(
            record_id, EdgeType.CONTRADICTS.value))
        for e in self._graph_index.get_edges(record_id, direction="incoming"):
            if e["edge_type"] == EdgeType.CONTRADICTS.value:
                ids.add(e["target"])
        ids.discard(record_id)
        return self._resolve_ids(ids, True, True)

    # ── Discovery, Evidence & Promotion (Features #4 / #5) ─────────────────────
    # The path research → discovery → evidence → PROPOSAL → validation → durable
    # memory. A proposal is a first-class, hashed, append-only PROPOSAL record;
    # its evidence rides inside the record's `data` (so it is covered by the
    # content hash and cannot be swapped after sealing). Validation is an
    # append-only status transition: promote() writes the durable memory and
    # supersedes the proposal with a PROMOTED copy that points at the memory;
    # reject() supersedes it with a REJECTED copy. Nothing is ever deleted, so a
    # rejected proposal stays permanently distinguishable from a committed one.

    def propose(self, proposal: MemoryProposal) -> str:
        """Record a memory proposal (a discovery awaiting validation, §4).

        The proposal is sealed and stored as a PROPOSAL record — NOT yet a
        durable memory. Its evidence is sealed too, so each piece keeps the
        identity/hash it will carry if the proposal is promoted. Returns the
        proposal record id (== proposal.proposal_id)."""
        proposal.seal_evidence()
        proposal.status = ProposalStatus.PENDING
        record = EmberRecord(
            id=proposal.proposal_id,
            namespace=proposal.namespace,
            record_type=RecordType.PROPOSAL,
            data=proposal.to_record_payload(),
            written_by=proposal.written_by,
            agent_id=proposal.agent_id,
            session_id=proposal.session_id,
            creation_reason=proposal.reason,
            derived_from=list(proposal.derivation),
            confidence=proposal.confidence,
            tags=list(proposal.tags) + ["proposal"],
        )
        return self._writer.write(record)

    def get_proposal(self, proposal_id: str) -> MemoryProposal | None:
        """Reconstruct a MemoryProposal from its stored record (any status).

        Follows supersession to the CURRENT proposal record, so the status
        reflects the latest transition (pending → promoted/rejected)."""
        rec = self._reader.get_current(proposal_id)
        if rec is None:
            rec = self._reader.get(proposal_id, include_deprecated=True,
                                   include_superseded=True)
        if rec is None or rec.record_type != RecordType.PROPOSAL:
            return None
        return self._proposal_from_record(rec)

    def _proposal_from_record(self, rec: EmberRecord) -> MemoryProposal:
        """Rebuild a MemoryProposal from a PROPOSAL record's payload."""
        data = dict(rec.data or {})
        return MemoryProposal(
            proposal_id=data.get("proposal_id", rec.id),
            namespace=rec.namespace,
            discovery=data.get("discovery"),
            reason=data.get("reason", rec.creation_reason or ""),
            evidence=[Evidence.from_dict(e) for e in data.get("evidence", [])],
            sources=data.get("sources", []),
            confidence=data.get("confidence", rec.confidence),
            derivation=data.get("derivation", list(rec.derived_from)),
            written_by=rec.written_by,
            agent_id=rec.agent_id,
            session_id=rec.session_id,
            created_at=rec.created_at,
            status=ProposalStatus(data.get("status", "pending")),
            tags=[t for t in rec.tags if t != "proposal"],
        )

    def promote(self, proposal_id: str,
                validated_by: str = "system") -> tuple[str, str]:
        """Validate a proposal into a durable memory (§4 validation step).

        Writes a NEW durable memory (record_type NODE) carrying the discovery as
        its data and the proposal's justification as provenance: reason →
        creation_reason, derivation → derived_from, confidence → confidence.
        Each piece of evidence is written as its own hashed EVIDENCE record and
        linked to the memory with a SUPPORTS edge, so the memory's grounding is
        traceable and other agents can attach further evidence later without
        touching it. The proposal record is then superseded by a PROMOTED copy
        that records which memory it became.

        Returns (memory_id, proposal_id). Raises if the proposal does not exist
        or is not currently pending.
        """
        proposal = self.get_proposal(proposal_id)
        if proposal is None:
            raise KeyError(f"Proposal {proposal_id} not found.")
        if proposal.status != ProposalStatus.PENDING:
            raise ValueError(
                f"Proposal {proposal_id} is {proposal.status.value}, "
                "only a pending proposal can be promoted.")

        # 1. The durable memory — the discovery, now first-class.
        memory = EmberRecord(
            namespace=proposal.namespace,
            record_type=RecordType.NODE,
            data=proposal.discovery,
            written_by=validated_by,
            agent_id=proposal.agent_id,
            session_id=proposal.session_id,
            creation_reason=proposal.reason,
            derived_from=list(proposal.derivation),
            confidence=proposal.confidence,
            tags=[t for t in proposal.tags if t != "proposal"],
        )
        memory_id = self._writer.write(memory)

        # 2. Each piece of evidence → its own EVIDENCE record + SUPPORTS edge.
        for ev in proposal.evidence:
            self._write_evidence_record(ev, memory_id)

        # 3. Supersede the proposal with a PROMOTED copy pointing at the memory.
        payload = proposal.to_record_payload()
        payload["status"] = ProposalStatus.PROMOTED.value
        payload["promoted_to"] = memory_id
        self.update(proposal_id, payload, written_by=validated_by,
                    creation_reason="proposal promoted to durable memory")
        return memory_id, proposal_id

    def reject(self, proposal_id: str, reason: str = "",
               rejected_by: str = "system") -> str:
        """Reject a proposal (§16 → Promotion: rejected proposals remain
        distinguishable from committed memories).

        Append-only: the proposal is superseded by a REJECTED copy. It is never
        deleted and never becomes a memory — it stays permanently queryable as a
        rejected proposal. Returns the new (rejected) proposal record id."""
        proposal = self.get_proposal(proposal_id)
        if proposal is None:
            raise KeyError(f"Proposal {proposal_id} not found.")
        if proposal.status != ProposalStatus.PENDING:
            raise ValueError(
                f"Proposal {proposal_id} is {proposal.status.value}, "
                "only a pending proposal can be rejected.")
        payload = proposal.to_record_payload()
        payload["status"] = ProposalStatus.REJECTED.value
        payload["rejection_reason"] = reason
        new_id, _ = self.update(proposal_id, payload, written_by=rejected_by,
                                creation_reason=f"proposal rejected: {reason}")
        return new_id

    def _write_evidence_record(self, ev: Evidence, supports_id: str) -> str:
        """Write one Evidence as an EVIDENCE record and link it to the memory
        it supports with a SUPPORTS edge carried ON the evidence record.

        The edge lives on the record (not just in the graph index) so it is part
        of the evidence's content hash and is rebuilt from the store on reconnect
        — the memory ← evidence grounding survives without a checkpoint. The
        evidence's own content_hash is preserved in the record data."""
        if ev.content_hash is None:
            ev.seal()
        edge = EdgeRef(
            edge_id=f"supports:{ev.evidence_id}:{supports_id}",
            target_id=supports_id,
            edge_type=EdgeType.SUPPORTS,
            label="supports",
        )
        supported = self._reader.get(supports_id, True, True)
        rec = EmberRecord(
            id=ev.evidence_id,
            namespace=supported.namespace if supported else "default",
            record_type=RecordType.EVIDENCE,
            data=ev.to_dict(),
            connections=[edge],
            written_by=ev.agent_id or "system",
            agent_id=ev.agent_id,
            session_id=ev.session_id,
            origin=ev.source,
        )
        # Idempotent: the same evidence may already back another memory.
        if self._reader.exists(ev.evidence_id):
            # Already stored — just ensure the SUPPORTS edge exists in the graph.
            self._graph_index.add_edge(
                ev.evidence_id, supports_id, EdgeType.SUPPORTS.value,
                edge_id=edge.edge_id, label="supports")
            return ev.evidence_id
        return self._writer.write(rec)

    def attach_evidence(self, memory_id: str, ev: Evidence) -> str:
        """Attach a new piece of evidence to an EXISTING durable memory.

        This is the multi-agent confirmation path: any agent can add independent
        evidence to a memory over time WITHOUT modifying (superseding) it —
        append a new EVIDENCE record with a SUPPORTS edge. Append-only, so the
        memory's hash is untouched and its confirmation trail only grows.
        Returns the evidence record id."""
        if not self._reader.exists(memory_id):
            raise KeyError(f"Memory {memory_id} not found.")
        return self._write_evidence_record(ev, memory_id)

    def evidence_for(self, memory_id: str) -> list[EmberRecord]:
        """Every EVIDENCE record supporting a memory (incoming SUPPORTS edges).

        The chain CLAIM → EVIDENCE → SOURCE: each returned record carries the
        source/source_type/observed_at identity of the observation that grounds
        the claim. An empty list means the memory rests on a bare agent
        assertion, not on evidence."""
        ids = set()
        for e in self._graph_index.get_edges(memory_id, direction="incoming"):
            if e["edge_type"] == EdgeType.SUPPORTS.value:
                ids.add(e["target"])
        return self._resolve_ids(ids, True, True)

    def get_evidence(self, evidence_id: str) -> Evidence | None:
        """Reconstruct a structured Evidence object from its stored record."""
        rec = self._reader.get(evidence_id, include_deprecated=True,
                               include_superseded=True)
        if rec is None or rec.record_type != RecordType.EVIDENCE:
            return None
        return Evidence.from_dict(rec.data)

    def proposals(self, namespace: str,
                  status: ProposalStatus | None = None) -> list[MemoryProposal]:
        """All proposals in a namespace, optionally filtered by status.

        Resolves to the CURRENT version of each proposal so status is accurate,
        and includes superseded/deprecated so rejected and promoted proposals
        remain visible (they are, by construction, superseded records)."""
        out, seen = [], set()
        records = self._reader.get_namespace(
            namespace, include_deprecated=True, limit=None)
        # get_namespace returns heads; also sweep superseded proposal lineages
        # by resolving each to its current version.
        for rec in records:
            if rec.record_type != RecordType.PROPOSAL:
                continue
            current = self._reader.get_current(rec.id) or rec
            if current.id in seen:
                continue
            seen.add(current.id)
            prop = self._proposal_from_record(current)
            if status is None or prop.status == status:
                out.append(prop)
        return out

    # ── Query (Index-accelerated) ─────────────────────────────────────────────

    def query(self, namespace: str, filters: dict | None = None,
              tags: list[str] | None = None,
              limit: int = 100,
              include_deprecated: bool = False,
              include_superseded: bool = False) -> list[EmberRecord]:
        """Document query with index acceleration."""
        # Backward compat: extract tags from filters if provided there
        effective_tags = tags
        effective_filters = dict(filters) if filters else None
        if effective_filters and "tags" in effective_filters:
            tag_val = effective_filters.pop("tags")
            if isinstance(tag_val, str):
                effective_tags = (effective_tags or []) + [tag_val]
            elif isinstance(tag_val, list):
                effective_tags = (effective_tags or []) + tag_val
            if not effective_filters:
                effective_filters = None

        return self._query_engine.query(
            namespace, effective_filters, effective_tags, limit=limit,
            include_deprecated=include_deprecated,
            include_superseded=include_superseded)

    def search(self, query_text: str, namespace: str | None = None,
               top_k: int = 10) -> list[tuple[EmberRecord, float]]:
        """Full-text BM25 search."""
        return self._query_engine.search(query_text, namespace, top_k)

    def similar(self, embedding: list[float],
                namespace: str | None = None,
                top_k: int = 10,
                threshold: float = 0.0) -> list[tuple[EmberRecord, float]]:
        """Vector similarity search."""
        return self._query_engine.similar(
            embedding, namespace, top_k, threshold)

    # ── Graph ─────────────────────────────────────────────────────────────────

    def link(self, from_id: str, to_id: str,
             edge_type: str = "relates_to",
             weight: float = 1.0, label: str = "") -> bool:
        """Create a graph edge between two records."""
        if not self.exists(from_id) or not self.exists(to_id):
            return False
        import uuid
        self._graph_index.add_edge(
            from_id, to_id, edge_type, weight,
            edge_id=str(uuid.uuid4()), label=label)
        return True

    def neighbors(self, record_id: str, depth: int = 1,
                  edge_type: str | None = None,
                  direction: str = "outgoing") -> list[EmberRecord]:
        """Graph traversal — find connected records."""
        return self._query_engine.neighbors(
            record_id, depth, edge_type, direction)

    def path(self, from_id: str, to_id: str,
             max_depth: int = 10) -> list[EmberRecord] | None:
        """Find shortest path between two records in the graph."""
        return self._query_engine.path(from_id, to_id, max_depth)

    def subgraph(self, root_id: str, depth: int = 2) -> dict:
        """Extract a subgraph around a record."""
        return self._query_engine.subgraph(root_id, depth)

    # ── Time ──────────────────────────────────────────────────────────────────

    def timeline(self, namespace: str,
                 start: datetime | None = None,
                 end: datetime | None = None) -> list[EmberRecord]:
        """Records in a time range via timeline index."""
        return self._query_engine.timeline(namespace, start, end)

    def latest(self, namespace: str, limit: int = 10) -> list[EmberRecord]:
        """Most recent N records in a namespace."""
        return self._query_engine.latest(namespace, limit)

    # ── Annotations ───────────────────────────────────────────────────────────

    def get_annotations(self, record_id: str) -> list[Annotation]:
        return self._writer.get_annotations(record_id)

    # ── Namespaces ────────────────────────────────────────────────────────────

    def create_namespace(self, name: str, description: str = "",
                         access_level: AccessLevel = AccessLevel.PRIVATE,
                         owner: str = "system"):
        """Create a namespace with access control."""
        return self._ns_manager.create(name, description, access_level, owner)

    def list_namespaces(self):
        return self._ns_manager.list_all()

    def check_namespace_access(self, namespace: str, caller: str,
                                operation: str = "read") -> bool:
        """Check if caller has access. operation: 'read' or 'write'."""
        if operation == "write":
            return self._ns_manager.check_write(namespace, caller)
        return self._ns_manager.check_read(namespace, caller)

    def grant_namespace_access(self, namespace: str, caller: str,
                                level: str = "read"):
        """Grant access to a caller. level: 'read' or 'write'."""
        if level == "write":
            self._ns_manager.grant_write(namespace, caller)
        else:
            self._ns_manager.grant_read(namespace, caller)

    def revoke_namespace_access(self, namespace: str, caller: str,
                                 level: str = "read"):
        """Revoke access from a caller. level: 'read' or 'write'."""
        if level == "write":
            self._ns_manager.revoke_write(namespace, caller)
        else:
            self._ns_manager.revoke_read(namespace, caller)

    # ── Persistence ───────────────────────────────────────────────────────────

    def checkpoint(self):
        """Persist all indexes and compact the WAL."""
        self._master_index.persist()
        self._graph_index.persist()
        self._timeline_index.persist()
        self._vector_index.persist()
        self._fulltext_index.persist()
        self._ns_manager.persist()
        self._store.checkpoint_wal()

    def stats(self) -> dict:
        base = self._store.stats()
        base["indexes"] = {
            "master": self._master_index.stats(),
            "graph": self._graph_index.stats(),
            "timeline": self._timeline_index.stats(),
            "vector": self._vector_index.stats(),
            "fulltext": self._fulltext_index.stats(),
        }
        return base

    def __repr__(self) -> str:
        return f"EmberDB(path={self._path}, records={self._store.record_count()})"
