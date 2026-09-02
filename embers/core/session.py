"""
Ember's Diaries — Session (Feature #9: first-class session tracking)

A session is a BOUNDED PERIOD OF AGENT ACTIVITY. Provenance (Feature #3) already
stamps a `session_id` onto every memory a session produced, so "which memories
came from session X?" is answerable. But §9 asks for more: the session itself
becomes a first-class, queryable object with its own identity, lifecycle, and
summary — not just a foreign key scattered across memories.

The spec's §9 shape:

    Session
    ├── session_id
    ├── agent_id
    ├── started_at
    ├── ended_at
    ├── task
    ├── status
    ├── summary
    ├── discoveries      — memory/proposal ids the session produced
    ├── failures         — failure ids recorded during the session (§13)
    └── memory_writes    — durable memory ids written during the session

It follows the same self-hashing, append-only pattern as Conflict / Evidence /
MemoryProposal: a Session is stored as its own SESSION record, and a lifecycle
transition (active → completed | abandoned) supersedes it with a new version so
the full history is preserved.

WHY STORE discoveries / failures / memory_writes ON THE SESSION when provenance
already links them by session_id? Two reasons. (1) It lets the session answer
"what did I produce?" without a full-store scan, and (2) it captures the
agent's OWN account of the session — a curated summary of what mattered — which
is distinct from the mechanical set of every record that happens to carry the
id. Both views are kept: the mechanical one via `db.get_by_session()`, the
curated one on the session record.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import uuid

from .types import SessionStatus


@dataclass
class Session:
    """A bounded period of agent activity (spec §9)."""

    # ── Identity ───────────────────────────────────────────────────────────────
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    namespace: str = "default"
    agent_id: str = "system"

    # ── Bounds & task (spec §9) ─────────────────────────────────────────────────
    started_at: datetime = field(default_factory=datetime.utcnow)
    ended_at: datetime | None = None
    task: str = ""

    # ── Lifecycle ──────────────────────────────────────────────────────────────
    status: SessionStatus = SessionStatus.ACTIVE
    summary: str = ""

    # ── What the session produced (spec §9) ─────────────────────────────────────
    discoveries: list = field(default_factory=list)    # proposal/discovery ids
    failures: list = field(default_factory=list)       # failure ids (§13)
    memory_writes: list = field(default_factory=list)  # durable memory ids

    tags: list = field(default_factory=list)

    def is_open(self) -> bool:
        return self.status == SessionStatus.ACTIVE

    def to_record_payload(self) -> dict:
        """The `data` payload stored on the SESSION record. The whole session
        account travels into the record's content hash, so a session's own
        record of what it produced is tamper-evident and versioned."""
        return {
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "task": self.task,
            "status": self.status.value,
            "summary": self.summary,
            "discoveries": list(self.discoveries),
            "failures": list(self.failures),
            "memory_writes": list(self.memory_writes),
        }

    def to_dict(self) -> dict:
        d = self.to_record_payload()
        d["namespace"] = self.namespace
        d["tags"] = list(self.tags)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        return cls(
            session_id    = d.get("session_id", str(uuid.uuid4())),
            namespace     = d.get("namespace", "default"),
            agent_id      = d.get("agent_id", "system"),
            started_at    = datetime.fromisoformat(d["started_at"]) if d.get("started_at") else datetime.utcnow(),
            ended_at      = datetime.fromisoformat(d["ended_at"]) if d.get("ended_at") else None,
            task          = d.get("task", ""),
            status        = SessionStatus(d.get("status", "active")),
            summary       = d.get("summary", ""),
            discoveries   = d.get("discoveries", []),
            failures      = d.get("failures", []),
            memory_writes = d.get("memory_writes", []),
            tags          = d.get("tags", []),
        )
