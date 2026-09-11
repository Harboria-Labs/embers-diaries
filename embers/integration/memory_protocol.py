"""
Ember's Diaries — Memory Protocol
The unified interface that LLMs use to interact with Ember's Diaries.

This is the "imbued" layer — the bridge between the cognitive database
and the language model. It provides:

1. remember(text) → store a new memory with auto-embedding
2. recall(query) → retrieve relevant memories as LLM context
3. reflect() → decay notes only. Conflicts are a separate agent queue.
4. forget(id) → deprecate a memory (never delete)
5. update(id, new_data) → supersede a memory (never overwrite)

The protocol is model-agnostic. Any LLM can use it.
"""

from datetime import datetime, timezone
from typing import Any, Callable

from ..core.record import EmberRecord
from ..core.annotation import Annotation, ReflectiveAnnotation
from ..core.types import RecordType, MemoryType, MemoryRoom, VerifyStatus, ConflictStatus
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
    and context formatting automatically. Conflict mapping is an
    agent decision, not a write side-effect.
    """

    def __init__(self, db,  # EmberDB instance
                 embed_fn: Callable | None = None,
                 embedding_dimension: int = 256,
                 default_namespace: str = "memories",
                 max_context_tokens: int = 4096):
        self.db = db
        self.namespace = default_namespace

        self.decay = DecayEngine()
        self.consolidation = ConsolidationEngine()
        self.segmenter = EpisodicSegmenter()
        self.reflection = ReflectionEngine(self.decay, None)

        self.embeddings = EmbeddingPipeline(embed_fn, embedding_dimension)
        self.context_builder = ContextBuilder(self.decay, max_context_tokens)

        self._write_count = 0
        self._last_conflict_hints: list[dict] = []

    _DEFAULT_DECAY_RATE_BY_TYPE = {
        MemoryType.FAILURE:    0.0,
        MemoryType.SKILL:      0.005,
        MemoryType.CONNECTIVE: 0.005,
        MemoryType.REFLECTIVE: 0.01,
        MemoryType.RAW:        0.01,
        MemoryType.UNSCOPED:   0.01,
        MemoryType.EPISODIC:   0.02,
    }

    def remember(self, content: Any,
                 tags: list[str] | None = None,
                 confidence: float = 1.0,
                 decay_rate: float | None = None,
                 written_by: str = "llm",
                 memory_type: str = "unscoped",
                 room: str = "unscoped",
                 verify_status: str = "hypothesis",
                 namespace: str | None = None,
                 agent_id: str | None = None,
                 session_id: str | None = None,
                 creation_reason: str | None = None,
                 derived_from: list | None = None) -> str:
        """Store a new memory. Agent sets kind and room on write.

        Kind ≠ room. Unknown or missing labels become unscoped.
        Ember does not guess. Recall must not invent a room later.
        """
        ns = namespace or self.namespace

        try:
            resolved_type = MemoryType(memory_type).value
        except ValueError:
            resolved_type = MemoryType.UNSCOPED.value
        try:
            resolved_room = MemoryRoom(room).value
        except ValueError:
            resolved_room = MemoryRoom.UNSCOPED.value

        data = content if isinstance(content, dict) else {"content": str(content)}
        if "memory_type" not in data:
            data["memory_type"] = resolved_type
        else:
            try:
                data["memory_type"] = MemoryType(data["memory_type"]).value
            except ValueError:
                data["memory_type"] = MemoryType.UNSCOPED.value
        if "room" not in data:
            data["room"] = resolved_room
        else:
            try:
                data["room"] = MemoryRoom(data["room"]).value
            except ValueError:
                data["room"] = MemoryRoom.UNSCOPED.value
        if "verify_status" not in data:
            data["verify_status"] = verify_status

        if decay_rate is None:
            try:
                resolved_decay_rate = self._DEFAULT_DECAY_RATE_BY_TYPE[MemoryType(data["memory_type"])]
            except ValueError:
                resolved_decay_rate = 0.01
        else:
            resolved_decay_rate = decay_rate

        record = EmberRecord(
            namespace=ns,
            record_type=RecordType.DOCUMENT,
            data=data,
            tags=tags or [],
            confidence=confidence,
            decay_rate=resolved_decay_rate,
            written_by=written_by,
            agent_id=agent_id,
            session_id=session_id,
            creation_reason=creation_reason,
            derived_from=list(derived_from) if derived_from else [],
        )

        record.embedding = self.embeddings.embed_record(record)
        record_id = self.db.write(record)
        self._check_conflicts(record)
        self._write_count += 1
        return record_id

    def recall(self, query: str,
               top_k: int = 10,
               namespace: str | None = None,
               room: str | None = None,
               threshold: float = 0.0,
               include_annotations: bool = True,
               format: str = "text") -> str | list[dict]:
        """Retrieve relevant memories. room filters by stored room only."""
        ns = namespace or self.namespace

        query_embedding = self.embeddings.embed_text(query)
        vector_results = self.db.similar(
            query_embedding, namespace=ns, top_k=top_k * 2, threshold=threshold)
        text_results = self.db.search(query, namespace=ns, top_k=top_k * 2)

        candidates: dict[str, tuple[EmberRecord, float]] = {}

        def _merge(results: list[tuple[EmberRecord, float]]):
            if not results:
                return
            scores = [s for _, s in results]
            lo, hi = min(scores), max(scores)
            spread = (hi - lo) or 1.0
            for record, raw_score in results:
                normalized = 0.2 + 0.8 * (raw_score - lo) / spread
                eff_conf = self.decay.effective_confidence(record)
                composite = normalized * max(eff_conf, 0.05)
                existing = candidates.get(record.id)
                if existing is None or composite > existing[1]:
                    candidates[record.id] = (record, composite)

        _merge(vector_results)
        _merge(text_results)

        ranked = sorted(candidates.values(), key=lambda pair: pair[1], reverse=True)

        if room is not None:
            try:
                want_room = MemoryRoom(room).value
            except ValueError:
                want_room = None
            if want_room is not None:
                kept = []
                for rec, score in ranked:
                    stored = MemoryRoom.UNSCOPED.value
                    if isinstance(rec.data, dict) and rec.data.get("room"):
                        stored = rec.data["room"]
                    if stored == want_room:
                        kept.append((rec, score))
                ranked = kept

        records = [r for r, _ in ranked[:top_k]]

        for r in records:
            try:
                count, last = self.db.record_access(r.id)
                r.access_count = count
                r.last_accessed = last
            except Exception:
                pass

        if format == "raw":
            return records
        elif format == "messages":
            return self.context_builder.build_message_context(records)
        elif format == "structured":
            rows = self.context_builder.build_structured_context(records)
            return self._stamp_conflicts(rows)
        else:
            return self.context_builder.build_text_context(
                records, include_annotations=include_annotations)

    def verify(self, record_id: str,
               status: str = "verified",
               note: str = "",
               written_by: str = "llm") -> bool:
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
        data = new_content if isinstance(new_content, dict) else {"content": str(new_content)}
        return self.db.update(record_id, data, written_by,
                              agent_id=agent_id, session_id=session_id,
                              creation_reason=creation_reason,
                              derived_from=derived_from)

    def forget(self, record_id: str, reason: str = "",
               written_by: str = "llm") -> bool:
        from ..core.types import DeprecationReason
        return self.db.deprecate(record_id, DeprecationReason.MANUAL,
                                  reason, written_by)

    def reflect(self, namespace: str | None = None,
                limit: int = 50) -> list[ReflectiveAnnotation]:
        ns = namespace or self.namespace
        records = self.db.get_namespace(ns, include_deprecated=False, limit=limit)
        annotations = self.reflection.reflect(records, context="scheduled_reflection")
        for ann in annotations:
            if ann.target_record_id:
                try:
                    self.db.annotate(ann.target_record_id, ann)
                except Exception:
                    pass
        return annotations

    def consolidate(self, namespace: str | None = None) -> list[str]:
        ns = namespace or self.namespace
        records = self.db.get_namespace(ns, include_deprecated=False)
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
        ns = namespace or self.namespace
        records = self.db.get_namespace(ns, include_deprecated=False)
        episodes = self.segmenter.segment(records)
        return [ep.to_dict() for ep in episodes]

    _DURABLE_MEMORY_TYPES = frozenset({RecordType.NODE, RecordType.DOCUMENT})
    _CONFLICT_SKIP_KEYS = frozenset({
        "subject", "content", "memory_type", "room", "verify_status",
    })

    def _check_conflicts(self, new_record: EmberRecord):
        """Hint only. Never maps a CONFLICT record."""
        self._last_conflict_hints = self.conflict_hints(new_record)

    def conflict_hints(self, new_record: EmberRecord) -> list[dict]:
        if not isinstance(new_record.data, dict):
            return []
        subject = new_record.data.get("subject")
        if subject is None:
            return []
        try:
            existing = self.db.get_namespace(new_record.namespace, limit=200)
        except Exception:
            return []
        hints = []
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
                if key in self._CONFLICT_SKIP_KEYS:
                    continue
                old_val = rec.data.get(key)
                if old_val is not None and new_val is not None and old_val != new_val:
                    hints.append({
                        "other_id": rec.id,
                        "subject": subject,
                        "field": key,
                        "theirs": old_val,
                        "ours": new_val,
                        "note": (
                            "Possible disagreement. Map it with "
                            "ember_map_conflict only if you decide this "
                            "is a real conflict."
                        ),
                    })
                    break
        return hints

    def _stamp_conflicts(self, rows: list[dict]) -> list[dict]:
        for row in rows:
            rid = row.get("id")
            if not rid:
                continue
            try:
                found = self.db.conflicts_for(rid)
            except Exception:
                found = []
            if not found:
                continue
            live = found[0]
            row["conflict"] = live.status.value
            row["conflict_id"] = live.conflict_id
        return rows

    def get_unresolved_conflicts(self, namespace: str | None = None) -> list[dict]:
        ns = namespace or self.namespace
        return [c.to_dict() for c in
                self.db.conflict_records(namespace=ns, status=ConflictStatus.OPEN)]

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
