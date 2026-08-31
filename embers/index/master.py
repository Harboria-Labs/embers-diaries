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

        # Version graph (Feature #2). Built from each record's immutable
        # `supersedes` pointer (child → parent). Unlike the 1:1 `_superseded`
        # map above — which keeps only the LATEST successor so get_current()
        # stays cheap — this captures BRANCHES: a single parent may be
        # superseded by several children (V1 → {V2-A, V2-B}), because the
        # append-only store physically keeps every child that points back to it.
        self._version_children: dict[str, set] = defaultdict(set)  # parent → {child ids}
        self._version_parent: dict[str, str] = {}                  # child → single parent id

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
                # Version graph (Feature #2): recover the branch-aware lineage
                # from each record's own supersedes pointer.
                if meta.get("supersedes"):
                    self._link_version(meta["supersedes"], rid)
            for old_id, new_id in data.get("superseded", {}).items():
                self._superseded[old_id] = new_id
                self._supersedes[new_id] = old_id
            # Also seed the version graph from the persisted linear map, so a
            # store written before Feature #2 (whose metas carry no `supersedes`
            # key) still gets its lineage back. Pre-#2 stores were necessarily
            # linear, so the 1:1 map reconstructs them exactly; for #2-era
            # stores this is redundant with the per-record links above (the
            # helper is idempotent) and only fills any gap the metas missed.
            for old_id, new_id in self._superseded.items():
                self._link_version(old_id, new_id)
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
                     supersedes: str | None = None,
                     **extra):
        """Add a record to all indexes.

        agent_id / session_id (Feature #3 provenance) are stored in the record's
        meta and mirrored into dedicated lookup indexes so 'which agent wrote
        this?' and 'what came out of this session?' are O(1) set lookups.

        supersedes (Feature #2) is the ID of the record this one replaces. It is
        persisted in meta and wired into the branch-aware version graph, so
        'what versions branch from here?' survives a checkpoint/reload.
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

    def _link_version(self, parent_id: str, child_id: str):
        """Record that ``child_id`` supersedes ``parent_id`` in the branch-aware
        version graph. Idempotent (sets dedupe), and a parent may accumulate
        several children — that fan-out IS a branch. ``supersedes`` is single
        valued, so each child has exactly one version parent.
        """
        with self._lock:
            self._version_children[parent_id].add(child_id)
            self._version_parent[child_id] = parent_id

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

    # ── Version graph (Feature #2 — branch-aware) ──────────────────────────────
    # These read the child→parent structure built from every record's immutable
    # `supersedes` pointer, so they see BRANCHES that the linear helpers above
    # (which follow the last-wins `_superseded` map) collapse. All operate on
    # IDs; EmberDB resolves them to records.

    def get_version_children(self, record_id: str) -> set[str]:
        """Direct successors — records that supersede this one. More than one
        means the lineage branches here."""
        return self._version_children.get(record_id, set()).copy()

    def get_version_parent(self, record_id: str) -> str | None:
        """The single record this one directly superseded, or None if it is an
        original (root) version."""
        return self._version_parent.get(record_id)

    def get_version_root(self, record_id: str) -> str:
        """Walk parents up to the original version at the top of the lineage."""
        current = record_id
        seen = {current}
        while current in self._version_parent:
            parent = self._version_parent[current]
            if parent in seen:          # defensive: never loop on a cycle
                break
            seen.add(parent)
            current = parent
        return current

    def get_version_ancestors(self, record_id: str) -> list[str]:
        """Prior versions, nearest first, walking `supersedes` upward."""
        out: list[str] = []
        current = self._version_parent.get(record_id)
        seen = {record_id}
        while current and current not in seen:
            seen.add(current)
            out.append(current)
            current = self._version_parent.get(current)
        return out

    def get_version_descendants(self, record_id: str) -> set[str]:
        """Every later version reachable through supersession — the whole
        subtree below this record, following all branches."""
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
        """All records in this record's lineage (root + every descendant)."""
        root = self.get_version_root(record_id)
        return {root} | self.get_version_descendants(root)

    def get_version_heads(self, record_id: str) -> set[str]:
        """The current version(s): leaves of the lineage tree (no successors).
        More than one head means the lineage has forked into live branches that
        were never reunited."""
        return {n for n in self._lineage_nodes(record_id)
                if not self._version_children.get(n)}

    def get_branch_points(self, record_id: str) -> set[str]:
        """Records in this lineage that were superseded in more than one
        direction — i.e. where the version history forks."""
        return {n for n in self._lineage_nodes(record_id)
                if len(self._version_children.get(n, set())) > 1}

    def get_version_tree(self, record_id: str) -> dict[str, list[str]]:
        """Adjacency map ``{node: [child ids]}`` for the entire lineage rooted
        at the original version — the full branching structure in one shot."""
        return {n: sorted(self._version_children.get(n, set()))
                for n in self._lineage_nodes(record_id)}

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
            "version_links": len(self._version_parent),
            "branch_points": sum(1 for c in self._version_children.values()
                                 if len(c) > 1),
        }
