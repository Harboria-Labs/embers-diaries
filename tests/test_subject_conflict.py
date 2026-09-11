"""Same subject + different content is not a conflict."""

from pathlib import Path

import pytest

from embers.db import EmberDB
from embers.integration.memory_protocol import MemoryProtocol
from embers.integration.conflict_policy import install


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


def test_same_subject_different_claim_field_is_a_conflict(protocol):
    a = protocol.remember(
        {"content": "note a", "subject": "server-probe", "status": "green"},
        room="project",
    )
    b = protocol.remember(
        {"content": "note b", "subject": "server-probe", "status": "red"},
        room="project",
    )
    found = protocol.db.conflicts_for(a)
    assert len(found) == 1
    assert "Field 'status'" in found[0].note
