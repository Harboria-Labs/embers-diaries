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
from .core.types import RecordType, DeprecationReason, AccessLevel
from .core.edge import EdgeRef
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
            written_by=record.written_by)

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

    # ── Write ─────────────────────────────────────────────────────────────────

    def write(self, record: EmberRecord) -> str:
        """
        Write a new record. Returns the record ID.
        The record is permanent — it can never be deleted.
        """
        return self._writer.write(record)

    def update(self, record_id: str, new_data: dict,
               written_by: str = "system") -> tuple[str, str]:
        """
        Create a new version of an existing record.
        Old record is preserved (superseded, never deleted).
        Returns (new_id, old_id).
        """
        result = self._writer.update(record_id, new_data, written_by)
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
