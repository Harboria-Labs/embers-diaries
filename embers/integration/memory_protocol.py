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
from ..core.types import RecordType, MemoryType, VerifyStatus, ConflictStatus
from ..cognitive.decay import DecayEngine
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
        self.consolidation = ConsolidationEngine()
        self.segmenter = EpisodicSegmenter()
        # No cognitive.ConflictDetector: conflict tracking is unified onto the
        # persisted conflict engine (EmberDB.map_conflict/conflicts_for, spec
        # §7) via _check_conflicts() below, instead of the old in-memory,
        # non-persistent ConflictDetector. ReflectionEngine's conflict-based
        # reflection sub-feature is therefore inactive (passed None) until it
        # is repointed at db.conflicts_for() -- reflect() is not currently
        # reachable through any MCP tool, so this has no live effect today.
        self.reflection = ReflectionEngine(self.decay, None)

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

        Retrieval strategy (two-phase, unified scoring):
          1. Vector similarity via db.similar() — uses the query embedding
             against the vector index. This is the primary retrieval path
             when an embedding function is available.
          2. Full-text BM25 via db.search() — keyword match as a complement.

        Adaptive ranking: vector (cosine) and BM25 scores are on different,
        incomparable scales, so each list is independently min-max
        normalized to [0,1] (standard hybrid-retrieval practice — this is
        not a bespoke scoring formula, just score normalization so the two
        result sets can be compared at all). Each candidate's normalized
        retrieval score is then weighted by its DECAYED confidence
        (DecayEngine.effective_confidence) — a stale or low-confidence
        memory ranks lower even if it's the strongest textual/semantic
        match, rather than a static concatenate-vector-then-append-text
        order with no notion of relevance decay at all. A record appearing
        in both result sets keeps its higher composite score.

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

        # ── Phase 2: Full-text BM25 search (public API) ───────────────────
        text_results = self.db.search(query, namespace=ns, top_k=top_k * 2)

        # ── Unified, confidence-weighted ranking ───────────────────────────
        candidates: dict[str, tuple[EmberRecord, float]] = {}

        def _merge(results: list[tuple[EmberRecord, float]]):
            if not results:
                return
            scores = [s for _, s in results]
            lo, hi = min(scores), max(scores)
            spread = (hi - lo) or 1.0
            for record, raw_score in results:
                # Min-max normalized, but floored at 0.2 rather than 0 --
                # a hard 0 is a batch-size artifact (the worst score in ANY
                # result set gets mapped to exactly 0 by plain min-max, even
                # when it's still a genuine, relevant hit), and 0 * anything
                # is always 0 -- which meant the confidence weight below
                # could never matter for whichever record happened to be
                # weakest in a given call, no matter how confident it was.
                normalized = 0.2 + 0.8 * (raw_score - lo) / spread
                eff_conf = self.decay.effective_confidence(record)
                # Floor so a genuine retrieval hit is never zeroed out just
                # because it decayed heavily -- decay demotes, it doesn't
                # disqualify. Reflection (ember_reflect) is what surfaces a
                # heavily-decayed memory for reinforcement/review.
                composite = normalized * max(eff_conf, 0.05)
                existing = candidates.get(record.id)
                if existing is None or composite > existing[1]:
                    candidates[record.id] = (record, composite)

        _merge(vector_results)
        _merge(text_results)

        ranked = sorted(candidates.values(), key=lambda pair: pair[1], reverse=True)
        records = [r for r, _ in ranked[:top_k]]

        # Track access -- persisted (see EmberDB.record_access / WriteEngine's
        # access sidecar). Previously this only mutated the in-memory record
        # object here, discarded the moment the call returned, so
        # reinforcement (DecayEngine.effective_confidence's access_count
        # term) could never accumulate across separate recall() calls. The
        # immutable record itself is still never touched -- record_access()
        # writes a small sidecar counter, same pattern as deprecation.
        for r in records:
            try:
                count, last = self.db.record_access(r.id)
                r.access_count = count
                r.last_accessed = last
            except Exception:
                pass

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

        The consolidated record is written to the SAME namespace it read
        from (not ConsolidationEngine's own long_term_ns default) so it
        stays visible to recall()/ember_recall with no special namespace
        argument -- previously this defaulted to a different namespace
        ("long_term") than recall() searches by default ("memories"),
        so consolidation's own output was invisible to the tool most
        callers would use to find it.

        Returns IDs of newly created consolidated records.
        """
        ns = namespace or self.namespace
        records = self.db.get_namespace(ns, include_deprecated=False)

        # Find consolidation candidates
        groups = self.consolidation.find_consolidation_candidates(records)

        new_ids = []
        for group in groups:
            consolidated = self.consolidation.create_consolidation_record(
                group, namespace=ns)
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

    # Durable memories are written under two different record types depending
    # on the path: remember() (this file) writes DOCUMENT, db.promote() writes
    # NODE. Only compare against these -- not evidence, proposals, conflicts,
    # or other staging records that also live in the namespace.
    _DURABLE_MEMORY_TYPES = frozenset({RecordType.NODE, RecordType.DOCUMENT})

    def _check_conflicts(self, new_record: EmberRecord):
        """Proactively check a new record against existing same-namespace
        memories for a field-value contradiction, and map any found through
        the PERSISTED conflict engine (EmberDB.map_conflict, spec §7) rather
        than the old in-memory-only ConflictDetector (removed).

        Deliberately opt-in and identity-scoped: only runs when the record's
        data is a dict that declares a "subject" key, and only compares
        against other records sharing that exact subject. Two memories with
        no subject, or different subjects, are never compared.

        This scoping exists because a blind all-keys diff across a namespace
        is unsound: two unrelated memories (e.g. a WiFi password and a
        standup-time note) almost always differ on their generic "content"
        field, which produced permanent false "CONFLICT DETECTED"
        annotations on ordinary, unrelated writes under the old
        ConflictDetector-based version of this method. Requiring an explicit
        shared subject means a difference is only surfaced when the caller
        has already asserted "these are claims about the same thing" --
        genuinely worth a human/agent's triage, not two unrelated facts that
        happen to share a wrapper key. See db.map_conflict()'s docstring and
        PromotionEngine._field_value_conflicts (engine/promotion.py) for the
        same reasoning applied on the proposal side.

        A mapped conflict is OPEN, not blocking -- map_conflict() never
        modifies or removes either memory (spec §7); this only makes the
        disagreement queryable and resolvable via ember_conflicts_for /
        ember_resolve_conflict, instead of a disconnected annotation no
        promotion or retrieval path could ever act on."""
        if not isinstance(new_record.data, dict):
            return
        subject = new_record.data.get("subject")
        if subject is None:
            return
        try:
            existing = self.db.get_namespace(new_record.namespace, limit=200)
        except Exception:
            return
        for rec in existing:
            if rec.id == new_record.id:
                continue
            if rec.record_type not in self._DURABLE_MEMORY_TYPES:
                continue
            if not isinstance(rec.data, dict):
                continue
            if rec.data.get("subject") != subject:
                continue
            for key, new_val in new_record.data.items():
                if key == "subject":
                    continue
                old_val = rec.data.get(key)
                if old_val is not None and new_val is not None and old_val != new_val:
                    try:
                        self.db.map_conflict(
                            rec.id, new_record.id,
                            detected_by=new_record.written_by or "conflict-detector",
                            note=(f"Field {key!r} differs for subject "
                                  f"{subject!r}: {old_val!r} vs {new_val!r}"))
                    except Exception:
                        pass
                    break  # one mapped conflict per pair is enough

    def get_unresolved_conflicts(self, namespace: str | None = None) -> list[dict]:
        """Get all OPEN (unresolved) conflicts, reading the persisted conflict
        engine directly rather than a separate in-memory tracker."""
        ns = namespace or self.namespace
        return [c.to_dict() for c in
                self.db.conflict_records(namespace=ns, status=ConflictStatus.OPEN)]

    # ── Stats ─────────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "memories_written": self._write_count,
            "db_stats": self.db.stats(),
            "conflicts": {
                "open": len(self.db.conflict_records(status=ConflictStatus.OPEN)),
            },
            "episodes": self.segmenter.stats(),
            "embeddings": {
                "dimension": self.embeddings.dimension,
                "vocab_size": self.embeddings.vocabulary_size,
                "is_custom": self.embeddings.is_custom,
            },
        }
