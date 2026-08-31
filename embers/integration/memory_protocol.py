"""
Ember's Diaries — Memory Protocol
The unified interface that LLMs use to interact with Ember's Diaries.

This is the "imbued" layer — the bridge between the cognitive database
and the language model. It provides:

1. remember(text) → store a new memory with auto-embedding
2. recall(query) → retrieve relevant memories as LLM context
3. reflect() → trigger cognitive processing (decay, conflicts, consolidation)
4. forget(id) → deprecate a memory (never delete)
5. update(id, new_data) → supersede a memory (never overwrite)

The protocol is model-agnostic. Any LLM can use it.
"""

from datetime import datetime, timezone
from typing import Any, Callable

from ..core.record import EmberRecord
from ..core.annotation import Annotation, ReflectiveAnnotation
from ..core.types import RecordType, MemoryType, VerifyStatus
from ..cognitive.decay import DecayEngine
from ..cognitive.conflict import ConflictDetector
from ..cognitive.consolidation import ConsolidationEngine
from ..cognitive.episodic import EpisodicSegmenter
from ..cognitive.reflection import ReflectionEngine
from .context import ContextBuilder
from .embeddings import EmbeddingPipeline


class MemoryProtocol:
    """
    The single interface for LLM ↔ Ember's Diaries communication.
    
    This is what gets wired into the model. When an LLM needs to:
    - Store something it learned → protocol.remember()
    - Retrieve relevant context → protocol.recall()
    - Verify a fact → protocol.verify()
    - Process its memories → protocol.reflect()
    
    The protocol handles embedding, indexing, retrieval, decay,
    conflict detection, and context formatting automatically.
    """

    def __init__(self, db,  # EmberDB instance
                 embed_fn: Callable | None = None,
                 embedding_dimension: int = 256,
                 default_namespace: str = "memories",
                 max_context_tokens: int = 4096):
        self.db = db
        self.namespace = default_namespace

        # Cognitive components
        self.decay = DecayEngine()
        self.conflicts = ConflictDetector()
        self.consolidation = ConsolidationEngine()
        self.segmenter = EpisodicSegmenter()
        self.reflection = ReflectionEngine(self.decay, self.conflicts)

        # Integration components
        self.embeddings = EmbeddingPipeline(embed_fn, embedding_dimension)
        self.context_builder = ContextBuilder(self.decay, max_context_tokens)

        # Internal state
        self._write_count = 0

    # ── Core Memory Operations ────────────────────────────────────────────────

    def remember(self, content: Any,
                 tags: list[str] | None = None,
                 confidence: float = 1.0,
                 decay_rate: float = 0.01,
                 written_by: str = "llm",
                 memory_type: str = "episodic",
                 verify_status: str = "hypothesis",
                 namespace: str | None = None,
                 agent_id: str | None = None,
                 session_id: str | None = None,
                 creation_reason: str | None = None,
                 derived_from: list | None = None) -> str:
        """
        Store a new memory. Auto-generates embedding and checks for conflicts.
        Returns the record ID.

        The embedding is set on the record before write. The db.write()
        callback handles all indexing (master, timeline, fulltext, vector,
        graph) automatically — no manual index calls needed here.

        Provenance (Feature #3): agent_id / session_id / creation_reason /
        derived_from are recorded on the memory and folded into its content
        hash, so every durable write can answer WHO wrote it, in WHICH session,
        WHY, and from WHAT prior memories it was derived.
        """
        ns = namespace or self.namespace

        # Build record
        data = content if isinstance(content, dict) else {"content": str(content)}
        if "memory_type" not in data:
            data["memory_type"] = memory_type
        if "verify_status" not in data:
            data["verify_status"] = verify_status

        record = EmberRecord(
            namespace=ns,
            record_type=RecordType.DOCUMENT,
            data=data,
            tags=tags or [],
            confidence=confidence,
            decay_rate=decay_rate,
            written_by=written_by,
            agent_id=agent_id,
            session_id=session_id,
            creation_reason=creation_reason,
            derived_from=list(derived_from) if derived_from else [],
        )

        # Generate embedding — set on record so db.write()'s callback
        # picks it up and indexes it in the vector store automatically
        record.embedding = self.embeddings.embed_record(record)

        # Write to store — callback handles ALL indexing
        record_id = self.db.write(record)

        # Conflict check (lazy — check against recent records in same namespace)
        self._check_conflicts(record)

        self._write_count += 1
        return record_id

    def recall(self, query: str,
               top_k: int = 10,
               namespace: str | None = None,
               threshold: float = 0.0,
               include_annotations: bool = True,
               format: str = "text") -> str | list[dict]:
        """
        Retrieve relevant memories for a query.

        Retrieval strategy (two-phase):
          1. Vector similarity via db.similar() — uses the query embedding
             against the vector index. This is the primary retrieval path
             when an embedding function is available.
          2. Full-text BM25 via db.search() — keyword match as a complement.
             Merged with vector results, deduped by record ID.

        If no embedding function was provided at init, the built-in TF-IDF
        pipeline generates lightweight embeddings automatically. This means
        vector search always runs — but for production quality, provide a
        real embedding function (e.g. sentence-transformers).

        Args:
            query: Natural language query
            top_k: Maximum memories to return
            namespace: Namespace filter
            threshold: Minimum similarity threshold
            format: 'text' (for prompt injection), 'messages' (for chat),
                    'structured' (for function calling), 'raw' (EmberRecord list)

        Returns formatted context ready for LLM consumption.
        """
        ns = namespace or self.namespace

        # ── Phase 1: Vector similarity search (public API) ────────────────
        query_embedding = self.embeddings.embed_text(query)
        vector_results = self.db.similar(
            query_embedding, namespace=ns, top_k=top_k * 2, threshold=threshold)

        records = []
        seen_ids = set()
        for record, score in vector_results:
            if record.id not in seen_ids:
                records.append(record)
                seen_ids.add(record.id)

        # ── Phase 2: Full-text BM25 search (public API) — merge in ───────
        text_results = self.db.search(query, namespace=ns, top_k=top_k)
        for record, score in text_results:
            if record.id not in seen_ids:
                records.append(record)
                seen_ids.add(record.id)

        # Limit
        records = records[:top_k]

        # Track access (in-memory only — the immutable record on disk is untouched)
        for r in records:
            r.access_count += 1
            r.last_accessed = datetime.now(timezone.utc)

        # Format output
        if format == "raw":
            return records
        elif format == "messages":
            return self.context_builder.build_message_context(records)
        elif format == "structured":
            return self.context_builder.build_structured_context(records)
        else:
            return self.context_builder.build_text_context(
                records, include_annotations=include_annotations)

    def verify(self, record_id: str,
               status: str = "verified",
               note: str = "",
               written_by: str = "llm") -> bool:
        """
        Update the epistemic status of a memory.
        Creates a verification annotation (never modifies the original).
        """
        valid_statuses = {"verified", "hypothesis", "contested", "deprecated"}
        if status not in valid_statuses:
            raise ValueError(f"Invalid status: {status}. Must be one of {valid_statuses}")

        ann = Annotation(
            content=f"Verification status updated to: {status}. {note}",
            context="verification",
            annotation_type="validation",
            written_by=written_by,
            tags=["verification", status],
        )
        self.db.annotate(record_id, ann)
        return True

    def update(self, record_id: str, new_content: Any,
               written_by: str = "llm",
               agent_id: str | None = None,
               session_id: str | None = None,
               creation_reason: str | None = None,
               derived_from: list | None = None) -> tuple[str, str]:
        """
        Update a memory (creates new version, preserves old).
        Returns (new_id, old_id).

        Provenance is attributed to this new version (see remember()); it is
        not inherited from the prior version.
        """
        data = new_content if isinstance(new_content, dict) else {"content": str(new_content)}
        return self.db.update(record_id, data, written_by,
                              agent_id=agent_id, session_id=session_id,
                              creation_reason=creation_reason,
                              derived_from=derived_from)

    def forget(self, record_id: str, reason: str = "",
               written_by: str = "llm") -> bool:
        """
        Deprecate a memory (never delete). Returns True if successful.
        """
        from ..core.types import DeprecationReason
        return self.db.deprecate(record_id, DeprecationReason.MANUAL,
                                  reason, written_by)

    # ── Cognitive Operations ──────────────────────────────────────────────────

    def reflect(self, namespace: str | None = None,
                limit: int = 50) -> list[ReflectiveAnnotation]:
        """
        Run a reflection cycle. Examines memories for:
        - Confidence decay
        - Unresolved conflicts
        - Consolidation opportunities
        
        Returns generated reflective annotations.
        """
        ns = namespace or self.namespace
        records = self.db.get_namespace(ns, include_deprecated=False, limit=limit)

        # Run reflection
        annotations = self.reflection.reflect(records, context="scheduled_reflection")

        # Write annotations to store
        for ann in annotations:
            if ann.target_record_id:
                try:
                    self.db.annotate(ann.target_record_id, ann)
                except Exception:
                    pass

        return annotations

    def consolidate(self, namespace: str | None = None) -> list[str]:
        """
        Run memory consolidation. Finds groups of related memories
        and creates consolidated long-term records.
        Returns IDs of newly created consolidated records.
        """
        ns = namespace or self.namespace
        records = self.db.get_namespace(ns, include_deprecated=False)

        # Find consolidation candidates
        groups = self.consolidation.find_consolidation_candidates(records)

        new_ids = []
        for group in groups:
            consolidated = self.consolidation.create_consolidation_record(group)
            try:
                rid = self.db.write(consolidated)
                new_ids.append(rid)
            except Exception:
                pass

        return new_ids

    def segment_episodes(self, namespace: str | None = None) -> list[dict]:
        """
        Segment records into episodic events.
        Returns list of episode dicts.
        """
        ns = namespace or self.namespace
        records = self.db.get_namespace(ns, include_deprecated=False)
        episodes = self.segmenter.segment(records)
        return [ep.to_dict() for ep in episodes]

    # ── Conflict Management ───────────────────────────────────────────────────

    def _check_conflicts(self, new_record: EmberRecord):
        """Check new record against existing records for conflicts."""
        ns = new_record.namespace
        try:
            existing = self.db.get_namespace(ns, limit=20)
            conflicts = self.conflicts.detect_value_conflict(new_record, existing)
            for conflict in conflicts:
                annotations = self.conflicts.create_conflict_annotations(conflict)
                for ann in annotations:
                    try:
                        self.db.annotate(ann.target_record_id, ann)
                    except Exception:
                        pass
        except Exception:
            pass

    def get_unresolved_conflicts(self) -> list[dict]:
        """Get all unresolved conflicts."""
        return [c.to_dict() for c in self.conflicts.get_unresolved()]

    # ── Stats ─────────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "memories_written": self._write_count,
            "db_stats": self.db.stats(),
            "conflicts": self.conflicts.stats(),
            "episodes": self.segmenter.stats(),
            "embeddings": {
                "dimension": self.embeddings.dimension,
                "vocab_size": self.embeddings.vocabulary_size,
                "is_custom": self.embeddings.is_custom,
            },
        }
