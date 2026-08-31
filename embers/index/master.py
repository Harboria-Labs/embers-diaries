"""
Ember's Diaries — Master Index
O(1) lookups by record ID, namespace index, tag index.
Maintained in-memory with periodic persistence.
"""

import threading
from collections import defaultdict
from pathlib import Path
from datetime import datetime

from ..storage.format import encode_index, decode_index


class MasterIndex:
    """
    In-memory index for fast record lookups.
    Persisted to disk on checkpoint. Rebuilt from store on startup.
    
    Indexes maintained:
    - id → record metadata (namespace, type, created_at, tags, deprecated, superseded)
    - namespace → set of record IDs
    - tag → set of record IDs
    - supersession chains
    - deprecation set
    """

    def __init__(self, store_path: Path):
        self._path = store_path / "indexes"
        self._path.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

        # Core indexes
        self._records: dict[str, dict] = {}           # id → metadata
        self._namespaces: dict[str, set] = defaultdict(set)  # ns → {ids}
        self._tags: dict[str, set] = defaultdict(set)        # tag → {ids}
        self._superseded: dict[str, str] = {}          # old_id → new_id
        self._supersedes: dict[str, str] = {}          # new_id → old_id
        self._deprecated: set[str] = set()
        self._written_by: dict[str, set] = defaultdict(set)  # author → {ids}
        self._by_agent: dict[str, set] = defaultdict(set)    # agent_id → {ids}   (Feature #3)
        self._by_session: dict[str, set] = defaultdict(set)  # session_id → {ids} (Feature #3)

        self._load()

    def _load(self):
        """Load persisted index from disk."""
        index_file = self._path / "master.json"
        if not index_file.exists():
            return
        try:
            data = decode_index(index_file.read_bytes())
            for rid, meta in data.get("records", {}).items():
                self._records[rid] = meta
                self._namespaces[meta["namespace"]].add(rid)
                for tag in meta.get("tags", []):
                    self._tags[tag].add(rid)
                if meta.get("written_by"):
                    self._written_by[meta["written_by"]].add(rid)
                # Provenance indexes (Feature #3). Rebuilt from each record's
                # meta, so an index persisted before provenance existed simply
                # has no agent_id/session_id keys and contributes nothing here.
                if meta.get("agent_id"):
                    self._by_agent[meta["agent_id"]].add(rid)
                if meta.get("session_id"):
                    self._by_session[meta["session_id"]].add(rid)
            for old_id, new_id in data.get("superseded", {}).items():
                self._superseded[old_id] = new_id
                self._supersedes[new_id] = old_id
            self._deprecated = set(data.get("deprecated", []))
        except Exception as e:
            print(f"[MasterIndex] Failed to load: {e}")

    def persist(self):
        """Save index to disk."""
        with self._lock:
            data = {
                "records": self._records,
                "superseded": self._superseded,
                "deprecated": list(self._deprecated),
                "persisted_at": datetime.utcnow().isoformat(),
            }
            index_file = self._path / "master.json"
            index_file.write_bytes(encode_index(data))

    # ── Index operations ──────────────────────────────────────────────────────

    def index_record(self, record_id: str, namespace: str, record_type: str,
                     created_at: str, tags: list[str], written_by: str = "system",
                     agent_id: str | None = None, session_id: str | None = None,
                     **extra):
        """Add a record to all indexes.

        agent_id / session_id (Feature #3 provenance) are stored in the record's
        meta and mirrored into dedicated lookup indexes so 'which agent wrote
        this?' and 'what came out of this session?' are O(1) set lookups.
        """
        with self._lock:
            meta = {
                "namespace": namespace,
                "record_type": record_type,
                "created_at": created_at,
                "tags": tags,
                "written_by": written_by,
                "agent_id": agent_id,
                "session_id": session_id,
                **extra,
            }
            self._records[record_id] = meta
            self._namespaces[namespace].add(record_id)
            for tag in tags:
                self._tags[tag].add(record_id)
            if written_by:
                self._written_by[written_by].add(record_id)
            if agent_id:
                self._by_agent[agent_id].add(record_id)
            if session_id:
                self._by_session[session_id].add(record_id)

    def mark_superseded(self, old_id: str, new_id: str):
        with self._lock:
            self._superseded[old_id] = new_id
            self._supersedes[new_id] = old_id

    def mark_deprecated(self, record_id: str):
        with self._lock:
            self._deprecated.add(record_id)

    # ── Lookups ───────────────────────────────────────────────────────────────

    def get_meta(self, record_id: str) -> dict | None:
        return self._records.get(record_id)

    def get_namespace_ids(self, namespace: str,
                          include_deprecated: bool = False,
                          include_superseded: bool = False) -> list[str]:
        ids = self._namespaces.get(namespace, set())
        result = []
        for rid in ids:
            if not include_deprecated and rid in self._deprecated:
                continue
            if not include_superseded and rid in self._superseded:
                continue
            result.append(rid)
        return result

    def get_by_tag(self, tag: str) -> set[str]:
        return self._tags.get(tag, set()).copy()

    def get_by_tags(self, tags: list[str], match_all: bool = False) -> set[str]:
        if not tags:
            return set()
        tag_sets = [self._tags.get(t, set()) for t in tags]
        if match_all:
            return set.intersection(*tag_sets) if tag_sets else set()
        return set.union(*tag_sets) if tag_sets else set()

    def get_by_author(self, written_by: str) -> set[str]:
        return self._written_by.get(written_by, set()).copy()

    def get_by_agent(self, agent_id: str) -> set[str]:
        """All record IDs written by a specific agent identity (Feature #3)."""
        return self._by_agent.get(agent_id, set()).copy()

    def get_by_session(self, session_id: str) -> set[str]:
        """All record IDs written during a specific session (Feature #3)."""
        return self._by_session.get(session_id, set()).copy()

    def is_superseded(self, record_id: str) -> bool:
        return record_id in self._superseded

    def get_superseded_by(self, record_id: str) -> str | None:
        return self._superseded.get(record_id)

    def is_deprecated(self, record_id: str) -> bool:
        return record_id in self._deprecated

    def get_supersession_chain(self, record_id: str) -> list[str]:
        """Follow chain from oldest to newest."""
        # Walk backward to find the root
        current = record_id
        seen = {current}
        while current in self._supersedes:
            prev = self._supersedes[current]
            if prev in seen:
                break
            seen.add(prev)
            current = prev
        root = current

        # Walk forward from root
        chain = [root]
        current = root
        seen2 = {current}
        while current in self._superseded:
            nxt = self._superseded[current]
            if nxt in seen2:
                break
            seen2.add(nxt)
            chain.append(nxt)
            current = nxt
        return chain

    def all_ids(self) -> list[str]:
        return list(self._records.keys())

    def record_count(self) -> int:
        return len(self._records)

    def namespace_count(self) -> int:
        return len(self._namespaces)

    def stats(self) -> dict:
        return {
            "total_records": len(self._records),
            "namespaces": len(self._namespaces),
            "tags": len(self._tags),
            "superseded": len(self._superseded),
            "deprecated": len(self._deprecated),
        }
