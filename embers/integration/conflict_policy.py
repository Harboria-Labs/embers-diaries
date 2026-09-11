"""Compatibility helpers. Real check lives on MemoryProtocol.

Do not map conflicts here. Do not replace protocol methods.
"""

from .memory_protocol import MemoryProtocol


SKIP = MemoryProtocol._CONFLICT_SKIP_KEYS


def candidates_for(db, new_record) -> list[dict]:
    proto = MemoryProtocol(db)
    return proto.conflict_hints(new_record)


def install() -> None:
    return


def install_mcp() -> None:
    from ..mcp.conflict_surface import install as install_surface
    install_surface()
