"""
Ember's Diaries — Memory Lifecycle (spec §11)

A memory previously only carried scattered signals — confidence,
verify_status, access_count, decay_rate — with nothing that read them
together as a single state. This module doesn't add a new persisted field;
it computes a classification from signals that already exist, the same way
DecayEngine.effective_confidence reads created_at/decay_rate/access_count
without storing a separate "current confidence" field anywhere.

That's a deliberate choice, not a shortcut: a persisted lifecycle field
would be a SECOND source of truth that could drift out of sync with the
decay/access/conflict data actually driving it (imagine a memory marked
"active" in a stored field while its decayed confidence has actually
dropped to near zero, because nothing remembered to update the field).
Computing it fresh from the record's real state on every read means it
can never go stale on its own.

States, from the architecture review's two example lifecycles merged into
one (spec §11 gave two overlapping proposals — Candidate→Stable and
Active→Archived — this reconciles them into a single ordering a record can
actually move through as its real signals change):

    ACTIVE      → freshly written/promoted, nothing has happened yet
    VERIFIED    → promotion engine marked it VerifyStatus.verified
    REINFORCED  → accessed often AND still confident (decay hasn't caught up)
    WEAKENING   → decayed confidence dropping, not yet stale
    STALE       → decayed confidence very low — a candidate for reflection
    DISPUTED    → has a live, unresolved mapped conflict (spec §7)
    ARCHIVED    → deprecated

DISPUTED and ARCHIVED are checked first regardless of confidence — a
conflicted or deprecated memory needs attention even if it happens to still
be strongly confident.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..core.record import EmberRecord
from .decay import DecayEngine


class LifecycleState(str, Enum):
    ACTIVE     = "active"
    VERIFIED   = "verified"
    REINFORCED = "reinforced"
    WEAKENING  = "weakening"
    STALE      = "stale"
    DISPUTED   = "disputed"
    ARCHIVED   = "archived"


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
        """Never modifies the record — pure computation, like
        DecayEngine.effective_confidence."""
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
