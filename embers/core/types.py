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
    CONFLICT   = "conflict"    # A mapped contradiction between two memories (§7)
    SESSION    = "session"     # A bounded period of agent activity (§9)
    FAILURE    = "failure"     # A failed approach, shared so others don't repeat it (§13)


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
    stays in the store, permanently distinguishable from a committed memory.

    HELD is a routing outcome, NOT a stored proposal state: the Promotion Engine
    uses it to say "this proposal did not meet the criteria to auto-promote and
    is awaiting more evidence or a human decision." A held proposal stays
    PENDING on disk (append-only: nothing is written for a hold)."""
    PENDING  = "pending"
    PROMOTED = "promoted"
    REJECTED = "rejected"


class MemoryStatus(str, Enum):
    """The epistemic state a DURABLE memory carries after promotion (spec §12).

    Crucial semantic (the whole point of the Promotion Engine): promotion does
    NOT assert a memory is definitely true. It asserts the proposal met the
    criteria to enter durable memory. The memory therefore still carries an
    explicit status, decoupled from the fact that it was stored at all:

      VERIFIED     grounded and confident enough to rely on
      PROVISIONAL  admitted to memory but not yet strongly confirmed
      DISPUTED     conflicting evidence exists; mapped, not resolved (§7)
      SUPERSEDED   a newer version has replaced it (mirrors the version chain)

    Status is an immutable per-version fact (folded into the content hash), so a
    status change is a new version — the history verified→disputed is preserved,
    never overwritten."""
    VERIFIED    = "verified"
    PROVISIONAL = "provisional"
    DISPUTED    = "disputed"
    SUPERSEDED  = "superseded"


class PromotionMethod(str, Enum):
    """HOW a memory came to be durable (spec §12 + the configurable engine).

    Recorded on the promoted memory so a later reader can weigh it by the
    process that admitted it, not just its confidence number:

      AUTOMATIC  policy gates passed (evidence valid, confidence high enough,
                 agent trusted, no known conflict) — no human in the loop
      CONSENSUS  enough independent agents corroborated it (multi-agent evidence)
      HUMAN      a human explicitly approved it
      DIRECT     written straight to memory without the proposal pipeline
                 (an ordinary db.write, tagged so it is distinguishable)"""
    AUTOMATIC = "automatic"
    CONSENSUS = "consensus"
    HUMAN     = "human"
    DIRECT    = "direct"


class PromotionMode(str, Enum):
    """How the Promotion Engine decides whether a proposal enters durable memory.

    This is the configurable knob the user asked for — conceptually the
    `[promotion] mode = "..."` setting. It selects the ROUTING policy that sits
    between a proposal and a durable memory:

      AUTOMATIC  promote as soon as policy gates pass (evidence valid, confidence
                 high enough, agent trusted, no conflicting memory) — no human
      CONSENSUS  promote once enough independent agents have corroborated the
                 discovery (distinct evidence authors ≥ threshold)
      HUMAN      never auto-promote; a human must explicitly approve
      HYBRID     route by risk: high-risk proposals (low confidence / conflict /
                 untrusted agent) go to the human gate, the rest auto-promote

    A mode governs only the DECISION. It never changes the append-only,
    hash-versioned nature of what promotion writes, and it never asserts a
    memory is true — a promoted memory still carries its own [[MemoryStatus]]."""
    AUTOMATIC = "automatic"
    CONSENSUS = "consensus"
    HUMAN     = "human"
    HYBRID    = "hybrid"


class ConflictType(str, Enum):
    """The kind of conflict the Conflict Engine has mapped (spec §7).

    STORAGE conflicts (two agents racing to modify the same memory) are handled
    by hash + version + optimistic concurrency (§6) at write time — they are
    prevented, not stored. What the Conflict Engine persists is the SEMANTIC
    kind: two DURABLE memories whose claims contradict each other. They are
    mapped and kept side by side, never silently reconciled or deleted."""
    SEMANTIC = "semantic"   # contradictory claims in two memories
    STORAGE  = "storage"    # concurrent modification (normally handled by §6)


class ConflictStatus(str, Enum):
    """Lifecycle of a mapped conflict (spec §7).

      OPEN          detected, not yet triaged
      INVESTIGATING someone is actively reconciling it
      RESOLVED      a resolution was recorded (e.g. one side is now correct)
      ACCEPTED_BOTH both memories are legitimately kept (context-dependent truth)
      SUPERSEDED    the conflict itself was replaced (e.g. one memory got a new
                    version that removed the contradiction)

    Transitions are append-only: like proposals, a conflict record is superseded
    by a new version carrying the new status, so the full triage history
    (open → investigating → resolved) is preserved and auditable. Neither
    contradicting memory is ever destroyed by any transition."""
    OPEN          = "open"
    INVESTIGATING = "investigating"
    RESOLVED      = "resolved"
    ACCEPTED_BOTH = "accepted_both"
    SUPERSEDED    = "superseded"


class SessionStatus(str, Enum):
    """Lifecycle of a first-class session (spec §9).

    A session is a bounded period of agent activity. It opens ACTIVE, and ends
    either COMPLETED (the task finished) or ABANDONED (the agent stopped without
    completing). Like every other lifecycle in Ember's Diaries, a status change
    is an append-only new version of the SESSION record — the history
    active → completed is preserved, never overwritten."""
    ACTIVE    = "active"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


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
