"""
Ember's Diaries
A cognitive database engine for AI memory systems.

Nothing is ever deleted. Nothing is ever overwritten.
Every state that ever existed is preserved. The past is first-class.

Quick start:
    from embers import EmberDB, EmberRecord, RecordType

    db = EmberDB.connect("./my_store")
    record_id = db.write(EmberRecord(
        namespace="memories",
        data={"content": "First memory"},
        tags=["test"],
    ))
    record = db.get(record_id)

    # LLM integration:
    from embers.integration import MemoryProtocol
    protocol = MemoryProtocol(db)
    protocol.remember("The user prefers dark mode")
    context = protocol.recall("What are the user's preferences?")
"""

from .db import EmberDB
from .core.record import EmberRecord
from .core.annotation import Annotation, ReflectiveAnnotation
from .core.edge import EdgeRef
from .core.evidence import Evidence
from .core.proposal import MemoryProposal
from .core.conflict import Conflict
from .core.session import Session
from .core.failure import Failure
from .core.integrity import HASH_BACKEND, RecordIntegrityError
from .core.errors import ConcurrentModificationError
from .engine.promotion import (
    PromotionEngine, PromotionPolicy, PromotionDecision, PromotionResult,
    PromotionOutcome,
)
from .core.types import (
    RecordType, MemoryType, MemoryScope,
    AccessLevel, VerifyStatus, DeprecationReason, EdgeType,
    SourceType, ProposalStatus, MemoryStatus, PromotionMethod, PromotionMode,
    ConflictType, ConflictStatus, SessionStatus,
)

__version__ = "0.2.0"
__author__  = "Sammie — ticketguy"

__all__ = [
    "EmberDB", "EmberRecord", "Annotation", "ReflectiveAnnotation",
    "EdgeRef", "Evidence", "MemoryProposal", "Conflict", "Session", "Failure",
    "RecordType", "MemoryType", "MemoryScope",
    "AccessLevel", "VerifyStatus", "DeprecationReason", "EdgeType",
    "SourceType", "ProposalStatus", "MemoryStatus", "PromotionMethod",
    "PromotionMode", "ConflictType", "ConflictStatus", "SessionStatus",
    "PromotionEngine", "PromotionPolicy", "PromotionDecision",
    "PromotionResult", "PromotionOutcome",
    "HASH_BACKEND", "RecordIntegrityError", "ConcurrentModificationError",
]
