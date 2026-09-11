"""
Ember's Diaries — Reflection Engine
Automated metacognitive reflection over stored memories.

Triggers reflective annotations when:
1. A memory's effective confidence drops below threshold
2. A conflict is detected
3. A consolidation event occurs
4. A scheduled reflection cycle runs

Implements the ReflectiveAnnotation system — the model
re-examining its own memories with new understanding.
"""

import uuid
from datetime import datetime, timezone
from typing import Callable

from ..core.record import EmberRecord
from ..core.annotation import Annotation, ReflectiveAnnotation
from .decay import DecayEngine


class ReflectionTrigger:
    """Represents a condition that triggers reflection."""

    def __init__(self, name: str, check_fn: Callable,
                 priority: float = 0.5):
        self.name = name
        self.check_fn = check_fn  # fn(record) -> bool
        self.priority = priority  # 0.0 - 1.0


class ReflectionEngine:
    """
    Metacognitive engine that generates reflective annotations.
    
    The engine periodically examines records and produces
    ReflectiveAnnotations capturing:
    - How understanding has changed
    - Whether past beliefs still hold
    - Connections discovered between memories
    - Confidence reassessments
    
    This is the "Emergence Engine" from the Lila architecture —
    the component that makes memory self-aware.

    Reflect comments on memories. It does not classify, set room,
    create memories, or change rank. When a stored room and kind
    exist, the comment names them (e.g. "this personal skill is fading").
    Missing labels are reported as unscoped, not guessed.
    """

    def __init__(self, decay_engine: DecayEngine | None = None,
                 conflict_detector=None,
                 confidence_threshold: float = 0.4,
                 reflection_cooldown_hours: float = 24.0):
        self._decay = decay_engine or DecayEngine()
        # No default instantiation: the old in-memory ConflictDetector this
        # took by default has been removed (conflict tracking is unified onto
        # the persisted conflict engine, see MemoryProtocol._check_conflicts).
        # conflict_detector is None unless a caller explicitly supplies an
        # object exposing has_conflicts()/get_conflicts() -- this makes the
        # conflict-based reflection branch below inert until reflect() is
        # repointed at db.conflicts_for(), rather than silently resurrecting
        # the removed dependency.
        self._conflicts = conflict_detector
        self._confidence_threshold = confidence_threshold
        self._cooldown_hours = reflection_cooldown_hours

        # Track when we last reflected on each record
        self._last_reflection: dict[str, datetime] = {}

        # Custom triggers
        self._triggers: list[ReflectionTrigger] = []

        # Generated annotations waiting to be written
        self._pending_annotations: list[ReflectiveAnnotation] = []

    def register_trigger(self, trigger: ReflectionTrigger):
        """Register a custom reflection trigger."""
        self._triggers.append(trigger)

    # ── Reflection cycle ─────────────────────────────────────

    def reflect(self, records: list[EmberRecord],
                context: str = "",
                written_by: str = "reflection_engine") -> list[ReflectiveAnnotation]:
        """
        Run a reflection cycle over a set of records.
        Returns generated ReflectiveAnnotations.
        """
        now = datetime.now(timezone.utc)
        annotations = []

        for record in records:
            if not self._should_reflect(record, now):
                continue

            # Check each reflection condition
            record_annotations = []

            # 1. Confidence decay
            eff_conf = self._decay.effective_confidence(record, now)
            if eff_conf < self._confidence_threshold:
                ann = self._create_decay_reflection(record, eff_conf, context, written_by)
                record_annotations.append(ann)

            # 2. Conflict detection (inert unless a conflict_detector was
            # explicitly supplied -- see __init__)
            if self._conflicts and self._conflicts.has_conflicts(record.id):
                conflicts = self._conflicts.get_conflicts(record.id)
                for conflict in conflicts:
                    if not conflict.resolved:
                        ann = self._create_conflict_reflection(
                            record, conflict, context, written_by)
                        record_annotations.append(ann)

            # 3. Custom triggers
            for trigger in self._triggers:
                if trigger.check_fn(record):
                    ann = self._create_trigger_reflection(
                        record, trigger, context, written_by)
                    record_annotations.append(ann)

            if record_annotations:
                self._last_reflection[record.id] = now
                annotations.extend(record_annotations)

        self._pending_annotations.extend(annotations)
        return annotations


    @staticmethod
    def _kind_and_room(record: EmberRecord) -> tuple[str, str]:
        """Read stored labels. Do not invent personal/project or a kind."""
        data = record.data if isinstance(record.data, dict) else {}
        kind = data.get("memory_type") or "unscoped"
        room = data.get("room") or "unscoped"
        return str(kind), str(room)

    def _should_reflect(self, record: EmberRecord, now: datetime) -> bool:
        """Check if enough time has passed since last reflection on this record."""
        last = self._last_reflection.get(record.id)
        if last is None:
            return True
        hours_since = (now - last).total_seconds() / 3600.0
        return hours_since >= self._cooldown_hours

    # ── Annotation generators ─────────────────────────────────

    def _create_decay_reflection(self, record: EmberRecord,
                                  effective_confidence: float,
                                  context: str,
                                  written_by: str) -> ReflectiveAnnotation:
        kind, room = self._kind_and_room(record)
        return ReflectiveAnnotation(
            target_record_id=record.id,
            content=(
                f"This {room} {kind} is fading. "
                f"Confidence has decayed to {effective_confidence:.2f} "
                f"(base: {record.confidence:.2f}). "
                f"It has not been accessed in a while. "
                f"Consider reinforcing or re-evaluating. "
                f"Reflect does not change the room or the kind."
            ),
            context=context or "scheduled_reflection_cycle",
            annotation_type="reflection",
            written_by=written_by,
            confidence=effective_confidence,
            triggered_by="confidence_decay",
            insight_score=1.0 - effective_confidence,  # More decayed = higher insight value
            tags=["decay", "needs_reinforcement", f"room:{room}", f"kind:{kind}"],
        )

    def _create_conflict_reflection(self, record: EmberRecord,
                                     conflict,
                                     context: str,
                                     written_by: str) -> ReflectiveAnnotation:
        kind, room = self._kind_and_room(record)
        return ReflectiveAnnotation(
            target_record_id=record.id,
            content=(
                f"This {room} {kind} has an unresolved conflict: "
                f"{conflict.description}. "
                f"It may be contested. "
                f"Conflicting records: {', '.join(conflict.record_ids)}. "
                f"Reflect does not change the room or the kind."
            ),
            context=context or "conflict_detected",
            annotation_type="reflection",
            written_by=written_by,
            confidence=1.0 - conflict.severity,
            triggered_by="conflict_detection",
            insight_score=conflict.severity,
            tags=["conflict", "needs_resolution", f"room:{room}", f"kind:{kind}"],
            related_record_ids=conflict.record_ids,
        )

    def _create_trigger_reflection(self, record: EmberRecord,
                                    trigger: ReflectionTrigger,
                                    context: str,
                                    written_by: str) -> ReflectiveAnnotation:
        kind, room = self._kind_and_room(record)
        return ReflectiveAnnotation(
            target_record_id=record.id,
            content=(
                f"Custom trigger '{trigger.name}' activated "
                f"for this {room} {kind}."
            ),
            context=context or f"trigger:{trigger.name}",
            annotation_type="reflection",
            written_by=written_by,
            confidence=1.0,
            triggered_by=trigger.name,
            insight_score=trigger.priority,
            tags=["custom_trigger", trigger.name, f"room:{room}", f"kind:{kind}"],
        )

    # ── Pending annotations ─────────────────────────────────

    def get_pending_annotations(self) -> list[ReflectiveAnnotation]:
        """Get annotations generated during reflection, ready to be written."""
        pending = list(self._pending_annotations)
        self._pending_annotations.clear()
        return pending

    def pending_count(self) -> int:
        return len(self._pending_annotations)

    def stats(self) -> dict:
        return {
            "records_reflected": len(self._last_reflection),
            "pending_annotations": len(self._pending_annotations),
            "custom_triggers": len(self._triggers),
        }
