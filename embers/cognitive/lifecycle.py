"""Computed lifecycle. Not a stored field."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..core.record import EmberRecord
from .decay import DecayEngine


class LifecycleState(str, Enum):
    ACTIVE = "active"
    VERIFIED = "verified"
    REINFORCED = "reinforced"
    WEAKENING = "weakening"
    STALE = "stale"
    DISPUTED = "disputed"
    ARCHIVED = "archived"


@dataclass
class LifecycleReport:
    state: LifecycleState
    effective_confidence: float
    access_count: int
    has_open_conflict: bool

    def to_dict(self) -> dict:
        return {
            "state": self.state.value,
            "effective_confidence": round(self.effective_confidence, 4),
            "access_count": self.access_count,
            "has_open_conflict": self.has_open_conflict,
        }


class LifecycleEngine:
    def __init__(self, decay_engine: DecayEngine | None = None,
                 reinforced_access_threshold: int = 5,
                 weakening_threshold: float = 0.5,
                 stale_threshold: float = 0.15):
        self._decay = decay_engine or DecayEngine()
        self.reinforced_access_threshold = reinforced_access_threshold
        self.weakening_threshold = weakening_threshold
        self.stale_threshold = stale_threshold

    def classify(self, record: EmberRecord,
                 has_open_conflict: bool = False) -> LifecycleReport:
        eff_conf = self._decay.effective_confidence(record)
        if record.deprecated:
            state = LifecycleState.ARCHIVED
        elif has_open_conflict:
            state = LifecycleState.DISPUTED
        elif eff_conf <= self.stale_threshold:
            state = LifecycleState.STALE
        elif eff_conf <= self.weakening_threshold:
            state = LifecycleState.WEAKENING
        elif record.access_count >= self.reinforced_access_threshold:
            state = LifecycleState.REINFORCED
        elif (record.data or {}).get("verify_status") == "verified":
            state = LifecycleState.VERIFIED
        else:
            state = LifecycleState.ACTIVE
        return LifecycleReport(
            state=state,
            effective_confidence=eff_conf,
            access_count=record.access_count,
            has_open_conflict=has_open_conflict,
        )
