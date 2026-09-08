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
    assert "X-Ember-Agent-Id" in response.json()["detail"]


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


def test_default_cors_does_not_grant_wildcard_access(client):
    response = client.request(
        "OPTIONS", "/records", headers={"Origin": "https://evil.example"},
    )
    assert response.headers.get("access-control-allow-origin") is None

