"""Same subject is a hint. Only the agent maps a conflict."""

from pathlib import Path

import pytest

from embers.db import EmberDB
from embers.integration.memory_protocol import MemoryProtocol
from embers.integration.conflict_policy import candidates_for, install


@pytest.fixture
def protocol(tmp_path: Path):
    install()
    db = EmberDB.connect(str(tmp_path / "store"))
    return MemoryProtocol(db, default_namespace="cf")


def test_different_content_same_subject_is_not_a_conflict(protocol):
    a = protocol.remember(
        {"content": "server-probe status is green.", "subject": "server-probe"},
        room="project",
    )
    b = protocol.remember(
        {"content": "server-probe status is red.", "subject": "server-probe"},
        room="project",
    )
    assert protocol.db.conflicts_for(a) == []
    assert protocol.db.conflicts_for(b) == []
    assert protocol._last_conflict_hints == []


def test_claim_field_is_only_a_hint_until_agent_maps(protocol):
    a = protocol.remember(
        {"content": "note a", "subject": "server-probe", "status": "green"},
        room="project",
    )
    b = protocol.remember(
        {"content": "note b", "subject": "server-probe", "status": "red"},
        room="project",
    )
    assert protocol.db.conflicts_for(a) == []
    hints = protocol._last_conflict_hints
    assert len(hints) == 1
    assert hints[0]["field"] == "status"
    assert hints[0]["other_id"] == a

    protocol.db.map_conflict(a, b, detected_by="agent", note="agent decided")
    found = protocol.db.conflicts_for(a)
    assert len(found) == 1
