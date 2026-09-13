"""Feedback + lifecycle + opt-in maintenance on current main."""

from pathlib import Path
import json
import pytest

from embers.db import EmberDB
from embers.db_feedback import bind as bind_feedback
bind_feedback(EmberDB)
from embers.core.record import EmberRecord
from embers.core.feedback import Feedback, FeedbackOutcome
from embers.core.types import RecordType
from embers.cognitive.lifecycle import LifecycleState
from embers.integration.memory_protocol import MemoryProtocol
from embers.maintenance import run_maintenance_cycle
from embers.mcp.session_auth import install as install_session_auth
from embers.mcp.lobby_surface import install as install_lobby, STORE
from embers.mcp.session_collab import install as install_collab
from embers.mcp.server import EmberMCP, TOOLS


def _body(result):
    assert not result["isError"], result["content"][0]["text"]
    return json.loads(result["content"][0]["text"])


@pytest.fixture
def db(tmp_path: Path):
    return EmberDB.connect(str(tmp_path / "store"))


def test_feedback_on_memory_does_not_rewrite(db):
    mid = db.write(EmberRecord(namespace="memories", data={"content": "door is green"}))
    before = db.get(mid).content_hash
    fid = db.give_feedback(mid, Feedback(
        memory_id=mid, agent_id="a", outcome=FeedbackOutcome.USEFUL,
    ))
    assert db.get(mid).content_hash == before
    assert db.get(fid).record_type == RecordType.FEEDBACK
    assert len(db.feedback_for(mid)) == 1


def test_feedback_rejects_proposal(db):
    from embers.core.proposal import MemoryProposal
    pid = db.propose(MemoryProposal(discovery={"content": "x"}, reason="r"))
    with pytest.raises(ValueError):
        db.give_feedback(pid, Feedback(
            memory_id=pid, agent_id="a", outcome=FeedbackOutcome.INCORRECT,
        ))


def test_lifecycle_disputed_uses_mapped_conflict(db):
    proto = MemoryProtocol(db)
    a = db.write(EmberRecord(namespace="memories", data={"content": "green"}))
    b = db.write(EmberRecord(namespace="memories", data={"content": "red"}))
    report = proto.get_lifecycle(a)
    assert report.state == LifecycleState.ACTIVE
    db.map_conflict(a, b, detected_by="agent")
    report = proto.get_lifecycle(a)
    assert report.state == LifecycleState.DISPUTED
    assert report.has_open_conflict is True


def test_maintenance_cycle_does_not_raise(db):
    proto = MemoryProtocol(db)
    db.write(EmberRecord(namespace="memories", data={"content": "n"}))
    out = run_maintenance_cycle(proto, ["memories"])
    assert "memories" in out
