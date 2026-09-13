"""
Ember's Diaries
A cognitive database engine for AI memory systems.

Nothing is ever deleted. Nothing is ever overwritten.
Every state that ever existed is preserved. The past is first-class.
"""

from .db import EmberDB
from .db_feedback import bind as _bind_feedback
_bind_feedback(EmberDB)
from .core.record import EmberRecord
from .core.annotation import Annotation, ReflectiveAnnotation
from .core.edge import EdgeRef
from .core.evidence import Evidence
from .core.proposal import MemoryProposal
from .core.conflict import Conflict
from .core.session import Session
from .core.failure import Failure
from .core.feedback import Feedback, FeedbackOutcome, FeedbackAttribution
from .core.agent_view import AgentView
from .core.integrity import HASH_BACKEND, RecordIntegrityError
from .core.errors import ConcurrentModificationError
from .engine.promotion import (
    PromotionEngine, PromotionPolicy, PromotionDecision, PromotionResult,
    PromotionOutcome,
)
from .core.types import (
    RecordType, MemoryType, MemoryRoom, MemoryScope,
    AccessLevel, VerifyStatus, DeprecationReason, EdgeType,
    SourceType, ProposalStatus, MemoryStatus, PromotionMethod, PromotionMode,
    ConflictType, ConflictStatus, SessionStatus,
)

__version__ = "0.2.0"
__author__  = "Sammie — ticketguy"

__all__ = [
    "EmberDB", "EmberRecord", "Annotation", "ReflectiveAnnotation",
    "EdgeRef", "Evidence", "MemoryProposal", "Conflict", "Session", "Failure",
    "Feedback", "FeedbackOutcome", "FeedbackAttribution",
    "AgentView",
    "RecordType", "MemoryType", "MemoryRoom", "MemoryScope",
    "AccessLevel", "VerifyStatus", "DeprecationReason", "EdgeType",
    "SourceType", "ProposalStatus", "MemoryStatus", "PromotionMethod",
    "PromotionMode", "ConflictType", "ConflictStatus", "SessionStatus",
    "PromotionEngine", "PromotionPolicy", "PromotionDecision",
    "PromotionResult", "PromotionOutcome",
    "HASH_BACKEND", "RecordIntegrityError", "ConcurrentModificationError",
]
