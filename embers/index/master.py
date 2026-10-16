from embers._native import atomic_replace as _quota_atomic_replace
"""
Ember's Diaries — Master Index
O(1) lookups by record ID, namespace index, tag index.
Maintained in-memory with periodic persistence.
"""

import threading
import weakref
import hashlib
from collections import defaultdict
from pathlib import Path
from datetime import datetime

from ..storage.format import encode_index, decode_index


class MasterIndex:
    """
    In-memory index for fast record lookups.
    Persisted to disk on checkpoint. Rebuilt from store on startup.
    """

    _registry: "weakref.WeakValueDictionary[str, MasterIndex]" = weakref.WeakValueDictionary()
    _registry_lock = threading.RLock()

    def __init__(self, store_path: Path):
        self._path = store_path / "indexes"
        self._path.mkdir(parents=True, exist_ok=True)
        self._store_root = Path(store_path).resolve()
        self._lock = threading.RLock()
        with self._registry_lock:
            self._registry[str(self._store_root)] = self

        self._records: dict[str, dict] = {}
        self._namespaces: dict[str, set] = defaultdict(set)
        self._tags: dict[str, set] = defaultdict(set)
        self._superseded: dict[str, str] = {}
        self._supersedes: dict[str, str] = {}
        self._deprecated: set[str] = set()
        self._written_by: dict[str, set] = defaultdict(set)
        self._by_agent: dict[str, set] = defaultdict(set)
        self._by_session: dict[str, set] = defaultdict(set)

        # Branch-aware version graph.
        self._version_children: dict[str, set] = defaultdict(set)
        self._version_parent: dict[str, str] = {}
        self.indexed_epoch: int | None = None
        self._candidate_seal: str | None = None
        self._health_check = None

        self._load()

    @classmethod
    def get_registered(cls, store_path: str | Path) -> "MasterIndex | None":
        """Return the live MasterIndex for a store, if one is registered."""
        key = str(Path(store_path).resolve())
        with cls._registry_lock:
            return cls._registry.get(key)

    def _load(self):
        """Load persisted index from disk."""
        index_file = self._path / "master.json"
        if not index_file.exists():
            return
        try:
            data = decode_index(index_file.read_bytes())
            self.reset()
            epoch = data.get("indexed_epoch")
            if epoch is not None and (type(epoch) is not int or epoch < 0):
                raise ValueError("Invalid MasterIndex epoch")
            self.indexed_epoch = epoch
            for rid, meta in data.get("records", {}).items():
                self._records[rid] = meta
                self._namespaces[meta["namespace"]].add(rid)
                for tag in meta.get("tags", []):
                    self._tags[tag].add(rid)
                if meta.get("written_by"):
                    self._written_by[meta["written_by"]].add(rid)
                if meta.get("agent_id"):
                    self._by_agent[meta["agent_id"]].add(rid)
                if meta.get("session_id"):
                    self._by_session[meta["session_id"]].add(rid)
                if meta.get("supersedes"):
                    self._link_version(meta["supersedes"], rid)
            for old_id, new_id in data.get("superseded", {}).items():
                self._superseded[old_id] = new_id
                self._supersedes[new_id] = old_id
            for old_id, new_id in self._superseded.items():
                self._link_version(old_id, new_id)
            self._deprecated = set(data.get("deprecated", []))
            self._candidate_seal = self._candidate_fingerprint()
        except Exception as e:
            self.reset()
            print(f"[MasterIndex] Failed to load: {e}")

    def reset(self):
        """Discard every derived posting before a source-backed rebuild."""
        with self._lock:
            self._records.clear()
            self._namespaces.clear()
            self._tags.clear()
            self._superseded.clear()
            self._supersedes.clear()
            self._deprecated.clear()
            self._written_by.clear()
            self._by_agent.clear()
            self._by_session.clear()
            self._version_children.clear()
            self._version_parent.clear()
            self.indexed_epoch = None
            self._candidate_seal = None

    def _candidate_fingerprint(self) -> str:
        """Detect unexpected in-memory type/namespace posting edits."""
        rows = sorted((rid, meta.get("namespace"), meta.get("record_type"))
                      for rid, meta in self._records.items())
        namespaces = sorted((ns, tuple(sorted(ids)))
                            for ns, ids in self._namespaces.items())
        return hashlib.sha256(repr((rows, namespaces)).encode("utf-8")).hexdigest()

    def reload(self):
        self.reset()
        self._load()

    def ensure_current(self):
        if self._health_check is not None:
            self._health_check()

    def persist(self, indexed_epoch: int | None = None):
        """Save index to disk."""
        with self._lock:
            published_epoch = (self.indexed_epoch if indexed_epoch is None
                               else indexed_epoch)
            data = {
                "records": self._records,
                "superseded": self._superseded,
                "deprecated": list(self._deprecated),
                "indexed_epoch": published_epoch,
                "persisted_at": datetime.utcnow().isoformat(),
            }
            index_file = self._path / "master.json"
            _quota_atomic_replace(str(index_file), encode_index(data))
            self.indexed_epoch = published_epoch
            self._candidate_seal = self._candidate_fingerprint()

    def index_record(self, record_id: str, namespace: str, record_type: str,
                     created_at: str, tags: list[str], written_by: str = "system",
                     agent_id: str | None = None, session_id: str | None = None,
                     supersedes: str | None = None, **extra):
        """Add a record to all indexes."""
        with self._lock:
            meta = {
                "namespace": namespace,
                "record_type": record_type,
                "created_at": created_at,
                "tags": tags,
                "written_by": written_by,
                "agent_id": agent_id,
                "session_id": session_id,
                "supersedes": supersedes,
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
            if supersedes:
                self._link_version(supersedes, record_id)

    def mark_superseded(self, old_id: str, new_id: str):
        with self._lock:
            self._superseded[old_id] = new_id
            self._supersedes[new_id] = old_id
            self._link_version(old_id, new_id)

    def _link_version(self, parent_id: str, child_id: str):
        with self._lock:
            self._version_children[parent_id].add(child_id)
            self._version_parent[child_id] = parent_id

    def mark_deprecated(self, record_id: str):
        with self._lock:
            self._deprecated.add(record_id)

    def get_meta(self, record_id: str) -> dict | None:
        self.ensure_current()
        return self._records.get(record_id)

    def get_namespace_ids(self, namespace: str,
                          include_deprecated: bool = False,
                          include_superseded: bool = False) -> list[str]:
        self.ensure_current()
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
        self.ensure_current()
        return self._tags.get(tag, set()).copy()

    def get_by_tags(self, tags: list[str], match_all: bool = False) -> set[str]:
        self.ensure_current()
        if not tags:
            return set()
        tag_sets = [self._tags.get(t, set()) for t in tags]
        if match_all:
            return set.intersection(*tag_sets) if tag_sets else set()
        return set.union(*tag_sets) if tag_sets else set()

    def get_by_author(self, written_by: str) -> set[str]:
        self.ensure_current()
        return self._written_by.get(written_by, set()).copy()

    def get_by_agent(self, agent_id: str) -> set[str]:
        self.ensure_current()
        return self._by_agent.get(agent_id, set()).copy()

    def has_agent(self, agent_id: str) -> bool:
        self.ensure_current()
        """Feature #8 — has this agent written anything to this store?"""
        return agent_id in self._by_agent

    def agents(self) -> list[str]:
        self.ensure_current()
        """Feature #8 — every agent identity present in the write index.

        Derived from the index that also powers get_by_agent(), so it reports
        actual write history. Adding an agent is just a new value here — no
        storage-layer change (spec §8)."""
        return sorted(self._by_agent.keys())

    def get_by_session(self, session_id: str) -> set[str]:
        self.ensure_current()
        return self._by_session.get(session_id, set()).copy()

    def is_superseded(self, record_id: str) -> bool:
        self.ensure_current()
        return record_id in self._superseded

    def get_superseded_by(self, record_id: str) -> str | None:
        self.ensure_current()
        return self._superseded.get(record_id)

    def is_deprecated(self, record_id: str) -> bool:
        self.ensure_current()
        return record_id in self._deprecated

    def get_supersession_chain(self, record_id: str) -> list[str]:
        self.ensure_current()
        current = record_id
        seen = {current}
        while current in self._supersedes:
            prev = self._supersedes[current]
            if prev in seen:
                break
            seen.add(prev)
            current = prev
        root = current
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

    def get_version_children(self, record_id: str) -> set[str]:
        self.ensure_current()
        return self._version_children.get(record_id, set()).copy()

    def get_version_parent(self, record_id: str) -> str | None:
        self.ensure_current()
        return self._version_parent.get(record_id)

    def get_version_root(self, record_id: str) -> str:
        self.ensure_current()
        current = record_id
        seen = {current}
        while current in self._version_parent:
            parent = self._version_parent[current]
            if parent in seen:
                break
            seen.add(parent)
            current = parent
        return current

    def get_version_ancestors(self, record_id: str) -> list[str]:
        out: list[str] = []
        current = self._version_parent.get(record_id)
        seen = {record_id}
        while current and current not in seen:
            seen.add(current)
            out.append(current)
            current = self._version_parent.get(current)
        return out

    def get_version_descendants(self, record_id: str) -> set[str]:
        out: set[str] = set()
        stack = [record_id]
        seen = {record_id}
        while stack:
            node = stack.pop()
            for child in self._version_children.get(node, set()):
                if child not in seen:
                    seen.add(child)
                    out.add(child)
                    stack.append(child)
        return out

    def _lineage_nodes(self, record_id: str) -> set[str]:
        root = self.get_version_root(record_id)
        return {root} | self.get_version_descendants(root)

    def get_version_heads(self, record_id: str) -> set[str]:
        return {n for n in self._lineage_nodes(record_id)
                if not self._version_children.get(n)}

    def get_branch_points(self, record_id: str) -> set[str]:
        return {n for n in self._lineage_nodes(record_id)
                if len(self._version_children.get(n, set())) > 1}

    def get_version_tree(self, record_id: str) -> dict[str, list[str]]:
        return {n: sorted(self._version_children.get(n, set()))
                for n in self._lineage_nodes(record_id)}

    def all_ids(self) -> list[str]:
        self.ensure_current()
        return list(self._records.keys())

    def get_type_ids(self, record_type: str, namespace: str | None = None) -> list[str]:
        """Source-certified candidates from the existing MasterIndex rows."""
        self.ensure_current()
        if self._candidate_seal != self._candidate_fingerprint():
            raise ValueError("MasterIndex conflict candidate metadata changed without a source epoch")
        ids = self._namespaces.get(namespace, set()) if namespace is not None else self._records
        return [rid for rid in ids if self._records[rid]["record_type"] == record_type]

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
            "version_links": len(self._version_parent),
            "branch_points": sum(1 for c in self._version_children.values()
                                 if len(c) > 1),
        }
