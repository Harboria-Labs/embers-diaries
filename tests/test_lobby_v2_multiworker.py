"""A distinct worker receives a committed Lobby event without event polling."""

import asyncio
import multiprocessing

import pytest

from embers.db import EmberDB
from embers.lobby import working
from embers.lobby import realtime_v2
from embers.lobby.realtime_v2 import stream


def _reader(store, scope, session, result):
    async def run():
        db = EmberDB.connect(store)

        async def connected():
            return False

        cursor = working.events(db, scope, session, "reader")["cursor"]
        feed = stream(db, scope, session, "reader", cursor, connected)
        result.put(await anext(feed))
        try:
            result.put(await asyncio.wait_for(anext(feed), 5))
        finally:
            await feed.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("postcommit_signal", [True, False])
def test_commit_in_one_process_wakes_another(tmp_path, monkeypatch, postcommit_signal):
    store = str(tmp_path / "store")
    db = EmberDB.connect(store)
    author = db.start_session("author", task="task", namespace="shared")
    reader = db.start_session("reader", task="task", namespace="shared")
    scope = working.create_scope(db, author, "author", task="task", namespace="shared")["scope_id"]
    working.grant(db, scope, author, "author", "reader")
    context = multiprocessing.get_context("spawn")
    result = context.Queue()
    worker = context.Process(target=_reader, args=(store, scope, reader, result))
    worker.start()
    try:
        assert "event: ready" in result.get(timeout=10)
        if not postcommit_signal:
            monkeypatch.setattr(realtime_v2, "committed", lambda _db: None)
        item = working.submit(db, scope, author, "author", {
            "client_submission_id": "cross-worker", "share": True,
            "kind": "finding", "claim": "one committed finding",
            "uncertainty": "unverified"})
        frame = result.get(timeout=8)
        assert "event: working_memory_changed" in frame
        assert item["item_id"] in frame
        worker.join(timeout=8)
        assert worker.exitcode == 0
    finally:
        if worker.is_alive():
            worker.terminate()
            worker.join()
