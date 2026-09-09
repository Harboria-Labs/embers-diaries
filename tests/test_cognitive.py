"""
Ember's Diaries — Tests for Cognitive Engine (Phase 4)
Decay, consolidation, conflict detection, episodic segmentation, reflection.
"""

import pytest
import time
from datetime import datetime, timedelta, timezone

from embers import EmberDB, EmberRecord, RecordType
from embers.cognitive.decay import DecayEngine
from embers.cognitive.consolidation import ConsolidationEngine
from embers.cognitive.episodic import EpisodicSegmenter, Episode
from embers.cognitive.reflection import ReflectionEngine, ReflectionTrigger


# ── Decay Engine ──────────────────────────────────────────────────────────────

class TestDecayEngine:
    def test_no_decay_when_rate_zero(self):
        engine = DecayEngine()
        r = EmberRecord(confidence=1.0, decay_rate=0.0)
        assert engine.effective_confidence(r) == 1.0

    def test_confidence_decays_over_time(self):
        engine = DecayEngine()
        r = EmberRecord(
            confidence=1.0,
            decay_rate=0.1,
            created_at=datetime.now(timezone.utc) - timedelta(hours=10),
        )
        eff = engine.effective_confidence(r)
        assert eff < 1.0
        assert eff > 0.0

    def test_reinforcement_slows_decay(self):
        engine = DecayEngine()
        now = datetime.now(timezone.utc)
        past = now - timedelta(hours=10)

        # Record with no accesses
        r1 = EmberRecord(
            confidence=1.0, decay_rate=0.1,
            created_at=past, access_count=0)
        # Record with many accesses
        r2 = EmberRecord(
            confidence=1.0, decay_rate=0.1,
            created_at=past, access_count=10)

        eff1 = engine.effective_confidence(r1, now)
        eff2 = engine.effective_confidence(r2, now)

        assert eff2 > eff1  # More accesses = slower decay

    def test_should_reinforce(self):
        engine = DecayEngine()
        r = EmberRecord(
            confidence=1.0, decay_rate=1.0,  # Fast decay
            created_at=datetime.now(timezone.utc) - timedelta(hours=24),
        )
        assert engine.should_reinforce(r, threshold=0.5) is True

    def test_decay_summary(self):
        engine = DecayEngine()
        r = EmberRecord(confidence=0.8, decay_rate=0.05)
        summary = engine.decay_summary(r)
        assert "record_id" in summary
        assert "effective_confidence" in summary
        assert "retention_percentage" in summary

    def test_rank_by_urgency(self):
        engine = DecayEngine()
        past = datetime.now(timezone.utc) - timedelta(hours=48)
        records = [
            EmberRecord(confidence=1.0, decay_rate=0.5, created_at=past),
            EmberRecord(confidence=1.0, decay_rate=0.01, created_at=past),
            EmberRecord(confidence=1.0, decay_rate=1.0, created_at=past),
        ]
        ranked = engine.rank_by_urgency(records)
        # Highest decay rate should be first (most urgent)
        assert ranked[0][1] < ranked[-1][1]


# ── Consolidation Engine ──────────────────────────────────────────────────────

class TestConsolidation:
    def test_classify_incoming(self):
        engine = ConsolidationEngine()
        # Default → sensory
        r = EmberRecord(confidence=0.5)
        assert engine.classify_incoming(r) == "sensory"
        # High confidence + important tag → long_term
        r2 = EmberRecord(confidence=0.9, tags=["important"])
        assert engine.classify_incoming(r2) == "long_term"

    def test_find_consolidation_candidates(self):
        engine = ConsolidationEngine()
        records = [
            EmberRecord(data={"i": 0}, tags=["topic_a"]),
            EmberRecord(data={"i": 1}, tags=["topic_a"]),
            EmberRecord(data={"i": 2}, tags=["topic_a"]),
            EmberRecord(data={"i": 3}, tags=["topic_b"]),
        ]
        groups = engine.find_consolidation_candidates(records)
        # Should find at least one group (the topic_a records)
        assert len(groups) >= 1

    def test_create_consolidation_record(self):
        engine = ConsolidationEngine()
        records = [
            EmberRecord(data={"content": "fact 1"}, tags=["science"],
                       confidence=0.8, decay_rate=0.1),
            EmberRecord(data={"content": "fact 2"}, tags=["science"],
                       confidence=0.9, decay_rate=0.2),
        ]
        consolidated = engine.create_consolidation_record(records)
        assert consolidated.namespace == "long_term"
        assert "consolidated" in consolidated.tags
        assert consolidated.data["source_count"] == 2
        assert consolidated.decay_rate < 0.1  # Slower decay after consolidation

    def test_promote_to_short_term(self):
        engine = ConsolidationEngine()
        r = EmberRecord(namespace="sensory", decay_rate=0.1)
        promoted = engine.promote_to_short_term(r)
        assert promoted.namespace == "short_term"
        assert promoted.decay_rate < r.decay_rate
        assert promoted.parent_id == r.id

    def test_promote_to_long_term(self):
        engine = ConsolidationEngine()
        r = EmberRecord(namespace="short_term", decay_rate=0.1)
        promoted = engine.promote_to_long_term(r)
        assert promoted.namespace == "long_term"
        assert promoted.training_candidate is True


