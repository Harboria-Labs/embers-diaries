"""
Ember's Diaries — Memory Consolidation Engine
Inspired by the Atkinson-Shiffrin model and LightMem's sleep-time update.

Three-stage pipeline:
1. Sensory → Short-term: Filter noise, extract salient information
2. Short-term → Long-term: Consolidate repeated/important memories
3. Sleep-time consolidation: Offline merging, deduplication, strengthening

All operations are append-only — consolidation creates new records
that reference the originals.
"""

import uuid
from datetime import datetime, timezone
from collections import defaultdict

from ..core.record import EmberRecord
from ..core.annotation import Annotation
from ..core.types import RecordType, MemoryType


def _tz_safe(dt: datetime) -> datetime:
    """EmberRecord.created_at defaults to datetime.utcnow() (naive), but
    anything that explicitly constructs a record with an aware UTC
    timestamp is also valid -- and Python raises TypeError comparing/
    sorting naive against aware datetimes. decay.py already guards against
    this (see DecayEngine.effective_confidence); this mirrors that same
    defensive pattern for consolidation's own created_at comparisons."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


class ConsolidationEngine:
    """
    Manages memory lifecycle from sensory input to long-term storage.
    Creates consolidation records that link back to source memories.
    
    Memory stages (mapped to namespaces):
    - sensory: raw inputs, short-lived, high volume
    - short_term: filtered and grouped, medium retention
    - long_term: consolidated, high confidence, persistent
    """

    def __init__(self, sensory_namespace: str = "sensory",
                 short_term_namespace: str = "short_term",
                 long_term_namespace: str = "long_term",
                 consolidation_threshold: int = 3,
                 importance_threshold: float = 0.7):
        self.sensory_ns = sensory_namespace
        self.short_term_ns = short_term_namespace
        self.long_term_ns = long_term_namespace
        self.consolidation_threshold = consolidation_threshold
        self.importance_threshold = importance_threshold

    def classify_incoming(self, record: EmberRecord) -> str:
        """
        Classify an incoming record into the appropriate memory stage.
        Returns the recommended namespace.
        
        Rules:
        - High confidence + tagged as important → long_term
        - Has been accessed multiple times → short_term
        - Default → sensory
        """
        if record.confidence >= self.importance_threshold:
            if "important" in record.tags or "verified" in record.tags:
                return self.long_term_ns

        if record.access_count >= self.consolidation_threshold:
            return self.short_term_ns

        return self.sensory_ns

    def find_consolidation_candidates(self,
                                       records: list[EmberRecord],
                                       similarity_fn=None) -> list[list[EmberRecord]]:
        """
        Find groups of records that should be consolidated.
        Groups by: same tags, similar data, temporal proximity.
        
        Returns list of record groups (each group → one consolidated record).
        """
        # Group by overlapping tags
        tag_groups: dict[str, list[EmberRecord]] = defaultdict(list)
        for r in records:
            if r.tags:
                key = tuple(sorted(r.tags[:3]))  # Use top 3 tags as group key
                tag_groups[key].append(r)

        # Also group by temporal proximity (within 1 hour)
        time_groups: dict[str, list[EmberRecord]] = defaultdict(list)
        sorted_records = sorted(records, key=lambda r: _tz_safe(r.created_at))
        if sorted_records:
            current_group_key = sorted_records[0].created_at.strftime("%Y-%m-%d-%H")
            for r in sorted_records:
                hour_key = r.created_at.strftime("%Y-%m-%d-%H")
                time_groups[hour_key].append(r)

        # Merge grouping strategies
        all_groups = []
        for key, group in tag_groups.items():
            if len(group) >= 2:
                all_groups.append(group)
        for key, group in time_groups.items():
            if len(group) >= self.consolidation_threshold:
                # Only add if not already covered by tag groups
                group_ids = {r.id for r in group}
                already_covered = any(
                    group_ids <= {r.id for r in g} for g in all_groups
                )
                if not already_covered:
                    all_groups.append(group)

        return all_groups

    def create_consolidation_record(self,
                                     source_records: list[EmberRecord],
                                     summary: str = "",
                                     written_by: str = "consolidation_engine",
                                     namespace: str | None = None) -> EmberRecord:
        """
        Create a consolidated record from multiple source records.
        The consolidated record links back to all sources.
        Sources are NOT deprecated — they remain in the store.
        """
        # Merge data
        merged_data = {
            "consolidated": True,
            "source_count": len(source_records),
            "source_ids": [r.id for r in source_records],
            "summary": summary or self._auto_summarize(source_records),
            "source_data": [r.data for r in source_records if r.data],
        }

        # Merge tags (union)
        all_tags = set()
        for r in source_records:
            all_tags.update(r.tags)
        all_tags.add("consolidated")

        # Average confidence (weighted by access count)
        total_weight = sum(max(r.access_count, 1) for r in source_records)
        weighted_conf = sum(
            r.confidence * max(r.access_count, 1) for r in source_records
        ) / total_weight if total_weight > 0 else 0.5

        # Use the earliest creation time as valid_from
        earliest = min(_tz_safe(r.created_at) for r in source_records)

        record = EmberRecord(
            id=str(uuid.uuid4()),
            namespace=namespace or self.long_term_ns,
            record_type=RecordType.DOCUMENT,
            data=merged_data,
            tags=list(all_tags),
            confidence=min(weighted_conf + 0.1, 1.0),  # Consolidation boosts confidence
            decay_rate=min(r.decay_rate for r in source_records) * 0.5,  # Slower decay
            written_by=written_by,
            valid_from=earliest,
            training_candidate=True,  # Consolidated memories are good training data
        )

        return record

    def _auto_summarize(self, records: list[EmberRecord]) -> str:
        """Basic auto-summary from record data. Override for LLM-powered summaries."""
        parts = []
        for r in records[:5]:  # Limit to first 5
            if isinstance(r.data, dict):
                content = r.data.get("content", r.data.get("summary", str(r.data)[:100]))
            elif isinstance(r.data, str):
                content = r.data[:100]
            else:
                content = str(r.data)[:100] if r.data else ""
            if content:
                parts.append(str(content))
        return " | ".join(parts)

    def promote_to_short_term(self, record: EmberRecord) -> EmberRecord:
        """
        Create a promoted version of a sensory record for short-term storage.
        Original remains in sensory namespace.
        """
        promoted = EmberRecord(
            id=str(uuid.uuid4()),
            namespace=self.short_term_ns,
            record_type=record.record_type,
            data=record.data,
            tags=record.tags + ["promoted_from_sensory"],
            confidence=record.confidence,
            decay_rate=record.decay_rate * 0.7,  # Slower decay in short-term
            written_by="consolidation_engine",
            origin=f"promoted_from:{record.id}",
            parent_id=record.id,
        )
        return promoted

    def promote_to_long_term(self, record: EmberRecord) -> EmberRecord:
        """
        Create a promoted version of a short-term record for long-term storage.
        """
        promoted = EmberRecord(
            id=str(uuid.uuid4()),
            namespace=self.long_term_ns,
            record_type=record.record_type,
            data=record.data,
            tags=record.tags + ["promoted_to_long_term"],
            confidence=min(record.confidence + 0.1, 1.0),
            decay_rate=record.decay_rate * 0.3,  # Much slower decay in long-term
            written_by="consolidation_engine",
            origin=f"promoted_from:{record.id}",
            parent_id=record.id,
            training_candidate=True,
        )
        return promoted
