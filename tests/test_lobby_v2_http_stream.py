"""Real HTTP SSE framing, committed event delivery and cursor recovery."""

import json
import socket
import threading
import time
from urllib.request import Request, urlopen

import uvicorn

import embers.api as api
import embers.api.v1 as v1
from embers.db import EmberDB
from embers.lobby import working


def _frame(response):
    lines = []
    while True:
        line = response.readline().decode("utf-8").rstrip("\r\n")
        if not line:
            if lines:
                return lines
            continue
        lines.append(line)


def _post(base, route, data, headers=None):
    request = Request(base + route, data=json.dumps(data).encode(), method="POST",
                      headers={"Content-Type": "application/json", **(headers or {})})
    with urlopen(request, timeout=5) as response:
        return json.load(response)


def test_http_lobby_stream_reconnects_from_committed_cursor(tmp_path):
    api._db = EmberDB.connect(str(tmp_path / "store"))
    api._registry = None
    v1._registry = None
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(api.app, host="127.0.0.1", port=port,
                                           lifespan="off", log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(.05)
        assert server.started
        base = f"http://127.0.0.1:{port}"
        author = _post(base, "/v1/agents/register", {"name": "author"})
        reader = _post(base, "/v1/agents/register", {"name": "reader"})
        ah = {"X-Ember-Agent-Id": author["agent_id"], "X-Ember-Token": author["token"]}
        rh = {"X-Ember-Agent-Id": reader["agent_id"], "X-Ember-Token": reader["token"]}
        a = _post(base, "/v1/sessions", {"task": "task", "namespace": "shared"}, ah)["session_id"]
        b = _post(base, "/v1/sessions", {"task": "task", "namespace": "shared"}, rh)["session_id"]
        scope = _post(base, "/v1/lobby/v2/scopes",
                      {"session_id": a, "task": "task", "namespace": "shared"}, ah)["scope_id"]
        _post(base, f"/v1/lobby/v2/scopes/{scope}/members",
              {"session_id": a, "agent_id": reader["agent_id"]}, ah)
        start = working.events(api._db, scope, b, reader["agent_id"])["cursor"]
        route = f"/v1/lobby/v2/scopes/{scope}/stream?session_id={b}&after={start}"
        with urlopen(Request(base + route, headers=rh), timeout=6) as response:
            assert "event: ready" in _frame(response)
            first = working.submit(api._db, scope, a, author["agent_id"], {
                "client_submission_id": "first", "share": True, "kind": "finding",
                "claim": "first committed finding"})
            frame = _frame(response)
            assert "event: working_memory_changed" in frame
            assert first["item_id"] in "".join(frame)
            cursor = next(line[4:] for line in frame if line.startswith("id: "))
        second = working.submit(api._db, scope, a, author["agent_id"], {
            "client_submission_id": "second", "share": True, "kind": "finding",
            "claim": "second committed finding"})
        with urlopen(Request(base + route, headers={**rh, "Last-Event-ID": cursor}), timeout=6) as response:
            assert "event: ready" in _frame(response)
            replay = _frame(response)
            assert "event: working_memory_changed" in replay
            assert second["item_id"] in "".join(replay)
        grant = _post(base, "/v1/observer-access",
                      {"lobby_scope_id": scope, "lobby_session_id": a}, ah)
        exchanged = Request(base + "/v1/observer/exchange", method="POST",
                            data=json.dumps({"code": grant["code"]}).encode(),
                            headers={"Content-Type": "application/json"})
        with urlopen(exchanged, timeout=5) as response:
            cookie = response.headers["Set-Cookie"].split(";", 1)[0]
        observer_headers = {"Cookie": cookie}
        with urlopen(Request(base + "/v1/observer/lobby", headers=observer_headers), timeout=5) as response:
            observed = json.load(response)
        before = observed["cursor"]
        observer_stream = f"{base}/v1/observer/lobby/stream?after={before}"
        with urlopen(Request(observer_stream, headers=observer_headers), timeout=6) as response:
            assert "event: ready" in _frame(response)
            third = working.submit(api._db, scope, a, author["agent_id"], {
                "client_submission_id": "third", "share": True, "kind": "outcome",
                "claim": "observer projection arrived"})
            arrived = _frame(response)
            assert "event: working_memory_changed" in arrived
            assert third["item_id"] in "".join(arrived)
            working.change(api._db, scope, a, author["agent_id"], third["item_id"],
                           version=third["version"], withdraw=True)
            removed = _frame(response)
            assert "event: working_memory_removed" in removed
            assert third["item_id"] in "".join(removed)
        with urlopen(Request(base + "/v1/observer/lobby", headers=observer_headers), timeout=5) as response:
            projection = json.load(response)
        assert next(i for i in projection["items"] if i["item_id"] == third["item_id"])["status"] == "withdrawn"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
