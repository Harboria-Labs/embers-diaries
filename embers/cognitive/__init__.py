"""
Ember's Diaries — Cognitive Engine
Human-inspired memory processing: episodic segmentation, consolidation,
confidence decay, reflective triggers.

Conflict detection previously lived here (ConflictDetector, in-memory only,
never persisted) and has been removed -- conflict tracking is unified onto
the persisted conflict engine in EmberDB (map_conflict/conflicts_for/
resolve_conflict, spec §7), triggered from
MemoryProtocol._check_conflicts(). See embers/integration/memory_protocol.py.
"""

from .decay import DecayEngine
from .consolidation import ConsolidationEngine
from .episodic import EpisodicSegmenter
from .reflection import ReflectionEngine

__all__ = [
    "DecayEngine", "ConsolidationEngine",
    "EpisodicSegmenter", "ReflectionEngine",
]
