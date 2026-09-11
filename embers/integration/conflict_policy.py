"""Thin helpers. The real check lives on MemoryProtocol.

Do not map conflicts. Do not replace protocol methods.
"""

from .memory_protocol import MemoryProtocol

SKIP = MemoryProtocol._CONFLICT_SKIP_KEYS


def candidates_for(db, new_record) -> list[dict]:
    return MemoryProtocol(db).conflict_hints(new_record)


def install() -> None:
    return
