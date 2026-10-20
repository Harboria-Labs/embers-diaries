"""Public HTTP API security boundary tests."""

import asyncio
import json
import tempfile
import urllib.parse

import pytest


class _Response:
    def __init__(self, status_code: int, body: bytes, headers=None):
        self.status_code = status_code
        self._body = body
        self.headers = dict(headers or [])

    @property
    def text(self) -> str:
        return self._body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self._body or b"null")


class ASGIClient:
    def __init__(self, app):
        self._app = app

    def request(self, method: str, path: str, json_body=None, headers=None):
        raw_headers = [(b"host", b"testserver")]
        body = b"" if json_body is None else json.dumps(json_body).encode()
        if json_body is not None:
            raw_headers.append((b"content-type", b"application/json"))
        raw_headers.append((b"content-length", str(len(body)).encode()))
        for key, value in (headers or {}).items():
            raw_headers.append((key.lower().encode(), str(value).encode()))
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.1"},
            "http_version": "1.1", "method": method.upper(), "scheme": "http",
            "path": path, "raw_path": path.encode(),
            "query_string": urllib.parse.urlencode({}).encode(),
            "root_path": "", "headers": raw_headers,
            "client": ("testclient", 123), "server": ("testserver", 80),
        }
        sent = {"status": 500, "body": b"", "headers": []}
        received = False

        async def receive():
            nonlocal received
            if received:
                return {"type": "http.disconnect"}
            received = True
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            if message["type"] == "http.response.start":
                sent["status"] = message["status"]
                sent["headers"] = message.get("headers", [])
            elif message["type"] == "http.response.body":
                sent["body"] += message.get("body", b"")

        asyncio.run(self._app(scope, receive, send))
        return _Response(sent["status"], sent["body"], sent["headers"])

    def get(self, path, headers=None):
        return self.request("GET", path, headers=headers)

    def post(self, path, json=None, headers=None):
        return self.request("POST", path, json_body=json, headers=headers)


@pytest.fixture
def client(tmp_path):
    import embers.api as api
    from embers import EmberDB
    import embers.api.v1 as v1

    api._db = EmberDB.connect(str(tmp_path / "store"))
    api._protocol = None
    api._registry = None
    v1._registry = None
    v1._protocol = None
    return ASGIClient(api.app)


def _auth(client):
    response = client.post("/v1/agents/register", json={"name": "security-test"})
    assert response.status_code == 200, response.text
    body = response.json()
    return {
        "X-Ember-Agent-Id": body["agent_id"],
        "X-Ember-Token": body["token"],
    }


