"""Agent decides conflicts. Write only hints. Resolver has real options."""

from pathlib import Path

import pytest

from embers.core.types import ConflictStatus
from embers.db import EmberDB
from embers.integration.memory_protocol import MemoryProtocol


@pytest.fixture
def protocol(tmp_path: Path):
    db = EmberDB.connect(str(tmp_path / "store"))
    return MemoryProtocol(db, default_namespace="cf")


def test_different_content_same_subject_is_not_mapped(protocol):
    a = protocol.remember(
        {"content": "green", "subject": "server-probe"}, room="project")
    b = protocol.remember(
        {"content": "red", "subject": "server-probe"}, room="project")
    assert protocol.db.conflicts_for(a) == []
    assert protocol.db.conflicts_for(b) == []
    assert protocol._last_conflict_hints == []


def test_claim_field_is_hint_only_until_mapped(protocol):
    a = protocol.remember(
        {"content": "a", "subject": "server-probe", "status": "green"},
        room="project")
    b = protocol.remember(
        {"content": "b", "subject": "server-probe", "status": "red"},
        room="project")
    assert protocol.db.conflicts_for(a) == []
    hints = protocol._last_conflict_hints
    assert len(hints) == 1
    assert hints[0]["field"] == "status"
    cid = protocol.db.map_conflict(a, b, detected_by="agent")
    assert protocol.db.get_conflict(cid).status == ConflictStatus.OPEN


def test_resolve_statuses(protocol):
    a = protocol.remember(
        {"content": "a", "subject": "x", "backend": "pg"}, room="project")
    b = protocol.remember(
        {"content": "b", "subject": "x", "backend": "mongo"}, room="project")
    cid = protocol.db.map_conflict(a, b, detected_by="agent")
    protocol.db.update_conflict_status(
        cid, ConflictStatus.INVESTIGATING, "looking", "agent")
    assert protocol.db.get_conflict(cid).status == ConflictStatus.INVESTIGATING
    protocol.db.update_conflict_status(
        cid, ConflictStatus.ACCEPTED_BOTH, "both true", "agent")
    assert protocol.db.get_conflict(cid).status == ConflictStatus.ACCEPTED_BOTH


def test_dismissed_uses_superseded(protocol):
    a = protocol.remember(
        {"content": "a", "subject": "x", "backend": "pg"}, room="project")
    b = protocol.remember(
        {"content": "b", "subject": "x", "backend": "mongo"}, room="project")
    cid = protocol.db.map_conflict(a, b, detected_by="agent")
    protocol.db.update_conflict_status(
        cid, ConflictStatus.SUPERSEDED, "dismissed: not a conflict", "agent")
    assert protocol.db.get_conflict(cid).status == ConflictStatus.SUPERSEDED
    assert protocol.db.conflicts_for(a) == []


def test_structured_recall_marks_open_conflict(protocol):
    a = protocol.remember(
        {"content": "a", "subject": "x", "backend": "pg"}, room="project")
    b = protocol.remember(
        {"content": "b", "subject": "x", "backend": "mongo"}, room="project")
    protocol.db.map_conflict(a, b, detected_by="agent")
    rows = protocol.recall("mongo", format="structured", top_k=10)
    marked = [r for r in rows if r.get("conflict") == "open"]
    assert marked
