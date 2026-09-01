"""
Ember's Diaries — Core Types
All enums used across the system.
"""

from enum import Enum, auto


class RecordType(str, Enum):
    """The record types Ember's Diaries natively supports.

    The first six are the original storage primitives. EVIDENCE and PROPOSAL
    (Features #4/#5) are staging types on the road to durable memory: a PROPOSAL
    is a discovery awaiting validation, EVIDENCE is a hashed observation that
    supports a claim. Both are ordinary append-only records — storing them with
    distinct record_types is what keeps a rejected proposal permanently
    DISTINGUISHABLE from a committed memory (spec §16 → Promotion)."""
    DOCUMENT   = "document"    # Structured or semi-structured data
    NODE       = "node"        # Graph vertex — entity, concept, memory
    EDGE       = "edge"        # Graph connection between nodes
    TIMESERIES = "timeseries"  # Sequential data indexed by time
    VECTOR     = "vector"      # Embedding record for semantic search
    RAW        = "raw"         # Binary, blobs, unstructured
    EVIDENCE   = "evidence"    # A hashed observation supporting a claim (§5)
    PROPOSAL   = "proposal"    # A discovery awaiting validation (§4)


class MemoryType(str, Enum):
    """Memory types for AI cognitive systems (e.g. A Thousand Pearls)."""
    RAW        = "raw"
    SKILL      = "skill"
    FAILURE    = "failure"
    EPISODIC   = "episodic"
    CONNECTIVE = "connective"
    REFLECTIVE = "reflective"


class MemoryScope(str, Enum):
    """Scope levels in the memory tree."""
    TASK      = "task"
    AGENT     = "agent"
    LILACORE  = "lilacore"


class AccessLevel(str, Enum):
    """Namespace access control levels."""
    PUBLIC   = "public"
    PRIVATE  = "private"
    INTERNAL = "internal"


class VerifyStatus(str, Enum):
    """Knowledge entry verification states."""
    VERIFIED   = "verified"
    HYPOTHESIS = "hypothesis"
    CONTESTED  = "contested"
    DEPRECATED = "deprecated"


class SourceType(str, Enum):
    """How a piece of evidence came to be known (spec §5).

    This is the distinction future agents need in order to weigh a memory:
    was the underlying claim *directly observed*, merely *inferred*, *reported
    by another agent* (hearsay — trust it as far as you trust that agent),
    *experimentally verified*, *imported* from an external corpus, or
    *manually entered* by a human? It separates the strong chain
    CLAIM → EVIDENCE → SOURCE from the weak chain CLAIM → agent assertion."""
    DIRECTLY_OBSERVED       = "directly_observed"
    INFERRED                = "inferred"
    REPORTED                = "reported"          # by another agent
    EXPERIMENTALLY_VERIFIED = "experimentally_verified"
    IMPORTED                = "imported"
    MANUALLY_ENTERED        = "manually_entered"


class ProposalStatus(str, Enum):
    """Lifecycle of a memory proposal (spec §4 / §12 promotion).

    PENDING once proposed; PROMOTED when validated into a durable memory (the
    proposal record itself is preserved and points to the memory it became);
    REJECTED when validation fails. A rejected proposal is NEVER deleted — it
    stays in the store, permanently distinguishable from a committed memory."""
    PENDING  = "pending"
    PROMOTED = "promoted"
    REJECTED = "rejected"


class DeprecationReason(str, Enum):
    """Why a record was deprecated."""
    SUPERSEDED   = "superseded"    # Replaced by newer version
    INVALID      = "invalid"       # Found to be incorrect
    EXPIRED      = "expired"       # Time-sensitive data past valid_until
    MERGED       = "merged"        # Combined into another record
    MANUAL       = "manual"        # Manually deprecated by operator


class EdgeType(str, Enum):
    """Semantic types for graph edges."""
    RELATES_TO    = "relates_to"
    CAUSED_BY     = "caused_by"
    LED_TO        = "led_to"
    CONTRADICTS   = "contradicts"
    SUPPORTS      = "supports"
    PART_OF       = "part_of"
    INSTANCE_OF   = "instance_of"
    SUPERSEDES    = "supersedes"
    REFLECTS_ON   = "reflects_on"
    DERIVED_FROM  = "derived_from"
    SIMILAR_TO    = "similar_to"
    CUSTOM        = "custom"
