"""Lobby SSE ordering, wakeup, authorization and bounded cursor tests."""

import asyncio
import json
import sqlite3

from embers.db import EmberDB
from embers.lobby import working
from embers.lobby.realtime_v2 import stream, _listeners


def _kind(frame):
    return next(line[7:] for line in frame.splitlines() if line.startswith("event: "))


def _data(frame):
    return json.loads(next(line[6:] for line in frame.splitlines() if line.startswith("data: ")))


def _setup(tmp_path):
    db = EmberDB.connect(str(tmp_path / "store"))
    a = db.start_session("author", task="task", namespace="shared")
    b = db.start_session("reader", task="task", namespace="shared")
    scope = working.create_scope(db, a, "author", task="task", namespace="shared")["scope_id"]
    working.grant(db, scope, a, "author", "reader")
    return db, a, b, scope


def _submit(db, scope, session, key):
    return working.submit(db, scope, session, "author", {
        "client_submission_id": key, "share": True, "kind": "finding",
        "claim": "parser " + key, "uncertainty": "unverified"})


def test_committed_push_reconnect_and_revocation(tmp_path):
    db, a, b, scope = _setup(tmp_path)
    async def run():
        async def connected():
            return False
        start = working.events(db, scope, b, "reader")["cursor"]
        gen = stream(db, scope, b, "reader", start, connected, auth_interval=0.1)
        assert _kind(await anext(gen)) == "ready"
        pending = asyncio.create_task(anext(gen))
        await asyncio.sleep(0)
        item = _submit(db, scope, a, "one")
        frame = await asyncio.wait_for(pending, 1)
        assert _kind(frame) == "working_memory_changed"
        assert _data(frame)["item_id"] == item["item_id"]
        cursor = _data(frame)["cursor"]
        await gen.aclose()
        _submit(db, scope, a, "two")
        reopened = EmberDB.connect(str(db._path))
        replay = stream(reopened, scope, b, "reader", cursor, connected)
        assert _kind(await anext(replay)) == "ready"
        assert _data(await anext(replay))["change"] == "changed"
        await replay.aclose()
        live = stream(db, scope, b, "reader", working.events(db, scope, b, "reader")["cursor"], connected)
        await anext(live)
        pending = asyncio.create_task(anext(live))
        await asyncio.sleep(0)
        working.revoke(db, scope, a, "author", "reader")
        assert _kind(await asyncio.wait_for(pending, 1)) == "denied"
        await live.aclose()
        assert not _listeners
    asyncio.run(run())


def test_missed_history_reset_and_context_signal_only(tmp_path, monkeypatch):
    db, a, b, scope = _setup(tmp_path)
    monkeypatch.setattr(working, "MAX_EVENTS_PER_SCOPE", 2)
    for i in range(4):
        _submit(db, scope, a, str(i))
    page = working.events(db, scope, b, "reader", after=0)
    assert page["reset"] and page["reason"] == "missed_history"
    assert not any("claim" in event for event in page["events"])
    async def run():
        async def connected():
            return False
        gen = stream(db, scope, b, "reader", 0, connected)
        assert _kind(await anext(gen)) == "ready"
        assert _kind(await anext(gen)) == "reset"
        await gen.aclose()
    asyncio.run(run())


def test_batch1_scope_file_migrates_in_place(tmp_path):
    db = EmberDB.connect(str(tmp_path / "store"))
    sid = db.start_session("author", task="task", namespace="shared")
    with sqlite3.connect(working.path(db)) as conn:
        conn.execute("CREATE TABLE scopes (id TEXT PRIMARY KEY, namespace TEXT, task TEXT, room TEXT, owner TEXT, status TEXT, created REAL)")
    scope = working.create_scope(db, sid, "author", task="task", namespace="shared")["scope_id"]
    assert working.events(db, scope, sid, "author")["reset"] is False
