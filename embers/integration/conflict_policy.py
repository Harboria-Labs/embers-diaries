"""Keep MemoryProtocol on the hint-only path.

install() attaches these methods so an old _check_conflicts body cannot auto-map.
"""

from ..core.types import RecordType

SKIP = frozenset({
    "subject", "content", "memory_type", "room", "verify_status",
})


def conflict_hints(self, new_record) -> list[dict]:
    if not isinstance(getattr(new_record, "data", None), dict):
        return []
    subject = new_record.data.get("subject")
    if subject is None:
        return []
    try:
        existing = self.db.get_namespace(new_record.namespace, limit=200)
    except Exception:
        return []
    durable = getattr(self, "_DURABLE_MEMORY_TYPES",
                      frozenset({RecordType.NODE, RecordType.DOCUMENT}))
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
                        "Possible disagreement. Map it with "
                        "ember_map_conflict only if you decide this "
                        "is a real conflict."
                    ),
                })
                break
    return hints


def _check_conflicts(self, new_record):
    self._last_conflict_hints = conflict_hints(self, new_record)


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


def install() -> None:
    from .memory_protocol import MemoryProtocol
    MemoryProtocol._CONFLICT_SKIP_KEYS = SKIP
    MemoryProtocol.conflict_hints = conflict_hints
    MemoryProtocol._check_conflicts = _check_conflicts
    MemoryProtocol._stamp_conflicts = _stamp_conflicts
    if getattr(MemoryProtocol.recall, "_ember_stamps_conflicts", False):
        return
    orig = MemoryProtocol.recall

    def recall(self, query, top_k=10, namespace=None, room=None,
               threshold=0.0, include_annotations=True, format="text"):
        result = orig(self, query, top_k=top_k, namespace=namespace, room=room,
                      threshold=threshold, include_annotations=include_annotations,
                      format=format)
        if format == "structured" and isinstance(result, list):
            return _stamp_conflicts(self, result)
        return result

    recall._ember_stamps_conflicts = True
    MemoryProtocol.recall = recall


def candidates_for(db, new_record) -> list[dict]:
    class _P:
        pass
    p = _P()
    p.db = db
    p._DURABLE_MEMORY_TYPES = frozenset({RecordType.NODE, RecordType.DOCUMENT})
    return conflict_hints(p, new_record)


def install_mcp() -> None:
    from ..mcp.conflict_surface import install as install_surface
    install_surface()
