"""Opt-in Codex Stop adapter and MCP parity using isolated stores."""

import asyncio
import json

import pytest

from embers.db import EmberDB
from embers.lobby import working
from embers.lobby.codex_stop import process_stop
from embers.lobby.realtime_v2 import stream
from embers.mcp.server import EmberMCP


def test_codex_stop_selected_finding_artifact_push_and_peer_recall(tmp_path):
    db = EmberDB.connect(str(tmp_path / "store"))
    author_sid = db.start_session("codex-agent", task="task", namespace="shared")
    reader_sid = db.start_session("reader-agent", task="task", namespace="shared")
    scope = working.create_scope(db, author_sid, "codex-agent", task="task",
                                 namespace="shared")["scope_id"]
    working.grant(db, scope, author_sid, "codex-agent", "reader-agent")
    outbox = tmp_path / ".ember-lobby-outbox"
    outbox.mkdir()
    (outbox / "result.txt").write_text("3 passed in isolated pytest\n", encoding="utf-8")
    marker = {"share": True, "kind": "finding", "claim": "three parser tests passed",
              "uncertainty": "unverified", "artifact_file": "result.txt", "redacted": True}
    event = {"hook_event_name": "Stop", "turn_id": "turn-1", "cwd": str(tmp_path),
             "last_assistant_message": "Done.\n<ember-lobby-v2>" + json.dumps(marker) + "</ember-lobby-v2>"}
    settings = {"url": "http://127.0.0.1:9200", "agent_id": "codex-agent",
                "token": "isolated", "session_id": author_sid, "scope_id": scope}

    def post(url, _headers, body):
        if url.endswith("/artifacts"):
            return working.upload_artifact(db, scope, author_sid, "codex-agent",
                content=body["content"], safe_to_share=body["safe_to_share"],
                redacted=body["redacted"], client_artifact_id=body["client_artifact_id"])
        if url.endswith("/items"):
            return working.submit(db, scope, author_sid, "codex-agent", body)
        raise AssertionError(url)

    async def run():
        async def connected():
            return False
        cursor = working.events(db, scope, reader_sid, "reader-agent")["cursor"]
        gen = stream(db, scope, reader_sid, "reader-agent", cursor, connected)
        await anext(gen)  # ready
        pending = asyncio.create_task(anext(gen))
        await asyncio.sleep(0)
        receipt = process_stop(event, settings, post)
        signal = await asyncio.wait_for(pending, 1)
        assert "event: working_memory_changed" in signal
        assert receipt["item_id"] in signal
        items = working.query(db, scope, reader_sid, "reader-agent", context="parser")
        assert items[0]["claim"] == marker["claim"]
        evidence = working.get(db, scope, reader_sid, "reader-agent",
                               receipt["item_id"], evidence=True)["evidence_refs"][0]
        artifact = working.get_artifact(db, scope, reader_sid, "reader-agent",
                                        evidence["reference"].split(":", 1)[1])
        assert artifact["content"] == "3 passed in isolated pytest\n"
        assert process_stop(event, settings, post)["item_id"] == receipt["item_id"]
        await gen.aclose()
    asyncio.run(run())


def test_codex_stop_withholding_and_no_marker(tmp_path):
    settings = {"url": "http://127.0.0.1:9200", "agent_id": "a", "token": "x",
                "session_id": "s", "scope_id": "scope"}
    def should_not_post(*_):
        raise AssertionError("publication not authorized")
    base = {"hook_event_name": "Stop", "turn_id": "turn", "cwd": str(tmp_path)}
    assert process_stop({**base, "last_assistant_message": "ordinary completion"}, settings,
                        should_not_post) is None
    marker = {"share": True, "withhold": True, "kind": "finding", "claim": "private"}
    assert process_stop({**base, "last_assistant_message": "<ember-lobby-v2>" +
                        json.dumps(marker) + "</ember-lobby-v2>"}, settings,
                        should_not_post) is None


