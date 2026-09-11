"""Subject conflicts compare claims, not prose.

content / room / kind / verify_status are not claim fields.
Two notes about the same subject with different wording stay both durable.
A conflict opens only when another field on the record disagrees.
"""

from ..core.types import RecordType

SKIP = frozenset({
    "subject", "content", "memory_type", "room", "verify_status",
})


def install() -> None:
    from .memory_protocol import MemoryProtocol
    MemoryProtocol._CONFLICT_SKIP_KEYS = SKIP
    MemoryProtocol._check_conflicts = _check_conflicts


def _check_conflicts(self, new_record):
    if not isinstance(new_record.data, dict):
        return
    subject = new_record.data.get("subject")
    if subject is None:
        return
    try:
        existing = self.db.get_namespace(new_record.namespace, limit=200)
    except Exception:
        return
    durable = getattr(self, "_DURABLE_MEMORY_TYPES",
                      frozenset({RecordType.NODE, RecordType.DOCUMENT}))
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
                try:
                    self.db.map_conflict(
                        rec.id, new_record.id,
                        detected_by=new_record.written_by or "conflict-detector",
                        note=(f"Field {key!r} differs for subject "
                              f"{subject!r}: {old_val!r} vs {new_val!r}"),
                    )
                except Exception:
                    pass
                break
