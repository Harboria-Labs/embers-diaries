"""Shared, provider-neutral conflict contract for MCP and REST.

Transport adapters authenticate callers. This module applies the same public
status vocabulary and response identity rules before delegating all durable
behavior and authorization to :class:`EmberDB`.
"""

from __future__ import annotations

from ..core.types import ConflictStatus, ConflictType


OPEN_QUEUE_STATUSES = frozenset({
    ConflictStatus.OPEN,
    ConflictStatus.INVESTIGATING,
})

PUBLIC_TRANSITIONS = {
    "investigating": ConflictStatus.INVESTIGATING,
    "resolved": ConflictStatus.RESOLVED,
    "accepted_both": ConflictStatus.ACCEPTED_BOTH,
    "dismissed": ConflictStatus.SUPERSEDED,
    "superseded": ConflictStatus.SUPERSEDED,
}


def conflict_payload(conflict) -> dict:
    """Serialize one conflict with stable and immutable record identities."""
    return conflict.to_dict()


def map_conflict(db, *, memory_a: str, memory_b: str, actor_id: str,
                 conflict_type: str = "semantic", note: str = "") -> dict:
    cid = db.map_conflict(
        memory_a,
        memory_b,
        detected_by=actor_id,
        conflict_type=ConflictType(conflict_type),
        note=note,
    )
    conflict = db.get_conflict(cid, caller=actor_id)
    return {
        "conflict_id": conflict.conflict_id,
        "record_id": conflict.record_id,
        "conflict": conflict_payload(conflict),
    }


def conflicts_for(db, *, memory_id: str, actor_id: str,
                  include_closed: bool = False) -> list[dict]:
    return [
        conflict_payload(conflict)
        for conflict in db.conflicts_for(
            memory_id, include_closed=include_closed, caller=actor_id)
    ]


def open_conflicts(db, *, namespace: str, actor_id: str) -> list[dict]:
    return [
        conflict_payload(conflict)
        for conflict in db.conflict_records(namespace=namespace, caller=actor_id)
        if conflict.status in OPEN_QUEUE_STATUSES
    ]


def transition_conflict(db, *, conflict_id: str, actor_id: str,
                        status: str = "resolved", resolution: str = "",
                        winner_id: str | None = None) -> dict:
    raw_status = status.lower()
    resolved_status = PUBLIC_TRANSITIONS.get(raw_status)
    if resolved_status is None:
        raise ValueError(
            "status must be investigating, resolved, accepted_both, "
            "dismissed, or superseded")
    if raw_status == "dismissed" and "dismissed" not in resolution.lower():
        resolution = (resolution + " dismissed: not a conflict").strip()

    new_id, old_id = db.update_conflict_status(
        conflict_id,
        resolved_status,
        resolution,
        changed_by=actor_id,
        winner_id=winner_id,
    )
    conflict = db.get_conflict(new_id, caller=actor_id)
    return {
        "conflict_id": conflict.conflict_id,
        "record_id": conflict.record_id,
        "superseded_record_id": old_id,
        "status": conflict.status.value,
        "conflict": conflict_payload(conflict),
    }
