"""
Ember's Diaries — Embedding Pipeline
Converts records to vector embeddings for semantic search.

Supports multiple embedding backends:
- Built-in: simple TF-IDF based embeddings (no dependencies)
- External: any function that maps text → list[float]

The pipeline extracts text from records, generates embeddings,
and stores them in the vector index.
"""

import math
import re
from collections import Counter, defaultdict

from ..core.record import EmberRecord


def _tokenize(text: str) -> list[str]:
    if not isinstance(text, str):
        return []
    return [t for t in re.findall(r'[a-z0-9]+', text.lower()) if len(t) >= 2]


def _extract_text(record: EmberRecord) -> str:
    """Extract all searchable text from a record."""
    parts = []

    if isinstance(record.data, dict):
        for v in record.data.values():
            if isinstance(v, str):
                parts.append(v)
            elif isinstance(v, (list, tuple)):
                parts.extend(str(x) for x in v if isinstance(x, str))
    elif isinstance(record.data, str):
        parts.append(record.data)

    parts.extend(record.tags)

    for ann in record.annotations:
        parts.append(ann.content)

    return " ".join(parts)


class EmbeddingPipeline:
    """
    Generates embeddings for Ember records.

    Built-in mode uses TF-IDF embeddings (no external dependencies).
    For production use, pass a custom embed_fn that calls a real model.
    """

    def __init__(self, embed_fn=None, dimension: int = 256):
        self._embed_fn = embed_fn
        self._dimension = dimension
        self._idf: dict[str, float] = {}
        self._vocab: dict[str, int] = {}
        self._doc_count: int = 0
        self._doc_freq: dict[str, int] = defaultdict(int)

    def _observe(self, text: str) -> None:
        """Grow the TF-IDF vocab from text. Needed before the first embed."""
        tokens = set(_tokenize(text))
        if not tokens:
            return
        self._doc_count += 1
        for token in tokens:
            self._doc_freq[token] += 1
        self._rebuild_vocab()

    def embed_record(self, record: EmberRecord) -> list[float]:
        text = _extract_text(record)
        if self._embed_fn:
            return self._embed_fn(text)
        if not self._vocab:
            self._observe(text)
        return self._tfidf_embed(text)

    def embed_text(self, text: str) -> list[float]:
        if self._embed_fn:
            return self._embed_fn(text)
        if not self._vocab:
            self._observe(text)
        return self._tfidf_embed(text)

    def embed_batch(self, records: list[EmberRecord]) -> list[tuple[str, list[float]]]:
        if self._embed_fn is None:
            for record in records:
                text = _extract_text(record)
                tokens = set(_tokenize(text))
                self._doc_count += 1
                for token in tokens:
                    self._doc_freq[token] += 1
            self._rebuild_vocab()

        results = []
        for record in records:
            embedding = self.embed_record(record)
            results.append((record.id, embedding))
        return results

    def _rebuild_vocab(self):
        sorted_tokens = sorted(self._doc_freq.items(), key=lambda x: -x[1])
        self._vocab = {}
        for i, (token, _) in enumerate(sorted_tokens[:self._dimension]):
            self._vocab[token] = i

        self._idf = {}
        for token, idx in self._vocab.items():
            df = self._doc_freq.get(token, 0)
            self._idf[token] = math.log((self._doc_count + 1) / (df + 1)) + 1

    def _tfidf_embed(self, text: str) -> list[float]:
        tokens = _tokenize(text)
        if not tokens:
            return [0.0] * self._dimension

        tf = Counter(tokens)
        vec = [0.0] * self._dimension
        for token, count in tf.items():
            idx = self._vocab.get(token)
            if idx is not None:
                idf = self._idf.get(token, 1.0)
                vec[idx] = count * idf

        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def vocabulary_size(self) -> int:
        return len(self._vocab)

    @property
    def is_custom(self) -> bool:
        return self._embed_fn is not None
