"""Rust-owned causal graph storage and traversal.

The Python class is a compatibility facade. Rust reads and atomically publishes
the existing adjacency JSON format, avoiding stale per-process graph copies.
"""

import json
import threading
from pathlib import Path

from embers._native import (
    graph_add_edge as _graph_add_edge,
    graph_query as _graph_query,
    graph_remove_edge as _graph_remove_edge,
)

GRAPH_BACKEND = "rust-pyo3"


class GraphIndex:
    """Bidirectional, durable adjacency index backed by the Rust core."""

    def __init__(self, store_path: Path):
        graph_dir = store_path / "indexes" / "graph"
        graph_dir.mkdir(parents=True, exist_ok=True)
        self._index_file = graph_dir / "adjacency.json"
        self._lock = threading.RLock()

    def _query(self, operation: str, node_id: str = "", other_id: str = "",
               depth: int = 1, edge_type: str | None = None,
               direction: str = "outgoing"):
        raw = _graph_query(
            str(self._index_file), operation, node_id, other_id, depth,
            edge_type, direction)
        return json.loads(bytes(raw).decode("utf-8"))

    def persist(self):
        """Mutations are already atomically durable; retained for API parity."""

    def add_edge(self, from_id: str, to_id: str,
                 edge_type: str = "relates_to", weight: float = 1.0,
                 edge_id: str = "", label: str = "",
                 metadata: dict | None = None):
        with self._lock:
            _graph_add_edge(
                str(self._index_file), from_id, to_id, edge_type, weight,
                edge_id, label,
                json.dumps(metadata or {}, ensure_ascii=False).encode("utf-8"))

    def remove_edge(self, from_id: str, to_id: str,
                    edge_type: str | None = None):
        with self._lock:
            _graph_remove_edge(
                str(self._index_file), from_id, to_id, edge_type)

    def neighbors(self, node_id: str, depth: int = 1,
                  edge_type: str | None = None,
                  direction: str = "outgoing") -> list[str]:
        return self._query("neighbors", node_id, depth=depth,
                           edge_type=edge_type, direction=direction)

    def path(self, from_id: str, to_id: str,
             max_depth: int = 10) -> list[str] | None:
        return self._query("path", from_id, to_id, depth=max_depth)

    def subgraph(self, root_id: str, depth: int = 2) -> dict:
        return self._query("subgraph", root_id, depth=depth)

    def connected(self, node_id: str, edge_type: str) -> list[str]:
        return self._query("connected", node_id, edge_type=edge_type)

    def get_edges(self, node_id: str,
                  direction: str = "outgoing") -> list[dict]:
        return self._query("edges", node_id, direction=direction)

    def degree(self, node_id: str, direction: str = "both") -> int:
        return self._query("degree", node_id, direction=direction)

    def node_count(self) -> int:
        return self.stats()["nodes"]

    def edge_count(self) -> int:
        return self.stats()["edges"]

    def stats(self) -> dict:
        return self._query("stats")
