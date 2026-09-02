"""
Ember's Diaries — Conflict (Feature #7: Conflict Engine)

A first-class record of a SEMANTIC contradiction between two durable memories:

    Memory A: "The system uses PostgreSQL."
    Memory B: "The system uses MongoDB."

The spec's rule is absolute: *do not silently delete conflicting memories.* Both
stay in the store; the conflict between them is made explicit, given an identity
and a lifecycle, and reconciled deliberately (by a human or a later agent) —
never by destroying one side.

This object is the spec's §7 shape:

    Conflict
    ├── memory_a
    ├── memory_b
    ├── detected_at
    ├── detected_by
    ├── conflict_type
    ├── status
    └── resolution

It follows the same self-hashing, append-only pattern as Evidence and
MemoryProposal: a Conflict is stored as its own CONFLICT record, and a status
transition supersedes it with a new version (history preserved).

CANONICAL PAIR ORDERING. A conflict between A and B is the same conflict as
between B and A — it is symmetric. So the pair is stored order-independently
(sorted), and the conflict's identity hash is computed over the sorted pair.
That makes "is there already a conflict mapped between these two?" a stable,
direction-free question and prevents mapping the same contradiction twice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import uuid

from .types import ConflictType, ConflictStatus
from .integrity import content_hash


@dataclass
class Conflict:
    """A mapped contradiction between two memories. Never destroys either side."""

    # ── Identity ───────────────────────────────────────────────────────────────
    conflict_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    namespace: str = "default"

    # ── The two contradicting memories (spec §7) ───────────────────────────────
    memory_a: str = ""                                 # record id
    memory_b: str = ""                                 # record id

    # ── Classification & detection ──────────────────────────────────────────────
    conflict_type: ConflictType = ConflictType.SEMANTIC
    detected_at: datetime = field(default_factory=datetime.utcnow)
    detected_by: str = "system"                        # who/what flagged it

    # ── Lifecycle ──────────────────────────────────────────────────────────────
    status: ConflictStatus = ConflictStatus.OPEN
    resolution: str = ""                               # how it was reconciled
    note: str = ""                                     # free-form context
    tags: list = field(default_factory=list)

    def __post_init__(self):
        # Symmetric: store the pair in a canonical (sorted) order so the same
        # contradiction always yields the same pair regardless of which side was
        # passed first. A conflict of a memory with itself is nonsensical.
        if self.memory_a == self.memory_b:
            raise ValueError("A memory cannot conflict with itself.")
        if self.memory_b < self.memory_a:
            self.memory_a, self.memory_b = self.memory_b, self.memory_a

    def pair_fingerprint(self) -> str:
        """Stable, direction-free identity of the CONTRADICTING PAIR (not the
        conflict record). Used to detect that a conflict between these two
        memories is already mapped, so we never map it twice."""
        return content_hash({
            "namespace": self.namespace,
            "memory_a": self.memory_a,
            "memory_b": self.memory_b,
            "conflict_type": self.conflict_type.value,
        })

    def to_record_payload(self) -> dict:
        """The `data` payload stored on the CONFLICT record. The pair + type +
        detection facts travel into the record's content hash; status/resolution
        are mutable via append-only supersession (a new version per transition)."""
        return {
            "conflict_id": self.conflict_id,
            "memory_a": self.memory_a,
            "memory_b": self.memory_b,
            "conflict_type": self.conflict_type.value,
            "detected_at": self.detected_at.isoformat(),
            "detected_by": self.detected_by,
            "status": self.status.value,
            "resolution": self.resolution,
            "note": self.note,
            "pair_fingerprint": self.pair_fingerprint(),
        }

    def to_dict(self) -> dict:
        d = self.to_record_payload()
        d["namespace"] = self.namespace
        d["tags"] = list(self.tags)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Conflict":
        return cls(
            conflict_id   = d.get("conflict_id", str(uuid.uuid4())),
            namespace     = d.get("namespace", "default"),
            memory_a      = d["memory_a"],
            memory_b      = d["memory_b"],
            conflict_type = ConflictType(d.get("conflict_type", "semantic")),
            detected_at   = datetime.fromisoformat(d["detected_at"]) if d.get("detected_at") else datetime.utcnow(),
            detected_by   = d.get("detected_by", "system"),
            status        = ConflictStatus(d.get("status", "open")),
            resolution    = d.get("resolution", ""),
            note          = d.get("note", ""),
            tags          = d.get("tags", []),
        )