def test_mcp_v2_manual_submission_and_scope_gate(tmp_path):
    db = EmberDB.connect(str(tmp_path / "mcp"))
    mcp = EmberMCP(db=db)
    def body(result):
        assert not result["isError"], result["content"][0]["text"]
        return json.loads(result["content"][0]["text"])
    reg = body(mcp.call_tool("ember_register", {"name": "mcp-v2"}))
    sid = body(mcp.call_tool("ember_start_session", {"agent_id": reg["agent_id"],
          "token": reg["token"], "task": "task", "namespace": "shared"}))["session_id"]
    scope = body(mcp.call_tool("ember_lobby_v2", {"action": "create_scope",
        "session_id": sid, "task": "task", "namespace": "shared"}))["scope_id"]
    posted = body(mcp.call_tool("ember_lobby_v2", {"action": "submit", "session_id": sid,
        "scope_id": scope, "client_submission_id": "manual-1", "share": True,
        "kind": "question", "claim": "does parser handle zero input?"}))
    found = body(mcp.call_tool("ember_lobby_v2", {"action": "query", "session_id": sid,
        "scope_id": scope, "context": "parser"}))
    assert found["items"][0]["item_id"] == posted["item_id"]


def test_rest_artifact_requires_explicit_share_and_separate_read_grant(tmp_path):
    import embers.api as api
    import embers.api.v1 as v1
    from tests.test_v1_promotion import ASGIClient

    api._db = EmberDB.connect(str(tmp_path / "api"))
    v1._registry = None
    client = ASGIClient(api.app)
    def identity(name):
        row = client.post("/v1/agents/register", json={"name": name}).json()
        headers = {"X-Ember-Agent-Id": row["agent_id"], "X-Ember-Token": row["token"]}
        sid = client.post("/v1/sessions", headers=headers,
                          json={"task": "shared", "namespace": "work"}).json()["session_id"]
        return row["agent_id"], headers, sid
    owner, oh, sid = identity("artifact-owner")
    reader, rh, rid = identity("artifact-reader")
    scope = client.post("/v1/lobby/v2/scopes", headers=oh,
        json={"session_id": sid, "task": "shared", "namespace": "work"}).json()["scope_id"]
    route = f"/v1/lobby/v2/scopes/{scope}/artifacts"
    denied = client.post(route, headers=oh, json={"session_id": sid, "content": "3 passed",
                                                    "safe_to_share": True})
    assert denied.status_code == 400
    uploaded = client.post(route, headers=oh, json={"session_id": sid, "content": "3 passed",
                                                  "safe_to_share": True, "redacted": True})
    assert uploaded.status_code == 200, uploaded.text
    artifact_id = uploaded.json()["reference"].split(":", 1)[1]
    uri = route + "/" + artifact_id
    assert client.get(uri, headers=rh, params={"session_id": rid}).status_code == 403
    working.grant(api._db, scope, sid, owner, reader)
    assert client.get(uri, headers=rh, params={"session_id": rid}).json()["content"] == "3 passed"


def test_artifact_capacity_and_expiry_signal(tmp_path, monkeypatch):
    db = EmberDB.connect(str(tmp_path / "bounded"))
    sid = db.start_session("owner", task="task", namespace="shared")
    scope = working.create_scope(db, sid, "owner", task="task", namespace="shared")["scope_id"]
    monkeypatch.setattr(working, "MAX_ARTIFACTS_PER_SCOPE", 1)
    working.upload_artifact(db, scope, sid, "owner", content="safe result",
                            safe_to_share=True, redacted=True)
    with pytest.raises(working.WorkingError):
        working.upload_artifact(db, scope, sid, "owner", content="another result",
                                safe_to_share=True, redacted=True)
    item = working.submit(db, scope, sid, "owner", {
        "client_submission_id": "expiry", "share": True, "kind": "finding",
        "claim": "temporary result"})
    cursor = working.events(db, scope, sid, "owner")["cursor"]
    now = working.time.time()
    monkeypatch.setattr(working.time, "time", lambda: now + working.TTL_SECONDS + 1)
    assert working.expire(db, scope, sid, "owner") == 1
    page = working.events(db, scope, sid, "owner", after=cursor)
    assert page["events"][0]["item_id"] == item["item_id"]
    assert page["events"][0]["change"] == "removed"
