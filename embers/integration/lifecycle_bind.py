"""Attach computed lifecycle onto MemoryProtocol without rewriting protocol.py."""

from ..cognitive.lifecycle import LifecycleEngine, LifecycleReport


def get_lifecycle(self, record_id: str) -> LifecycleReport | None:
    record = self.db._reader.get(record_id, include_deprecated=True,
                                 include_superseded=True)
    if record is None:
        return None
    if not hasattr(self, "lifecycle"):
        self.lifecycle = LifecycleEngine(getattr(self, "decay", None))
    has_open_conflict = len(self.db.conflicts_for(record_id)) > 0
    return self.lifecycle.classify(record, has_open_conflict=has_open_conflict)


def bind(MemoryProtocol) -> None:
    if getattr(MemoryProtocol, "_lifecycle_bound", False):
        return
    orig = MemoryProtocol.__init__

    def __init__(self, *args, **kwargs):
        orig(self, *args, **kwargs)
        if not hasattr(self, "lifecycle"):
            self.lifecycle = LifecycleEngine(self.decay)

    MemoryProtocol.__init__ = __init__
    MemoryProtocol.get_lifecycle = get_lifecycle
    MemoryProtocol._lifecycle_bound = True