# ── Conflict Detection ────────────────────────────────────────────────────────
# The old ConflictDetector (in-memory only, never persisted) has been removed.
# Conflict detection is now unified onto the persisted conflict engine in
# EmberDB (map_conflict/conflicts_for/resolve_conflict, spec §7), exercised
# via MemoryProtocol._check_conflicts() and tested in test_conflict_engine.py
# / test_promotion_engine.py rather than here.


# ── Episodic Segmentation ────────────────────────────────────────────────────

class TestEpisodicSegmentation:
    def test_segment_by_time_gap(self):
        segmenter = EpisodicSegmenter(temporal_gap_minutes=1)
        now = datetime.utcnow()
        records = [
            EmberRecord(data={"i": 0}, created_at=now,
                       tags=["topic"]),
            EmberRecord(data={"i": 1}, created_at=now + timedelta(seconds=10),
                       tags=["topic"]),
            # Big gap
            EmberRecord(data={"i": 2}, created_at=now + timedelta(minutes=5),
                       tags=["topic"]),
            EmberRecord(data={"i": 3}, created_at=now + timedelta(minutes=5, seconds=10),
                       tags=["topic"]),
        ]
        episodes = segmenter.segment(records)
        assert len(episodes) >= 2  # Should split at the gap

    def test_segment_by_topic_shift(self):
        segmenter = EpisodicSegmenter(temporal_gap_minutes=60)
        now = datetime.utcnow()
        records = [
            EmberRecord(data={}, created_at=now, tags=["cooking", "food"]),
            EmberRecord(data={}, created_at=now + timedelta(seconds=5),
                       tags=["cooking", "food"]),
            # Topic shift
            EmberRecord(data={}, created_at=now + timedelta(seconds=10),
                       tags=["astronomy", "science"], namespace="other"),
            EmberRecord(data={}, created_at=now + timedelta(seconds=15),
                       tags=["astronomy", "science"], namespace="other"),
        ]
        episodes = segmenter.segment(records)
        assert len(episodes) >= 2

    def test_episode_has_properties(self):
        segmenter = EpisodicSegmenter()
        now = datetime.utcnow()
        records = [
            EmberRecord(data={"i": 0}, created_at=now, tags=["a"]),
            EmberRecord(data={"i": 1}, created_at=now + timedelta(seconds=1), tags=["a"]),
        ]
        episodes = segmenter.segment(records)
        assert len(episodes) >= 1
        ep = episodes[0]
        assert ep.size >= 1
        assert ep.start_time is not None
        assert ep.importance >= 0

    def test_get_recent_episodes(self):
        segmenter = EpisodicSegmenter(temporal_gap_minutes=1)
        now = datetime.utcnow()
        records = []
        for i in range(10):
            records.append(EmberRecord(
                data={"i": i},
                created_at=now + timedelta(minutes=i * 5),
                tags=[f"topic_{i % 2}"],
            ))
        segmenter.segment(records)
        recent = segmenter.get_recent_episodes(limit=3)
        assert len(recent) <= 3

    def test_stats(self):
        segmenter = EpisodicSegmenter()
        now = datetime.utcnow()
        records = [EmberRecord(data={"i": i}, created_at=now + timedelta(seconds=i))
                   for i in range(5)]
        segmenter.segment(records)
        stats = segmenter.stats()
        assert stats["episode_count"] >= 1


# ── Reflection Engine ─────────────────────────────────────────────────────────

class TestReflection:
    def test_reflect_on_decayed_memories(self):
        engine = ReflectionEngine(confidence_threshold=0.5)
        past = datetime.now(timezone.utc) - timedelta(hours=100)
        records = [
            EmberRecord(confidence=1.0, decay_rate=0.5, created_at=past),
        ]
        annotations = engine.reflect(records)
        assert len(annotations) >= 1
        assert annotations[0].triggered_by == "confidence_decay"

    def test_custom_trigger(self):
        engine = ReflectionEngine()
        trigger = ReflectionTrigger(
            name="high_value",
            check_fn=lambda r: isinstance(r.data, dict) and r.data.get("value", 0) > 100,
            priority=0.9,
        )
        engine.register_trigger(trigger)

        records = [EmberRecord(data={"value": 200})]
        annotations = engine.reflect(records)
        assert len(annotations) >= 1

    def test_cooldown_prevents_repeated_reflection(self):
        engine = ReflectionEngine(
            confidence_threshold=0.5,
            reflection_cooldown_hours=24.0)
        past = datetime.now(timezone.utc) - timedelta(hours=100)
        records = [EmberRecord(confidence=1.0, decay_rate=0.5, created_at=past)]

        # First reflection
        ann1 = engine.reflect(records)
        # Second immediate reflection — should be blocked by cooldown
        ann2 = engine.reflect(records)
        assert len(ann1) >= 1
        assert len(ann2) == 0

    def test_pending_annotations(self):
        engine = ReflectionEngine(confidence_threshold=0.5)
        past = datetime.now(timezone.utc) - timedelta(hours=100)
        records = [EmberRecord(confidence=1.0, decay_rate=0.5, created_at=past)]
        engine.reflect(records)

        pending = engine.get_pending_annotations()
        assert len(pending) >= 1
        # After retrieval, pending should be empty
        assert engine.pending_count() == 0
