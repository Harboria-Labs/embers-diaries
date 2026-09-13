"""Feedback methods bound onto EmberDB. Keeps db.py from growing another 80k upload."""

from .core.record import EmberRecord
from .core.edge import EdgeRef
from .core.types import RecordType, EdgeType
from .core.feedback import Feedback


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
