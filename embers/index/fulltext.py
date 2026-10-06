from embers._native import atomic_replace as _quota_atomic_replace
"""
Ember's Diaries — Full-Text Search Index
Inverted index for keyword search across record data.
Pure Python implementation — no external dependencies.
"""

import re
import math
import threading
from collections import defaultdict, Counter
from pathlib import Path

from ..storage.format import encode_index, decode_index


# Simple tokenizer: lowercase, split on non-alphanumeric, filter short tokens
def _tokenize(text: str) -> list[str]:
    if not isinstance(text, str):
        return []
    tokens = re.findall(r'[a-z0-9]+', text.lower())
    return [t for t in tokens if len(t) >= 2]


def _extract_text(data) -> str:
    """Recursively extract text from any data structure."""
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        parts = []
        for v in data.values():
            parts.append(_extract_text(v))
        return " ".join(parts)
    if isinstance(data, (list, tuple)):
        return " ".join(_extract_text(item) for item in data)
    if isinstance(data, (int, float)):
        return str(data)
    return ""


class FullTextIndex:
    """
    TF-IDF based full-text search over record data.
    
    - Inverted index: token → {record_id: term_frequency}
    - BM25 scoring for relevance ranking
    """

    def __init__(self, store_path: Path):
        self._path = store_path / "indexes" / "fulltext"
        self._path.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

        # token → {record_id: term_count}
        self._inverted: dict[str, dict[str, int]] = defaultdict(dict)
        # record_id → total token count
        self._doc_lengths: dict[str, int] = {}
        # record_id → namespace
        self._doc_ns: dict[str, str] = {}
        # Total documents indexed
        self._total_docs: int = 0
        self._avg_doc_length: float = 0.0

        self._load()

    def _load(self):
        index_file = self._path / "inverted.json"
        if not index_file.exists():
            return
        try:
            data = decode_index(index_file.read_bytes())
            self._inverted = defaultdict(dict, data.get("inverted", {}))
            self._doc_lengths = data.get("doc_lengths", {})
            self._doc_ns = data.get("doc_ns", {})
            self._total_docs = data.get("total_docs", 0)
            self._avg_doc_length = data.get("avg_doc_length", 0.0)
        except Exception as e:
            print(f"[FullTextIndex] Failed to load: {e}")

    def persist(self):
        with self._lock:
            data = {
                "inverted": dict(self._inverted),
                "doc_lengths": self._doc_lengths,
                "doc_ns": self._doc_ns,
                "total_docs": self._total_docs,
                "avg_doc_length": self._avg_doc_length,
            }
            index_file = self._path / "inverted.json"
            _quota_atomic_replace(str(index_file), encode_index(data))

    # ── Index operations ──────────────────────────────────────────────────────

    def add(self, record_id: str, data, namespace: str = "default",
            extra_text: str = ""):
        """Index a record's data for full-text search."""
        with self._lock:
            text = _extract_text(data) + " " + extra_text
            tokens = _tokenize(text)
            if not tokens:
                return

            # Count term frequencies
            tf = Counter(tokens)

            # Update inverted index
            for token, count in tf.items():
                self._inverted[token][record_id] = count

            self._doc_lengths[record_id] = len(tokens)
            self._doc_ns[record_id] = namespace
            self._total_docs = len(self._doc_lengths)
            total_len = sum(self._doc_lengths.values())
            self._avg_doc_length = total_len / self._total_docs if self._total_docs > 0 else 0

    def remove(self, record_id: str):
        """Remove a record from the full-text index."""
        with self._lock:
            for token_docs in self._inverted.values():
                token_docs.pop(record_id, None)
            self._doc_lengths.pop(record_id, None)
            self._doc_ns.pop(record_id, None)
            self._total_docs = len(self._doc_lengths)

    # ── Search ────────────────────────────────────────────────────────────────

    def search(self, query: str, namespace: str | None = None,
               top_k: int = 10,
               fields: list[str] | None = None) -> list[tuple[str, float]]:
        """
        BM25 full-text search.
        Returns list of (record_id, relevance_score), highest first.
        """
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        # BM25 parameters
        k1 = 1.2
        b = 0.75
        N = self._total_docs
        avgdl = self._avg_doc_length or 1.0

        scores: dict[str, float] = defaultdict(float)

        for token in query_tokens:
            docs = self._inverted.get(token, {})
            if not docs:
                continue

            # IDF
            df = len(docs)
            idf = math.log((N - df + 0.5) / (df + 0.5) + 1)

            for rid, tf in docs.items():
                # Namespace filter
                if namespace and self._doc_ns.get(rid) != namespace:
                    continue

                dl = self._doc_lengths.get(rid, 1)
                # BM25 score
                numerator = tf * (k1 + 1)
                denominator = tf + k1 * (1 - b + b * dl / avgdl)
                scores[rid] += idf * numerator / denominator

        # Sort by score
        ranked = sorted(scores.items(), key=lambda x: -x[1])
        return ranked[:top_k]

    def search_exact(self, phrase: str, namespace: str | None = None) -> list[str]:
        """
        Exact phrase search (returns all record IDs containing the phrase).
        Slower — scans matching documents.
        """
        tokens = _tokenize(phrase)
        if not tokens:
            return []

        # Find documents containing ALL tokens
        candidate_sets = []
        for token in tokens:
            docs = set(self._inverted.get(token, {}).keys())
            candidate_sets.append(docs)

        if not candidate_sets:
            return []

        candidates = set.intersection(*candidate_sets)

        if namespace:
            candidates = {rid for rid in candidates
                          if self._doc_ns.get(rid) == namespace}

        return list(candidates)

    # ── Utilities ─────────────────────────────────────────────────────────────

    def count(self) -> int:
        return self._total_docs

    def vocabulary_size(self) -> int:
        return len(self._inverted)

    def stats(self) -> dict:
        return {
            "total_docs": self._total_docs,
            "vocabulary_size": len(self._inverted),
            "avg_doc_length": round(self._avg_doc_length, 1),
        }
