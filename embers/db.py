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
    MemoryStatus, PromotionMethod, PromotionMode,
)
from .core.edge import EdgeRef
from .core.evidence import Evidence
from .core.proposal import MemoryProposal
from .core.conflict import Conflict
from .core.session import Session
from .core.types import ConflictType, ConflictStatus, SessionStatus
from .storage.store import PhysicalStore
from .engine.writer import WriteEngine
from .engine.reader import ReadEngine
from .engine.promotion import PromotionEngine, PromotionPolicy
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

    def __init__(self, store_path: str | Path,
                 promotion_mode: PromotionMode = PromotionMode.AUTOMATIC,
                 promotion_policy: PromotionPolicy | None = None):
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

        # Promotion Engine (§10–§12) — the configurable policy that decides
        # whether/how a proposal becomes durable memory. Mechanism lives in
        # promote()/reject(); this is the routing in front of it.
        self._promotion = PromotionEngine(self, promotion_mode, promotion_policy)

        # Register write callbacks to keep indexes updated
        self._writer.register_callback(self._on_write)

        # Rebuild indexes from existing records if needed
        self._rebuild_indexes_if_needed()

        _safe_print(f"🔥 Ember's Diaries connected → {self._path}")
        stats = self._store.stats()
        _safe_print(f"   Records: {stats['record_count']} | WAL: {stats['wal_size_bytes']} bytes")

    @classmethod
    def connect(cls, store_path: str | Path,
                promotion_mode: PromotionMode = PromotionMode.AUTOMATIC,
                promotion_policy: PromotionPolicy | None = None) -> "EmberDB":
        """Connect to (or create) an Ember's Diaries store.

        `promotion_mode` selects how the Promotion Engine decides whether a
        proposal becomes durable memory (AUTOMATIC by default). `promotion_policy`
        tunes the gates (confidence thresholds, consensus size, trusted agents)."""
        return cls(store_path, promotion_mode=promotion_mode,
                   promotion_policy=promotion_policy)

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
               derived_from: list | None = None,
               expected_hash: str | None = None) -> tuple[str, str]:
        """
        Create a new version of an existing record.
        Old record is preserved (superseded, never deleted).
        Returns (new_id, old_id).

        Provenance (Feature #3) — agent_id / session_id / creation_reason /
        derived_from — attributes THIS version to its author; it is recorded on
        the new record and folded into its content hash.

        Optimistic concurrency (Feature #6) — pass `expected_hash` (the content
        hash of the version you read) to make this a compare-and-set: if another
        writer has advanced the lineage head in the meantime, the write is
        refused with a ConcurrentModificationError instead of silently forking.
        The returned old_id is the version actually superseded (the resolved
        head), which may differ from the record_id you passed.
        """
        result = self._writer.update(
            record_id, new_data, written_by,
            agent_id=agent_id, session_id=session_id,
            creation_reason=creation_reason, derived_from=derived_from,
            expected_hash=expected_hash)
        # result = (new_id, superseded_id); under CAS the superseded id is the
        # resolved head, so index the link off result[1], not the raw argument.
        self._master_index.mark_superseded(result[1], result[0])
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

    # ── Conflict Engine (Feature #7) ───────────────────────────────────────────
    # `conflicts()` above answers "what contradicts X?" from raw CONTRADICTS
    # edges. The Conflict Engine promotes that into a first-class, append-only
    # subsystem: a mapped contradiction becomes a CONFLICT record with the spec
    # §7 shape (memory_a/memory_b/detected_*/type/status/resolution) and a
    # triage lifecycle (open → investigating → resolved | accepted_both |
    # superseded). NEITHER contradicting memory is ever destroyed — only the
    # conflict record between them carries a resolution.
    #
    # Two conflict shapes are distinguished (§7):
    #   • STORAGE conflict — two agents racing to modify one memory. Prevented at
    #     write time by hash + version + optimistic concurrency (§6); it is never
    #     stored as a Conflict, it is refused as a ConcurrentModificationError.
    #   • SEMANTIC conflict — two durable memories whose claims disagree. This is
    #     what map_conflict() records and the lifecycle below reconciles.

    def map_conflict(self, memory_a: str, memory_b: str,
                     detected_by: str = "system",
                     conflict_type: ConflictType = ConflictType.SEMANTIC,
                     note: str = "") -> str:
        """Map a SEMANTIC contradiction between two existing memories (§7).

        Records a CONFLICT record (status OPEN) AND draws a symmetric
        `contradicts` edge between the two memories, so the contradiction is
        visible both as a queryable conflict object and via `conflicts()`.
        NEITHER memory is modified or destroyed. Idempotent: if a conflict
        between this pair is already mapped and still live (not resolved/
        superseded), its existing id is returned rather than mapping a duplicate.

        Returns the conflict record id. Raises KeyError if either memory is
        missing, ValueError if the two ids are equal."""
        if not self.exists(memory_a):
            raise KeyError(f"Memory {memory_a} not found.")
        if not self.exists(memory_b):
            raise KeyError(f"Memory {memory_b} not found.")

        conflict = Conflict(
            namespace=self.get(memory_a, True, True).namespace,
            memory_a=memory_a, memory_b=memory_b,
            conflict_type=conflict_type, detected_by=detected_by, note=note)

        # Idempotency: is this exact pair already mapped and still live?
        existing = self._find_live_conflict(conflict.pair_fingerprint())
        if existing is not None:
            return existing.conflict_id

        record = EmberRecord(
            id=conflict.conflict_id,
            namespace=conflict.namespace,
            record_type=RecordType.CONFLICT,
            data=conflict.to_record_payload(),
            written_by=detected_by,
            creation_reason="semantic conflict mapped",
            tags=list(conflict.tags) + ["conflict"],
        )
        cid = self._writer.write(record)

        # Map the contradiction on the graph too (symmetric), so the existing
        # conflicts() view and the promotion engine's conflict gate see it.
        self.link(memory_a, memory_b, EdgeType.CONTRADICTS.value,
                  label="contradicts")
        return cid

    def get_conflict(self, conflict_id: str) -> Conflict | None:
        """Reconstruct a Conflict from its record (following supersession to the
        CURRENT version, so status reflects the latest transition)."""
        rec = self._reader.get_current(conflict_id)
        if rec is None:
            rec = self._reader.get(conflict_id, include_deprecated=True,
                                   include_superseded=True)
        if rec is None or rec.record_type != RecordType.CONFLICT:
            return None
        return self._conflict_from_record(rec)

    def _conflict_from_record(self, rec: EmberRecord) -> Conflict:
        data = dict(rec.data or {})
        c = Conflict.from_dict({**data, "namespace": rec.namespace,
                                "tags": [t for t in rec.tags if t != "conflict"]})
        return c

    def _find_live_conflict(self, pair_fingerprint: str) -> Conflict | None:
        """The current, not-yet-closed conflict for a pair fingerprint, if any.
        'Live' = status not in {RESOLVED, SUPERSEDED} — a closed conflict does
        not block mapping a fresh one if the contradiction resurfaces."""
        closed = {ConflictStatus.RESOLVED, ConflictStatus.SUPERSEDED}
        for c in self.conflict_records(namespace=None):
            if c.pair_fingerprint() == pair_fingerprint and c.status not in closed:
                return c
        return None

    def conflict_records(self, namespace: str | None = None,
                         status: ConflictStatus | None = None) -> list[Conflict]:
        """All mapped conflicts, optionally filtered by namespace and/or status.

        Resolves each conflict lineage to its CURRENT version so status is
        accurate, and de-duplicates superseded copies of the same conflict."""
        out, seen = [], set()
        namespaces = ([namespace] if namespace is not None
                      else self._all_namespaces())
        for ns in namespaces:
            for rec in self._reader.get_namespace(ns, include_deprecated=True,
                                                  limit=None):
                if rec.record_type != RecordType.CONFLICT:
                    continue
                current = self._reader.get_current(rec.id) or rec
                if current.id in seen:
                    continue
                seen.add(current.id)
                c = self._conflict_from_record(current)
                if status is None or c.status == status:
                    out.append(c)
        return out

    def conflicts_for(self, memory_id: str,
                     include_closed: bool = False) -> list[Conflict]:
        """Every mapped Conflict that involves a given memory (either side).

        By default only live conflicts (not resolved/superseded); pass
        include_closed=True to see the full triage history for the memory."""
        closed = {ConflictStatus.RESOLVED, ConflictStatus.SUPERSEDED}
        out = []
        for c in self.conflict_records(
                namespace=self.get(memory_id, True, True).namespace
                if self.exists(memory_id) else None):
            if memory_id not in (c.memory_a, c.memory_b):
                continue
            if not include_closed and c.status in closed:
                continue
            out.append(c)
        return out

    def update_conflict_status(self, conflict_id: str,
                               status: ConflictStatus,
                               resolution: str = "",
                               changed_by: str = "system") -> tuple[str, str]:
        """Advance a conflict's lifecycle — as a NEW version (append-only, §7).

        A transition (open → investigating → resolved | accepted_both |
        superseded) supersedes the conflict record with a new version carrying
        the new status/resolution, so the full triage history is preserved.
        NEITHER contradicting memory is touched — resolving a conflict records a
        decision, it never deletes a memory. Returns (new_id, old_id)."""
        head = self._reader.get_current(conflict_id)
        if head is None or head.record_type != RecordType.CONFLICT:
            raise KeyError(f"Conflict {conflict_id} not found.")
        conflict = self._conflict_from_record(head)
        payload = conflict.to_record_payload()
        payload["status"] = ConflictStatus(status).value
        if resolution:
            payload["resolution"] = resolution
        # Supersede the CURRENT head (not the original id) so repeated
        # transitions extend one linear chain rather than forking the lineage.
        return self.update(
            head.id, payload, written_by=changed_by,
            creation_reason=f"conflict → {ConflictStatus(status).value}")

    def resolve_conflict(self, conflict_id: str, resolution: str,
                         changed_by: str = "system") -> tuple[str, str]:
        """Convenience: mark a conflict RESOLVED with a resolution note. Neither
        memory is deleted — the resolution is a recorded decision only."""
        return self.update_conflict_status(
            conflict_id, ConflictStatus.RESOLVED, resolution, changed_by)

    def _all_namespaces(self) -> list[str]:
        """Every namespace that currently has records — used to sweep for
        conflicts when no namespace filter is given."""
        namespaces = set()
        for rid in self._store.all_ids():
            rec = self._store.read(rid)
            if rec is not None:
                namespaces.add(rec.namespace)
        return list(namespaces)

    # ── Sessions (Feature #9) ──────────────────────────────────────────────────
    # Provenance (#3) already stamps `session_id` on every memory a session
    # produced, so get_by_session() answers "which memories came from session X?"
    # mechanically. Feature #9 makes the SESSION itself first-class: a bounded
    # period of agent activity with its own identity, lifecycle, task, and a
    # curated account of what it produced (discoveries / failures / memory_writes).
    #
    # The session record is append-only like everything else: ending a session,
    # or logging a discovery/failure/write against it, supersedes it with a new
    # version, so the session's history is preserved and auditable.

    def start_session(self, agent_id: str, task: str = "",
                      namespace: str = "default",
                      session_id: str | None = None) -> str:
        """Open a first-class session (status ACTIVE) and return its id.

        The returned id is what callers pass as `session_id` to write/propose so
        the session's memories are attributable to it (provenance, #3)."""
        session = Session(agent_id=agent_id, task=task, namespace=namespace,
                          **({"session_id": session_id} if session_id else {}))
        record = EmberRecord(
            id=session.session_id,
            namespace=namespace,
            record_type=RecordType.SESSION,
            data=session.to_record_payload(),
            written_by=agent_id,
            agent_id=agent_id,
            session_id=session.session_id,
            creation_reason="session started",
            tags=list(session.tags) + ["session"],
        )
        return self._writer.write(record)

    def get_session(self, session_id: str) -> Session | None:
        """Reconstruct a Session from its CURRENT version, so status/summary and
        the produced-ids lists reflect the latest transition."""
        rec = self._reader.get_current(session_id)
        if rec is None:
            rec = self._reader.get(session_id, include_deprecated=True,
                                   include_superseded=True)
        if rec is None or rec.record_type != RecordType.SESSION:
            return None
        return self._session_from_record(rec)

    def _session_from_record(self, rec: EmberRecord) -> Session:
        data = dict(rec.data or {})
        return Session.from_dict({**data, "namespace": rec.namespace,
                                  "tags": [t for t in rec.tags if t != "session"]})

    def _update_session(self, session: Session, changed_by: str,
                        reason: str) -> str:
        """Supersede a session with a new version (append-only). Returns the new
        record id; get_session() continues to resolve by the original id."""
        head = self._reader.get_current(session.session_id)
        if head is None or head.record_type != RecordType.SESSION:
            raise KeyError(f"Session {session.session_id} not found.")
        new_id, _ = self.update(head.id, session.to_record_payload(),
                                written_by=changed_by, creation_reason=reason)
        return new_id

    def record_discovery(self, session_id: str, discovery_id: str,
                         changed_by: str = "system") -> str:
        """Log a discovery/proposal id against a session's curated account."""
        session = self._require_session(session_id)
        if discovery_id not in session.discoveries:
            session.discoveries.append(discovery_id)
        return self._update_session(session, changed_by, "session discovery logged")

    def record_failure(self, session_id: str, failure_id: str,
                       changed_by: str = "system") -> str:
        """Log a failure id (§13) against a session's curated account."""
        session = self._require_session(session_id)
        if failure_id not in session.failures:
            session.failures.append(failure_id)
        return self._update_session(session, changed_by, "session failure logged")

    def record_memory_write(self, session_id: str, memory_id: str,
                            changed_by: str = "system") -> str:
        """Log a durable memory id against a session's curated account."""
        session = self._require_session(session_id)
        if memory_id not in session.memory_writes:
            session.memory_writes.append(memory_id)
        return self._update_session(session, changed_by, "session memory write logged")

    def end_session(self, session_id: str, summary: str = "",
                    status: SessionStatus = SessionStatus.COMPLETED,
                    changed_by: str = "system") -> str:
        """Close a session (COMPLETED or ABANDONED) with a summary and an
        `ended_at` timestamp — as a new append-only version."""
        session = self._require_session(session_id)
        session.status = SessionStatus(status)
        session.ended_at = datetime.utcnow()
        if summary:
            session.summary = summary
        return self._update_session(
            session, changed_by, f"session → {SessionStatus(status).value}")

    def _require_session(self, session_id: str) -> Session:
        session = self.get_session(session_id)
        if session is None:
            raise KeyError(f"Session {session_id} not found.")
        return session

    def sessions(self, agent_id: str | None = None,
                 namespace: str | None = None,
                 status: SessionStatus | None = None) -> list[Session]:
        """All sessions, optionally filtered by agent / namespace / status.
        Resolves each session lineage to its current version."""
        out, seen = [], set()
        namespaces = ([namespace] if namespace is not None
                      else self._all_namespaces())
        for ns in namespaces:
            for rec in self._reader.get_namespace(ns, include_deprecated=True,
                                                  limit=None):
                if rec.record_type != RecordType.SESSION:
                    continue
                current = self._reader.get_current(rec.id) or rec
                if current.id in seen:
                    continue
                seen.add(current.id)
                s = self._session_from_record(current)
                if agent_id is not None and s.agent_id != agent_id:
                    continue
                if status is not None and s.status != status:
                    continue
                out.append(s)
        return out

    def session_discoveries(self, session_id: str) -> list[EmberRecord]:
        """> What did this agent discover during session X?

        The proposals/discoveries the session recorded, resolved to records."""
        session = self._require_session(session_id)
        return self._resolve_ids(session.discoveries, True, True)

    def session_memories(self, session_id: str) -> list[EmberRecord]:
        """> Which memories were created during session X?

        Combines the session's own curated memory_writes list with the
        provenance view (every record stamped with this session_id), so a memory
        is found whether or not it was explicitly logged on the session."""
        ids = set(self.get_session(session_id).memory_writes)
        for rec in self.get_by_session(session_id, include_superseded=True):
            if rec.record_type not in (RecordType.SESSION,):
                ids.add(rec.id)
        return self._resolve_ids(ids, True, True)

    def session_failures(self, session_id: str) -> list[EmberRecord]:
        """> What failures occurred [during / before memory Y was created in]
        session X? — the failure records the session logged (§13)."""
        session = self._require_session(session_id)
        return self._resolve_ids(session.failures, True, True)

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
                validated_by: str = "system",
                status: "MemoryStatus | None" = None,
                promotion_method: "PromotionMethod | None" = None,
                ) -> tuple[str, str]:
        """Validate a proposal into a durable memory (§4 validation step).

        Writes a NEW durable memory (record_type NODE) carrying the discovery as
        its data and the proposal's justification as provenance: reason →
        creation_reason, derivation → derived_from, confidence → confidence.
        Each piece of evidence is written as its own hashed EVIDENCE record and
        linked to the memory with a SUPPORTS edge, so the memory's grounding is
        traceable and other agents can attach further evidence later without
        touching it. The proposal record is then superseded by a PROMOTED copy
        that records which memory it became.

        EPISTEMIC STATE (spec §12). A promoted memory does NOT assert "this is
        true" — only "this met the criteria to enter durable memory". So it
        carries two explicit fields, stored under reserved `_status` /
        `_promotion_method` keys INSIDE the memory's data (hence inside the
        content hash and versioned — a status change is a new version):
          • status           VERIFIED (default) / PROVISIONAL / DISPUTED
          • promotion_method HOW it was admitted — HUMAN by default, because a
                             bare promote() call is an explicit caller decision;
                             the Promotion Engine passes AUTOMATIC / CONSENSUS.
        Backwards-compat: the keys are added only when set, and read back with a
        VERIFIED / HUMAN default, so pre-existing promoted memories hash and read
        exactly as before (§15).

        Returns (memory_id, proposal_id). Raises if the proposal does not exist
        or is not currently pending.
        """
        from .core.types import MemoryStatus, PromotionMethod
        status = status or MemoryStatus.VERIFIED
        promotion_method = promotion_method or PromotionMethod.HUMAN

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
            data=self._with_status(proposal.discovery, status, promotion_method),
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

    # ── Promotion Engine (§10–§12) — configurable routing to durable memory ────

    @property
    def promotion_mode(self) -> PromotionMode:
        """The store's configured promotion mode (AUTOMATIC / CONSENSUS / HUMAN /
        HYBRID)."""
        return self._promotion.mode

    def submit(self, proposal_id: str,
               validated_by: str = "promotion-engine") -> "PromotionResult":
        """Run a pending proposal through the Promotion Engine.

        The engine DECIDES (per the configured mode + policy) whether the
        proposal meets the criteria to enter durable memory, and if so promotes
        it with the appropriate method + status. On a HOLD nothing is written and
        the proposal stays PENDING. Returns a PromotionResult (decision +
        memory_id when promoted)."""
        return self._promotion.submit(proposal_id, validated_by=validated_by)

    def promotion_route(self, proposal_id: str) -> "PromotionDecision":
        """Dry-run: what WOULD the Promotion Engine do with this proposal? Returns
        the decision without writing anything."""
        return self._promotion.route(proposal_id)

    # ── Epistemic status of a durable memory (§12) ─────────────────────────────

    @staticmethod
    def _with_status(discovery, status, promotion_method) -> dict:
        """Fold the memory's epistemic status + promotion method into its data.

        The discovery is normally a dict; we add reserved `_status` /
        `_promotion_method` keys alongside it. A non-dict discovery (str, list,
        number) is wrapped as {"value": <discovery>, ...} so the status still has
        somewhere to live without losing the original payload — `memory_status`
        / `get` unwrap it symmetrically."""
        meta = {
            "_status": status.value,
            "_promotion_method": promotion_method.value,
        }
        if isinstance(discovery, dict):
            merged = dict(discovery)
            merged.update(meta)
            return merged
        return {"value": discovery, **meta}

    def memory_status(self, memory_id: str) -> "MemoryStatus":
        """The current epistemic status of a durable memory.

        Reads the CURRENT version (status changes are new versions). Defaults to
        VERIFIED when the key is absent, so a memory written before this feature
        — or by a plain db.write() — reads as VERIFIED without any migration."""
        from .core.types import MemoryStatus
        rec = self._reader.get_current(memory_id) or self._reader.get(
            memory_id, include_deprecated=True, include_superseded=True)
        if rec is None:
            raise KeyError(f"Memory {memory_id} not found.")
        data = rec.data if isinstance(rec.data, dict) else {}
        return MemoryStatus(data.get("_status", MemoryStatus.VERIFIED.value))

    def promotion_method(self, memory_id: str) -> "PromotionMethod | None":
        """How a durable memory was admitted (AUTOMATIC / CONSENSUS / HUMAN).

        None when unknown — e.g. a memory created by a plain db.write() that
        never went through promotion."""
        from .core.types import PromotionMethod
        rec = self._reader.get_current(memory_id) or self._reader.get(
            memory_id, include_deprecated=True, include_superseded=True)
        if rec is None:
            raise KeyError(f"Memory {memory_id} not found.")
        data = rec.data if isinstance(rec.data, dict) else {}
        val = data.get("_promotion_method")
        return PromotionMethod(val) if val else None

    def set_status(self, memory_id: str, status: "MemoryStatus",
                   changed_by: str = "system",
                   reason: str | None = None) -> tuple[str, str]:
        """Change a memory's epistemic status — as a NEW version (append-only).

        A status transition (e.g. VERIFIED → DISPUTED when conflicting evidence
        appears) never overwrites: it supersedes the memory with a new version
        carrying the new `_status`, so the history verified→disputed is fully
        preserved and auditable. The promotion_method is carried forward
        unchanged. Returns (new_id, old_id)."""
        from .core.types import MemoryStatus, PromotionMethod
        rec = self._reader.get_current(memory_id)
        if rec is None:
            raise KeyError(f"Memory {memory_id} not found.")
        data = dict(rec.data) if isinstance(rec.data, dict) else {"value": rec.data}
        method = data.get("_promotion_method", PromotionMethod.HUMAN.value)
        data["_status"] = MemoryStatus(status).value
        data["_promotion_method"] = method
        return self.update(
            rec.id, data, written_by=changed_by,
            creation_reason=reason or f"status → {MemoryStatus(status).value}")

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
