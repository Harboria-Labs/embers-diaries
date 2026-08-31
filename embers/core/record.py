"""
Ember's Diaries — EmberRecord
The atomic unit of the entire system.
Every piece of data ever stored is an EmberRecord.
Records are permanent. They are never modified after creation.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import uuid

from .types import RecordType, DeprecationReason
from .edge import EdgeRef
from .annotation import Annotation
from .integrity import RecordIntegrityError, content_hash


@dataclass
class EmberRecord:
    """
    The universal storage unit.

    One record can be a document, a graph node, a graph edge,
    a time-series point, a vector embedding, or raw binary.
    The record_type field determines how it is indexed and queried.

    Records are IMMUTABLE after creation.
    - No UPDATE: write a new record and mark the old as superseded
    - No DELETE: deprecate the record (it stays in the store forever)
    - Changes: add Annotations (never touch the original data)
    """

    # ── Identity ─────────────────────────────────────────────────────────────
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    namespace: str = "default"
    record_type: RecordType = RecordType.DOCUMENT
    version: int = 1
    parent_hash: str | None = None
    content_hash: str | None = None

    # ── Payload — stores anything ─────────────────────────────────────────────
    data: Any = None  # dict | list | str | bytes | float | None

    # ── Time — always first-class ─────────────────────────────────────────────
    created_at: datetime = field(default_factory=datetime.utcnow)
    valid_from: datetime | None = None    # When this record becomes logically active
    valid_until: datetime | None = None   # When this record becomes logically inactive

    # ── Supersession chain ────────────────────────────────────────────────────
    superseded_by: str | None = None      # ID of newer record (if this has been updated)
    supersedes: str | None = None         # ID of the record this replaces

    # ── Deprecation ───────────────────────────────────────────────────────────
    deprecated: bool = False
    deprecated_at: datetime | None = None
    deprecation_reason: DeprecationReason | None = None
    deprecation_note: str = ""

    # ── Graph ─────────────────────────────────────────────────────────────────
    connections: list = field(default_factory=list)  # list[EdgeRef]
    parent_id: str | None = None

    # ── Vector ────────────────────────────────────────────────────────────────
    embedding: list | None = None         # list[float] for semantic similarity

    # ── Annotations — never overwrites, only adds ─────────────────────────────
    annotations: list = field(default_factory=list)  # list[Annotation]

    # ── Health ────────────────────────────────────────────────────────────────
    confidence: float = 1.0              # 0.0 - 1.0
    decay_rate: float = 0.0              # How confidence drops without reinforcement
    access_count: int = 0
    last_accessed: datetime | None = None

    # ── Source ────────────────────────────────────────────────────────────────
    written_by: str = "system"           # system | agent_id | "sammie"
    origin: str | None = None            # Where the data came from

    # ── Tags ─────────────────────────────────────────────────────────────────
    tags: list = field(default_factory=list)
    schema_version: str = "1.0"

    # ── Training metadata ────────────────────────────────────────────────────
    training_candidate: bool = False     # Mark for fine-tuning corpus
    retrieval_candidate: bool = True     # Include in retrieval results

    # ── Properties ───────────────────────────────────────────────────────────

    @property
    def is_current(self) -> bool:
        """True if this record has not been superseded."""
        return self.superseded_by is None

    @property
    def is_active(self) -> bool:
        """True if not deprecated and within valid time window."""
        if self.deprecated:
            return False
        now = datetime.now(timezone.utc)
        if self.valid_from:
            vf = self.valid_from
            # Normalize: if stored datetime is naive, treat as UTC
            if vf.tzinfo is None:
                vf = vf.replace(tzinfo=timezone.utc)
            if now < vf:
                return False
        if self.valid_until:
            vu = self.valid_until
            if vu.tzinfo is None:
                vu = vu.replace(tzinfo=timezone.utc)
            if now > vu:
                return False
        return True

    @property
    def is_head(self) -> bool:
        """True if this is the latest version (not superseded, not deprecated)."""
        return self.is_current and not self.deprecated

    def age_seconds(self) -> float:
        return (datetime.now(timezone.utc) - self.created_at).total_seconds()

    def canonical_hash_payload(self) -> dict:
        """Return immutable state covered by the record's SHA-256 identity."""
        return {
            "record_id": self.id,
            "version": self.version,
            "parent_hash": self.parent_hash,
            "namespace": self.namespace,
            "record_type": self.record_type.value,
            "data": self.data,
            "created_at": self.created_at.isoformat(),
            "valid_from": self.valid_from.isoformat() if self.valid_from else None,
            "valid_until": self.valid_until.isoformat() if self.valid_until else None,
            "supersedes": self.supersedes,
            "connections": [edge.to_dict() for edge in self.connections],
            "parent_id": self.parent_id,
            "embedding": self.embedding,
            "confidence": self.confidence,
            "decay_rate": self.decay_rate,
            "written_by": self.written_by,
            "origin": self.origin,
            "tags": self.tags,
            "schema_version": self.schema_version,
            "training_candidate": self.training_candidate,
            "retrieval_candidate": self.retrieval_candidate,
        }

    def compute_content_hash(self) -> str:
        return content_hash(self.canonical_hash_payload())

    def seal(self) -> str:
        """Compute and attach the immutable content identity."""
        computed = self.compute_content_hash()
        if self.content_hash is not None and self.content_hash != computed:
            raise RecordIntegrityError(
                f"Record {self.id} content does not match its existing hash"
            )
        self.content_hash = computed
        return computed

    def verify_integrity(self) -> bool:
        if self.content_hash is None:
            return False
        computed = self.compute_content_hash()
        if computed != self.content_hash:
            raise RecordIntegrityError(
                f"Record {self.id} failed integrity verification: "
                f"expected {self.content_hash}, computed {computed}"
            )
        return True

    # ── Serialization ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Convert to a plain dict for storage serialization."""
        return {
            "id":               self.id,
            "namespace":        self.namespace,
            "record_type":      self.record_type.value,
            "version":          self.version,
            "parent_hash":      self.parent_hash,
            "content_hash":     self.content_hash,
            "data":             self.data,
            "created_at":       self.created_at.isoformat(),
            "valid_from":       self.valid_from.isoformat() if self.valid_from else None,
            "valid_until":      self.valid_until.isoformat() if self.valid_until else None,
            "superseded_by":    self.superseded_by,
            "supersedes":       self.supersedes,
            "deprecated":       self.deprecated,
            "deprecated_at":    self.deprecated_at.isoformat() if self.deprecated_at else None,
            "deprecation_reason": self.deprecation_reason.value if self.deprecation_reason else None,
            "deprecation_note": self.deprecation_note,
            "connections":      [e.to_dict() for e in self.connections],
            "parent_id":        self.parent_id,
            "embedding":        self.embedding,
            "annotations":      [a.to_dict() for a in self.annotations],
            "confidence":       self.confidence,
            "decay_rate":       self.decay_rate,
            "access_count":     self.access_count,
            "last_accessed":    self.last_accessed.isoformat() if self.last_accessed else None,
            "written_by":       self.written_by,
            "origin":           self.origin,
            "tags":             self.tags,
            "schema_version":   self.schema_version,
            "training_candidate":  self.training_candidate,
            "retrieval_candidate": self.retrieval_candidate,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EmberRecord":
        """Reconstruct a record from a plain dict."""
        from .types import DeprecationReason

        return cls(
            id             = d["id"],
            namespace      = d.get("namespace", "default"),
            record_type    = RecordType(d["record_type"]),
            version        = d.get("version", 1),
            parent_hash    = d.get("parent_hash"),
            content_hash   = d.get("content_hash"),
            data           = d.get("data"),
            created_at     = datetime.fromisoformat(d["created_at"]),
            valid_from     = datetime.fromisoformat(d["valid_from"]) if d.get("valid_from") else None,
            valid_until    = datetime.fromisoformat(d["valid_until"]) if d.get("valid_until") else None,
            superseded_by  = d.get("superseded_by"),
            supersedes     = d.get("supersedes"),
            deprecated     = d.get("deprecated", False),
            deprecated_at  = datetime.fromisoformat(d["deprecated_at"]) if d.get("deprecated_at") else None,
            deprecation_reason = DeprecationReason(d["deprecation_reason"]) if d.get("deprecation_reason") else None,
            deprecation_note   = d.get("deprecation_note", ""),
            connections    = [EdgeRef.from_dict(e) for e in d.get("connections", [])],
            parent_id      = d.get("parent_id"),
            embedding      = d.get("embedding"),
            annotations    = [Annotation.from_dict(a) for a in d.get("annotations", [])],
            confidence     = d.get("confidence", 1.0),
            decay_rate     = d.get("decay_rate", 0.0),
            access_count   = d.get("access_count", 0),
            last_accessed  = datetime.fromisoformat(d["last_accessed"]) if d.get("last_accessed") else None,
            written_by     = d.get("written_by", "system"),
            origin         = d.get("origin"),
            tags           = d.get("tags", []),
            schema_version = d.get("schema_version", "1.0"),
            training_candidate  = d.get("training_candidate", False),
            retrieval_candidate = d.get("retrieval_candidate", True),
        )

    def __repr__(self) -> str:
        status = "active" if self.is_active else ("deprecated" if self.deprecated else "superseded")
        return f"EmberRecord(id={self.id[:8]}..., type={self.record_type.value}, ns={self.namespace}, status={status})"
