"""Bulk memory write: one MCP tool, one REST route, same per-item path."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import embers.api as api
import embers.api.v1 as v1
import embers.mcp  # installs session auth and room/kind wiring
import embers.storage.store as storemod
from embers import AccessLevel, EmberDB
from embers.identity.registry import AgentRegistry
from embers.mcp.server import EmberMCP
from embers.storage.format import encode


@pytest.fixture
def batch(tmp_path, monkeypatch):
    db = EmberDB.connect(str(tmp_path / "store"))
    monkeypatch.setattr(api, "_db", db)
    monkeypatch.setattr(api, "_protocol", None)
    monkeypatch.setattr(api, "_registry", None)
    monkeypatch.setattr(v1, "_protocol", None)
    monkeypatch.setattr(v1, "_registry", None)
    mcp = EmberMCP(db=db)
    client = TestClient(api.app)
    return client, mcp, db


def _body(result):
    assert result.get("isError") is not True, result
    return json.loads(result["content"][0]["text"])


def _auth(mcp):
    ident = _body(mcp.call_tool("ember_register", {"name": "batch-agent"}))
    return {"agent_id": ident["agent_id"], "token": ident["token"]}


def _headers(auth):
    return {"X-Ember-Agent-Id": auth["agent_id"], "X-Ember-Token": auth["token"]}


def _memory_ids(db):
    return {
        record_id for record_id in db._store.all_ids()
        if (record := db.get(record_id)) is not None
        and record.namespace == "memories"
        and record.record_type.value in ("document", "node")
    }


def test_happy_path_indexes_conflicts_and_one_wal_batch(batch, monkeypatch, tmp_path):
    client, mcp, db = batch
    auth = _auth(mcp)
    calls = {"batch": 0, "single": 0, "ids": []}
    real_batch = storemod._write_record_batch
    real_single = storemod._write_record_transaction

    def tracked_batch(*args, **kwargs):
        calls["batch"] += 1
        calls["ids"].append(list(args[4]))
        return real_batch(*args, **kwargs)

    def tracked_single(*args, **kwargs):
        calls["single"] += 1
        return real_single(*args, **kwargs)

    monkeypatch.setattr(storemod, "_write_record_batch", tracked_batch)
    monkeypatch.setattr(storemod, "_write_record_transaction", tracked_single)
    records = [
        {"content": {"content": "alpha claim", "subject": "Widget", "claim": "on"}},
        {"content": {"content": "beta claim", "subject": "Widget", "claim": "off"}},
        {"content": "gamma note", "room": "project", "memory_type": "episodic"},
    ]
    written = _body(mcp.call_tool("ember_write_batch", {"records": records, **auth}))
    assert written["atomic"] is False and written["count"] == 3
    assert all(item["ok"] for item in written["results"])
    assert written["results"][0]["possible_conflicts"] == []
    assert written["results"][1]["possible_conflicts"][0]["field"] == "claim"
    assert calls["batch"] == 1 and calls["single"] == 0 and len(calls["ids"][0]) == 3
    for item, record in zip(written["results"], records):
        stored = db.get(item["id"])
        assert stored.agent_id == auth["agent_id"]
        if isinstance(record["content"], dict):
            assert stored.data["subject"] == "Widget"
        else:
            assert stored.data["room"] == "project"
            assert stored.data["memory_type"] == "episodic"
    assert any(found.id == written["results"][2]["id"] for found, _score in db.search("gamma"))
    reopened = EmberDB.connect(str(tmp_path / "store"))
    assert reopened.get(written["results"][0]["id"]).data["claim"] == "on"

    response = client.post(
        "/v1/memory/write-batch", headers=_headers(auth),
        json={"records": [{"content": "rest gamma"}]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["results"][0]["ok"] and "possible_conflicts" not in body["results"][0]
    assert db.get(body["results"][0]["id"]).data["content"] == "rest gamma"
    _body(mcp.call_tool("ember_write", {"content": "still single", **auth}))
    assert calls["single"] == 1 and calls["batch"] == 2


def test_partial_failure_keeps_neighbors(batch):
    client, mcp, db = batch
    auth = _auth(mcp)
    before = _memory_ids(db)
    written = _body(mcp.call_tool("ember_write_batch", {
        "records": [
            {"content": "kept-a"},
            {"namespace": "memories"},
            {"content": "kept-b"},
        ],
        **auth,
    }))
    assert [item["ok"] for item in written["results"]] == [True, False, True]
    assert written["results"][1]["error"]["status"] == 400
    kept = _memory_ids(db) - before
    assert kept == {written["results"][0]["id"], written["results"][2]["id"]}
    response = client.post("/v1/memory/write-batch", headers=_headers(auth), json={
        "records": [{"content": "rest-kept"}, {"primary_context": "  "}, {"content": "rest-also"}],
    })
    assert response.status_code == 200
    flags = [item["ok"] for item in response.json()["results"]]
    assert flags == [True, False, True]


def test_atomic_rollback_writes_nothing(batch):
    _client, mcp, db = batch
    auth = _auth(mcp)
    before = set(db._store.all_ids())
    written = _body(mcp.call_tool("ember_write_batch", {
        "atomic": True,
        "records": [
            {"content": "should-not-land"},
            {"namespace": "memories"},
            {"content": "also-not"},
        ],
        **auth,
    }))
    assert written["atomic"] is True
    assert [item["ok"] for item in written["results"]] == [False, False, False]
    assert written["results"][1]["error"]["type"] == "KeyError"
    assert written["results"][0]["error"]["type"] == "AtomicRollback"
    assert set(db._store.all_ids()) == before


def test_auth_failure_writes_nothing(batch):
    client, mcp, db = batch
    before = set(db._store.all_ids())
    denied = mcp.call_tool("ember_write_batch", {"records": [{"content": "nope"}]})
    assert denied["isError"] is True
    response = client.post("/v1/memory/write-batch", json={"records": [{"content": "nope"}]})
    assert response.status_code == 401
    assert set(db._store.all_ids()) == before


def test_over_limit_batch_writes_nothing(batch, monkeypatch):
    client, mcp, db = batch
    auth = _auth(mcp)
    before = set(db._store.all_ids())
    mcp.config = replace(mcp.config, storage=replace(mcp.config.storage, max_write_batch=2))
    denied = mcp.call_tool("ember_write_batch", {
        "records": [{"content": "a"}, {"content": "b"}, {"content": "c"}],
        **auth,
    })
    assert denied["isError"] is True
    assert "max_write_batch" in denied["content"][0]["text"]
    monkeypatch.setattr(
        api, "_config",
        replace(api._get_config(), storage=replace(api._get_config().storage, max_write_batch=2)),
    )
    response = client.post("/v1/memory/write-batch", headers=_headers(auth), json={
        "records": [{"content": "a"}, {"content": "b"}, {"content": "c"}],
    })
    assert response.status_code == 400
    assert set(db._store.all_ids()) == before


def test_namespace_denial_is_per_item_and_atomic_rolls_back(batch):
    client, mcp, db = batch
    auth = _auth(mcp)
    owner, _token = AgentRegistry(db).register("owner")
    db.create_namespace("sealed", access_level=AccessLevel.PRIVATE, owner=owner.agent_id)
    before = set(db._store.all_ids())
    written = _body(mcp.call_tool("ember_write_batch", {
        "records": [
            {"content": "public-ok"},
            {"content": "secret", "namespace": "sealed"},
        ],
        **auth,
    }))
    assert written["results"][0]["ok"] is True
    assert written["results"][1]["ok"] is False
    assert written["results"][1]["error"]["status"] == 403
    assert db.get(written["results"][0]["id"]) is not None
    atomic = client.post("/v1/memory/write-batch", headers=_headers(auth), json={
        "atomic": True,
        "records": [
            {"content": "should-vanish"},
            {"content": "secret", "namespace": "sealed"},
        ],
    })
    assert atomic.status_code == 200
    body = atomic.json()
    assert body["results"][1]["error"]["status"] == 403
    assert body["results"][0]["error"]["type"] == "AtomicRollback"
    assert "should-vanish" not in json.dumps([
        db.get(record_id).to_dict() for record_id in set(db._store.all_ids()) - before
        if db.get(record_id) is not None
    ])


def test_record_byte_limit_partial_and_atomic(batch):
    _client, mcp, db = batch
    auth = _auth(mcp)
    small = mcp.protocol.build_memory(
        "hi", agent_id=auth["agent_id"], written_by=auth["agent_id"])
    large = mcp.protocol.build_memory(
        "x" * 30000, agent_id=auth["agent_id"], written_by=auth["agent_id"])
    small.seal()
    large.seal()
    small_size = len(encode(small.to_dict()))
    large_size = len(encode(large.to_dict()))
    db._store.max_record_bytes = small_size + (large_size - small_size) // 2
    before = _memory_ids(db)
    written = _body(mcp.call_tool("ember_write_batch", {
        "records": [{"content": "hi"}, {"content": "x" * 30000}, {"content": "yo"}],
        **auth,
    }))
    assert [item["ok"] for item in written["results"]] == [True, False, True]
    assert written["results"][1]["error"]["status"] == 413
    assert _memory_ids(db) - before == {written["results"][0]["id"], written["results"][2]["id"]}
    atomic_before = set(db._store.all_ids())
    atomic = _body(mcp.call_tool("ember_write_batch", {
        "atomic": True,
        "records": [{"content": "hi"}, {"content": "x" * 30000}],
        **auth,
    }))
    assert all(not item["ok"] for item in atomic["results"])
    assert set(db._store.all_ids()) == atomic_before


def test_total_byte_refusal_keeps_best_effort_prefix(batch, monkeypatch):
    _client, mcp, db = batch
    auth = _auth(mcp)
    real_single = storemod._write_record_transaction
    state = {"single": 0}

    def refuse_batch(*_args, **_kwargs):
        raise OSError("storage.max_total_bytes exceeded: limit=10, attempted=99")

    def limited_single(*args, **kwargs):
        state["single"] += 1
        if state["single"] > 1:
            raise OSError("storage.max_total_bytes exceeded: limit=10, attempted=99")
        return real_single(*args, **kwargs)

    monkeypatch.setattr(storemod, "_write_record_batch", refuse_batch)
    monkeypatch.setattr(storemod, "_write_record_transaction", limited_single)
    written = _body(mcp.call_tool("ember_write_batch", {
        "records": [{"content": "fit"}, {"content": "overflow"}, {"content": "later"}],
        **auth,
    }))
    assert [item["ok"] for item in written["results"]] == [True, False, False]
    assert db.get(written["results"][0]["id"]).data["content"] == "fit"
    assert written["results"][1]["error"]["status"] == 507
    atomic_before = set(db._store.all_ids())
    state["single"] = 0
    atomic = _body(mcp.call_tool("ember_write_batch", {
        "atomic": True,
        "records": [{"content": "none"}, {"content": "neither"}],
        **auth,
    }))
    assert state["single"] == 0
    assert all(item["ok"] is False for item in atomic["results"])
    assert set(db._store.all_ids()) == atomic_before


def test_session_only_auth_logs_the_write(batch):
    client, mcp, db = batch
    auth = _auth(mcp)
    started = _body(mcp.call_tool("ember_start_session", {**auth, "task": "batch"}))
    written = _body(mcp.call_tool("ember_write_batch", {
        "session_id": started["session_id"],
        "records": [{"content": "via-session"}],
    }))
    assert written["results"][0]["ok"] is True
    assert written["results"][0]["id"] in db.get_session(started["session_id"]).memory_writes
    response = client.post(
        "/v1/memory/write-batch",
        headers={"X-Ember-Session-Id": started["session_id"]},
        json={"records": [{"content": "rest-session", "session_id": started["session_id"]}]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["results"][0]["id"] in db.get_session(started["session_id"]).memory_writes


def test_browser_fetches_send_ngrok_header_and_sse_uses_fetch():
    root = Path("embers/api")
    for name in (
        "status_monitor.js", "observer_transport.js", "lobby_observer.js",
        "observer.html", "visualizer.html", "observe.html", "research_settings.html", "control.js",
    ):
        text = (root / name).read_text(encoding="utf-8")
        assert "ngrok-skip-browser-warning" in text, name
    transport = (root / "observer_transport.js").read_text(encoding="utf-8")
    assert "new EmberFetchEventSource(this.scope.streamUrl())" in transport
    assert "new EventSource(" not in transport
    assert "active.abort(),10000" not in transport
    lobby = (root / "lobby_observer.js").read_text(encoding="utf-8")
    assert "window.EmberFetchEventSource||EventSource" in lobby
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required to parse the fetch SSE client")
    subprocess.run(
        [node, "--test", "tests/js/fetch_event_source.test.cjs"],
        cwd=Path(__file__).resolve().parents[1], check=True,
    )