def test_health_is_public_but_does_not_disclose_store_stats(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.2.0"}


@pytest.mark.parametrize("method,path", [
    ("get", "/namespaces"),
    ("get", "/search"),
    ("post", "/records"),
    ("post", "/memory/remember"),
])
def test_legacy_stateful_routes_require_agent_headers(client, method, path):
    kwargs = {"json": {"content": "blocked"}} if method == "post" else {}
    response = getattr(client, method)(path, **kwargs)
    assert response.status_code == 401
    detail = response.json()["detail"]
    assert "Session" in detail or "Token" in detail


def test_invalid_token_is_rejected(client):
    headers = {"X-Ember-Agent-Id": "agent-nope", "X-Ember-Token": "wrong"}
    response = client.get("/namespaces", headers=headers)
    assert response.status_code == 401
    assert response.json()["detail"] == "unknown agent"


def test_authenticated_legacy_write_is_attributed_to_agent(client):
    headers = _auth(client)
    response = client.post(
        "/records",
        headers=headers,
        json={"namespace": "secure", "data": {"content": "owned write"},
              "written_by": "caller-controlled-forgery"},
    )
    assert response.status_code == 200, response.text
    record_id = response.json()["id"]

    read = client.get(f"/records/{record_id}", headers=headers)
    assert read.status_code == 200, read.text
    record = read.json()
    assert record["agent_id"] == headers["X-Ember-Agent-Id"]
    assert record["written_by"] == headers["X-Ember-Agent-Id"]


def test_session_header_is_enough_after_start_session(client):
    headers = _auth(client)
    started = client.post("/v1/sessions", headers=headers, json={"task": "api-session"})
    assert started.status_code == 200, started.text
    session_id = started.json()["session_id"]

    session_headers = {"X-Ember-Session-Id": session_id}
    written = client.post(
        "/v1/memory/write",
        headers=session_headers,
        json={"content": "session-only write", "room": "task", "memory_type": "episodic"},
    )
    assert written.status_code == 200, written.text
    assert written.json()["agent_id"] == headers["X-Ember-Agent-Id"]

    legacy = client.post(
        "/memory/remember",
        headers=session_headers,
        json={"content": "legacy session write"},
    )
    assert legacy.status_code == 200, legacy.text


def test_default_cors_does_not_grant_wildcard_access(client):
    response = client.request(
        "OPTIONS", "/records", headers={"Origin": "https://evil.example"},
    )
    assert response.headers.get("access-control-allow-origin") is None


def test_maintenance_authorizes_every_namespace_before_running(client, monkeypatch):
    import embers.api as api
    import embers.api.feedback_routes as routes
    from embers import AccessLevel

    owner = _auth(client)
    outsider = _auth(client)
    private = "security_private"
    api._db.create_namespace(private, access_level=AccessLevel.PRIVATE,
                             owner=owner["X-Ember-Agent-Id"])
    calls = []
    monkeypatch.setattr(routes, "run_maintenance_cycle",
                        lambda protocol, namespaces: calls.append(namespaces) or {"ok": True})

    assert client.post("/v1/maintenance", headers=outsider,
                       json={"namespaces": [private]}).status_code == 403
    assert client.post("/v1/maintenance", headers=outsider,
                       json={"namespaces": ["unregistered", private]}).status_code == 403
    assert calls == []

    allowed = client.post("/v1/maintenance", headers=owner,
                          json={"namespaces": [private, "unregistered"]})
    assert allowed.status_code == 200, allowed.text
    assert calls == [[private, "unregistered"]]


def test_maintenance_preserves_public_and_internal_acl(client, monkeypatch):
    import embers.api as api
    import embers.api.feedback_routes as routes
    from embers import AccessLevel

    owner = _auth(client)
    outsider = _auth(client)
    api._db.create_namespace("public_maintenance", access_level=AccessLevel.PUBLIC,
                             owner=owner["X-Ember-Agent-Id"])
    api._db.create_namespace("internal_maintenance", access_level=AccessLevel.INTERNAL,
                             owner=owner["X-Ember-Agent-Id"])
    calls = []
    monkeypatch.setattr(routes, "run_maintenance_cycle",
                        lambda protocol, namespaces: calls.append(namespaces) or {})

    assert client.post("/v1/maintenance", headers=outsider,
                       json={"namespaces": ["public_maintenance"]}).status_code == 200
    assert client.post("/v1/maintenance", headers=outsider,
                       json={"namespaces": ["internal_maintenance"]}).status_code == 403
    assert client.post("/v1/maintenance", headers=owner,
                       json={"namespaces": ["internal_maintenance"]}).status_code == 200
    assert calls == [["public_maintenance"], ["internal_maintenance"]]


@pytest.mark.parametrize("namespaces", ["security_private", "", [], [""], ["  "],
                                         ["ok", None], [42], None])
def test_maintenance_rejects_malformed_namespaces(client, monkeypatch, namespaces):
    import embers.api.feedback_routes as routes

    calls = []
    monkeypatch.setattr(routes, "run_maintenance_cycle",
                        lambda *args: calls.append(args))
    response = client.post("/v1/maintenance", headers=_auth(client),
                           json={"namespaces": namespaces})
    assert response.status_code == 400, response.text
    assert calls == []


def test_token_and_session_must_match_before_observation_binding(client, monkeypatch):
    import embers.api as api
    from embers.integration import write_observation

    first = _auth(client)
    second = _auth(client)
    started = client.post("/v1/sessions", headers=first, json={"task": "owned"})
    assert started.status_code == 200, started.text
    session_id = started.json()["session_id"]
    bindings = []
    monkeypatch.setattr(write_observation, "bind",
                        lambda actor, session=None, request=None: bindings.append((actor, session)))

    wrong = client.get("/namespaces", headers={**second, "X-Ember-Session-Id": session_id})
    assert wrong.status_code == 403
    assert bindings == []
    unknown = client.get("/namespaces", headers={**first, "X-Ember-Session-Id": "missing"})
    assert unknown.status_code == 401
    assert bindings == []

    own = client.get("/namespaces", headers={**first, "X-Ember-Session-Id": session_id})
    assert own.status_code == 200, own.text
    assert bindings == [(first["X-Ember-Agent-Id"], session_id)]
    bindings.clear()
    assert client.get("/namespaces", headers=first).status_code == 200
    assert bindings == [(first["X-Ember-Agent-Id"], None)]
    bindings.clear()
    assert client.get("/namespaces", headers={"X-Ember-Session-Id": session_id}).status_code == 200
    assert bindings == [(first["X-Ember-Agent-Id"], session_id)]

    api._db.end_session(session_id)
    bindings.clear()
    inactive = client.get("/namespaces", headers={**first, "X-Ember-Session-Id": session_id})
    assert inactive.status_code == 401
    assert bindings == []
