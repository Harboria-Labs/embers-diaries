"""
Ember's Diaries — LLM Integration Layer
Bridges the database to language models.
Memory KV injection, context building, embedding pipeline.
"""

from .context import ContextBuilder
from .embeddings import EmbeddingPipeline
from .memory_protocol import MemoryProtocol
from .conflict_policy import install as install_conflict_policy

install_conflict_policy()

__all__ = ["ContextBuilder", "EmbeddingPipeline", "MemoryProtocol"]
