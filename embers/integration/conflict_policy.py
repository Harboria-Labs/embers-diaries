"""Conflicts are a decision, not a write side-effect.

Write may HINT that two subject-rows disagree on a claim field.
Only the agent maps a conflict (ember_map_conflict).
content / room / kind / verify_status are never claims.
"""

from __future__ import annotations

import json

from ..core.types import RecordType

SKIP = frozenset({
    "subject", "content", "memory_type", "room", "verify_status",
})


def candidates_for(db, new_record) -> list[dict]:
    if not isinstance(getattr(new_record, "data", None), dict):
        return []
    subject = new_record.data.get("subject")
    if subject is None:
        return []
    try:
        existing = db.get_namespace(new_record.namespace, limit=200)
    except Exception:
        return []
    durable = frozenset({RecordType.NODE, RecordType.DOCUMENT})
    hints = []
    for rec in existing:
        if rec.id == new_record.id:
            continue
        if rec.record_type not in durable:
            continue
        if not isinstance(rec.data, dict):
            continue
        if rec.data.get("subject") != subject:
            continue
        for key, new_val in new_record.data.items():
            if key in SKIP:
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
                        "Possible disagreement. Map it with ember_map_conflict "
                        "only if you decide this is a real conflict."
                    ),
                })
                break
    return hints


def install() -> None:
    from .memory_protocol import MemoryProtocol
    MemoryProtocol._CONFLICT_SKIP_KEYS = SKIP
    MemoryProtocol._check_conflicts = _check_conflicts


def _check_conflicts(self, new_record):
    self._last_conflict_hints = candidates_for(self.db, new_record)


def install_mcp() -> None:
    from ..mcp import server
    orig = server.EmberMCP._call

    def _call(self, name: str, args: dict):
        result = orig(self, name, args)
        if name != "ember_write" or not isinstance(result, dict):
            return result
        if result.get("isError"):
            return result
        hints = getattr(self.protocol, "_last_conflict_hints", None) or []
        if not hints:
            return result
        try:
            body = json.loads(result["content"][0]["text"])
        except Exception:
            return result
        body["possible_conflicts"] = hints
        result["content"][0]["text"] = json.dumps(body)
        return result

    server.EmberMCP._call = _call
