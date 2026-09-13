"""Feedback methods bound onto EmberDB."""

from enum import Enum

from .core.record import EmberRecord
from .core.edge import EdgeRef
from .core import types as _types
from .core.feedback import Feedback


def _ensure_enums():
    if not hasattr(_types.RecordType, "FEEDBACK"):
        members = {m.name: m.value for m in _types.RecordType}
        members["FEEDBACK"] = "feedback"
        new_rt = Enum("RecordType", members, type=str)
        _types.RecordType = new_rt
        from .core import record as record_mod
        record_mod.RecordType = new_rt
    if not hasattr(_types.EdgeType, "FEEDBACK_ON"):
        members = {m.name: m.value for m in _types.EdgeType}
        members["FEEDBACK_ON"] = "feedback_on"
        new_et = Enum("EdgeType", members, type=str)
        _types.EdgeType = new_et


_ensure_enums()
RecordType = _types.RecordType
EdgeType = _types.EdgeType


def give_feedback(self, memory_id: str, fb: Feedback) -> str:
    target = self._reader.get(memory_id, include_deprecated=True,
                              include_superseded=True)
    if target is None:
        raise KeyError(f"Memory {memory_id} not found.")
    if target.record_type not in self._DURABLE_MEMORY_TYPES:
        raise ValueError(
            f"{memory_id} is a {target.record_type.value} record, not a "
            f"durable memory — give_feedback() only accepts NODE or DOCUMENT.")
    edge = EdgeRef(
        edge_id=f"feedback_on:{fb.id}:{memory_id}",
        target_id=memory_id,
        edge_type=EdgeType.FEEDBACK_ON,
        label="feedback_on",
    )
    rec = EmberRecord(
        id=fb.id,
        namespace=target.namespace,
        record_type=RecordType.FEEDBACK,
        data=fb.to_dict(),
        connections=[edge],
        written_by=fb.agent_id,
        agent_id=fb.agent_id,
        session_id=fb.session_id,
    )
    return self._writer.write(rec)


def feedback_for(self, memory_id: str):
    ids = set()
    for e in self._graph_index.get_edges(memory_id, direction="incoming"):
        if e["edge_type"] == EdgeType.FEEDBACK_ON.value:
            ids.add(e["target"])
    return self._resolve_ids(ids, True, True)


def get_feedback(self, feedback_id: str):
    rec = self._reader.get(feedback_id, include_deprecated=True,
                           include_superseded=True)
    if rec is None or rec.record_type != RecordType.FEEDBACK:
        return None
    return Feedback.from_dict(rec.data)


def bind(EmberDB):
    EmberDB.give_feedback = give_feedback
    EmberDB.feedback_for = feedback_for
    EmberDB.get_feedback = get_feedback
