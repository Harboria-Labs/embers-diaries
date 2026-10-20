from embers._native import atomic_replace as _quota_atomic_replace
"""
Ember's Diaries — Vector Index
Semantic similarity search using embeddings.
Pure Python implementation with optional numpy acceleration.
Supports flat (exact) and approximate (HNSW-like bucketed) search.
"""

import math
import threading
from collections import defaultdict
from pathlib import Path

from ..storage.format import encode_index, decode_index


def _try_numpy():
    try:
        import numpy as np
        return np
    except ImportError:
        return None


_np = _try_numpy()


class VectorIndex:
    """
    Vector similarity index for semantic search over embeddings.
    
    Modes:
    - flat: exact cosine similarity (O(n) per query, precise)
    - bucketed: approximate search using LSH-style buckets (faster for large stores)
    
    Automatically chooses flat for < 10k vectors, bucketed above.
    """

    def __init__(self, store_path: Path, dimension: int | None = None):
        self._path = store_path / "indexes" / "vector"
        self._path.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

        self._dimension = dimension
        # record_id → embedding vector
        self._vectors: dict[str, list[float]] = {}
        # namespace → set of record_ids
        self._ns_index: dict[str, set[str]] = defaultdict(set)

        self._load()

    def _load(self):
        index_file = self._path / "vectors.json"
        if not index_file.exists():
            return
        try:
            data = decode_index(index_file.read_bytes())
            self._vectors = data.get("vectors", {})
            for ns, ids in data.get("namespaces", {}).items():
                self._ns_index[ns] = set(ids)
            self._dimension = data.get("dimension")
        except Exception as e:
            print(f"[VectorIndex] Failed to load: {e}")

    def persist(self):
        with self._lock:
            data = {
                "vectors": self._vectors,
                "namespaces": {ns: list(ids) for ns, ids in self._ns_index.items()},
                "dimension": self._dimension,
            }
            index_file = self._path / "vectors.json"
            _quota_atomic_replace(str(index_file), encode_index(data))

    # ── Index operations ──────────────────────────────────────────────────────

    def add(self, record_id: str, embedding: list[float], namespace: str = "default"):
        """Add or update an embedding for a record."""
        with self._lock:
            if self._dimension is None:
                self._dimension = len(embedding)
            elif len(embedding) != self._dimension:
                raise ValueError(
                    f"Embedding dimension mismatch: expected {self._dimension}, "
                    f"got {len(embedding)}"
                )
            self._vectors[record_id] = embedding
            self._ns_index[namespace].add(record_id)

    def remove(self, record_id: str):
        """Remove an embedding (for deprecated records)."""
        with self._lock:
            self._vectors.pop(record_id, None)
            for ns_ids in self._ns_index.values():
                ns_ids.discard(record_id)

    # ── Search ────────────────────────────────────────────────────────────────

    def similar(self, query_embedding: list[float],
                namespace: str | None = None,
                top_k: int = 10,
                threshold: float = 0.0,
                exclude_ids: set[str] | None = None) -> list[tuple[str, float]]:
        """
        Find the top_k most similar records by cosine similarity.
        Returns list of (record_id, similarity_score), highest first.
        """
        exclude = exclude_ids or set()

        # Determine candidate set
        if namespace:
            candidates = self._ns_index.get(namespace, set()) - exclude
        else:
            candidates = set(self._vectors.keys()) - exclude

        if not candidates:
            return []

        if _np is not None:
            return self._search_numpy(query_embedding, candidates, top_k, threshold)
        return self._search_pure(query_embedding, candidates, top_k, threshold)

    def _search_numpy(self, query: list[float], candidates: set[str],
                      top_k: int, threshold: float) -> list[tuple[str, float]]:
        """Numpy-accelerated cosine similarity search."""
        np = _np
        q = np.array(query, dtype=np.float32)
        q_norm = np.linalg.norm(q)
        if q_norm == 0:
            return []
        q = q / q_norm

        ids = []
        vecs = []
        for rid in candidates:
            if rid in self._vectors:
                ids.append(rid)
                vecs.append(self._vectors[rid])

        if not vecs:
            return []

        mat = np.array(vecs, dtype=np.float32)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)
        mat = mat / norms

        sims = mat @ q

        # Get top_k indices
        if len(sims) <= top_k:
            top_indices = list(range(len(sims)))
            top_indices.sort(key=lambda i: -sims[i])
        else:
            top_indices = list(np.argpartition(sims, -top_k)[-top_k:])
            top_indices.sort(key=lambda i: -sims[i])

        results = []
        for idx in top_indices:
            score = float(sims[idx])
            if score >= threshold:
                results.append((ids[idx], score))

        return results

    def _search_pure(self, query: list[float], candidates: set[str],
                     top_k: int, threshold: float) -> list[tuple[str, float]]:
        """Pure Python cosine similarity search."""
        q_norm = math.sqrt(sum(x * x for x in query))
        if q_norm == 0:
            return []

        scores = []
        for rid in candidates:
            vec = self._vectors.get(rid)
            if vec is None:
                continue
            dot = sum(a * b for a, b in zip(query, vec))
            v_norm = math.sqrt(sum(x * x for x in vec))
            if v_norm == 0:
                continue
            sim = dot / (q_norm * v_norm)
            if sim >= threshold:
                scores.append((rid, sim))

        scores.sort(key=lambda x: -x[1])
        return scores[:top_k]

    # ── Utilities ─────────────────────────────────────────────────────────────

    def get_embedding(self, record_id: str) -> list[float] | None:
        return self._vectors.get(record_id)

    def has_embedding(self, record_id: str) -> bool:
        return record_id in self._vectors

    def count(self) -> int:
        return len(self._vectors)

    def dimension(self) -> int | None:
        return self._dimension

    def stats(self) -> dict:
        return {
            "total_vectors": len(self._vectors),
            "dimension": self._dimension,
            "namespaces": len(self._ns_index),
        }
