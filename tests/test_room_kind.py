"""Room and kind labels on write. Kind ≠ room. Recall does not invent a room."""

from pathlib import Path

import pytest

from embers.db import EmberDB
from embers.integration.memory_protocol import MemoryProtocol
from embers.mcp.room_wire import install
from embers.mcp import server


@pytest.fixture
def protocol(tmp_path: Path):
    db = EmberDB.connect(str(tmp_path / "store"))
    return MemoryProtocol(db)


def test_remember_stores_room_and_kind(protocol):
    rid = protocol.remember(
        "Alex prefers surgical diffs",
        memory_type="skill",
        room="personal",
    )
    rec = protocol.db.get(rid)
    assert rec.data["memory_type"] == "skill"
    assert rec.data["room"] == "personal"


def test_forgotten_labels_are_unscoped(protocol):
    rid = protocol.remember("something happened")
    rec = protocol.db.get(rid)
    assert rec.data["memory_type"] == "unscoped"
    assert rec.data["room"] == "unscoped"


def test_unknown_labels_are_unscoped(protocol):
    rid = protocol.remember("x", memory_type="not-a-kind", room="not-a-room")
    rec = protocol.db.get(rid)
    assert rec.data["memory_type"] == "unscoped"
    assert rec.data["room"] == "unscoped"


def test_recall_filters_stored_room_only(protocol):
    protocol.remember("likes dark mode", memory_type="skill", room="personal")
    protocol.remember("this repo uses Prisma", memory_type="skill", room="project")
    protocol.remember("unlabelled note", memory_type="skill")

    personal = protocol.recall("mode", room="personal", format="raw")
    project = protocol.recall("Prisma", room="project", format="raw")
    unscoped = protocol.recall("note", room="unscoped", format="raw")

    assert all(r.data.get("room") == "personal" for r in personal)
    assert all(r.data.get("room") == "project" for r in project)
    assert all(r.data.get("room") == "unscoped" for r in unscoped)


def test_recall_does_not_invent_room_for_unscoped(protocol):
    protocol.remember("Alex likes dark mode")  # unscoped
    hits = protocol.recall("dark mode", room="personal", format="raw")
    assert hits == []


def test_mcp_schema_exposes_room_and_kind():
    install()
    write = next(t for t in server.TOOLS if t["name"] == "ember_write")
    recall = next(t for t in server.TOOLS if t["name"] == "ember_recall")
    assert "memory_type" in write["inputSchema"]["properties"]
    assert "room" in write["inputSchema"]["properties"]
    assert "room" in recall["inputSchema"]["properties"]
