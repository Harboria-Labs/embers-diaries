"""
/v1 proposal → durable memory (§4/§5/§12) behavioral tests.

The gap these close: /v1/memory/propose sealed real Evidence into a PROPOSAL
and nothing on the HTTP surface could admit it to durable memory. Evidence went
in and dead-ended. These drive the real ASGI app end to end.
"""

import asyncio
import json
import os
import tempfile
import urllib.parse

import pytest

os.environ.setdefault("EMBER_STORE", tempfile.mkdtemp(prefix="embers_v1_promo_"))


class _Response:
    """Minimal response wrapper matching the bits of httpx we use."""

    def __init__(self, status_code: int, body: bytes):
        self.status_code = status_code
        self._body = body

    @property
    def text(self) -> str:
        return self._body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self._body or b"null")


class ASGIClient:
    """Drive the real FastAPI app over raw ASGI.

    starlette's own TestClient needs httpx, which this venv does not have.
    Calling the ASGI app directly keeps these tests dependency-free while
    still exercising real routing, header parsing, and status codes.
    """

    def __init__(self, app):
        self._app = app

    def _request(self, method: str, path: str, json_body=None,
                 params=None, headers=None) -> _Response:
        raw_headers = [(b"host", b"testserver")]
        body = b""
        if json_body is not None:
            body = json.dumps(json_body).encode()
            raw_headers.append((b"content-type", b"application/json"))
        raw_headers.append((b"content-length", str(len(body)).encode()))
        for key, value in (headers or {}).items():
            raw_headers.append((key.lower().encode(), str(value).encode()))

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.1"},
            "http_version": "1.1",
            "method": method.upper(),
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": urllib.parse.urlencode(params or {}).encode(),
            "root_path": "",
            "headers": raw_headers,
            "client": ("testclient", 123),
            "server": ("testserver", 80),
        }

        sent = {"status": 500, "body": b""}
        request_body_sent = False

        async def receive():
            nonlocal request_body_sent
            if request_body_sent:
                return {"type": "http.disconnect"}
            request_body_sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            if message["type"] == "http.response.start":
                sent["status"] = message["status"]
            elif message["type"] == "http.response.body":
                sent["body"] += message.get("body", b"")

        asyncio.run(self._app(scope, receive, send))
        return _Response(sent["status"], sent["body"])

    def get(self, path, params=None, headers=None):
        return self._request("GET", path, params=params, headers=headers)

    def post(self, path, json=None, params=None, headers=None):
        return self._request("POST", path, json_body=json, params=params,
                             headers=headers)


@pytest.fixture(scope="module")
def client():
    from embers.api import app
    return ASGIClient(app)


@pytest.fixture(scope="module")
def auth(client):
    """Register an agent over HTTP and return its credential headers."""
    r = client.post("/v1/agents/register",
                    json={"name": "luna", "provider": "local", "model": "test"})
    assert r.status_code == 200, r.text
    body = r.json()
    return {"X-Ember-Agent-Id": body["agent_id"],
            "X-Ember-Token": body["token"]}


def _propose(client, auth, content, confidence=0.9, evidence=None,
             namespace="v1memories"):
    r = client.post("/v1/memory/propose", headers=auth, json={
        "discovery": {"content": content},
        "reason": "reproduced",
        "confidence": confidence,
        "namespace": namespace,
        "evidence": evidence if evidence is not None else [
            {"source": "run.log", "source_type": "directly_observed",
             "description": "observed directly"},
        ],
    })
    assert r.status_code == 200, r.text
    return r.json()["proposal_id"]


class TestSubmitOverHTTP:
    def test_evidence_backed_proposal_becomes_durable_memory(self, client, auth):
        pid = _propose(client, auth, "http parser fails above 10000 rows")

        r = client.post("/v1/memory/submit", headers=auth,
                        json={"proposal_id": pid})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["promoted"] is True, body
        memory_id = body["memory_id"]
        assert memory_id

        # the evidence travelled with it and is readable back over HTTP
        ev = client.get(f"/v1/memory/{memory_id}/evidence", headers=auth)
        assert ev.status_code == 200, ev.text
        listed = ev.json()["evidence"]
        assert len(listed) == 1
        assert listed[0]["source"] == "run.log"
        assert listed[0]["content_hash"], "evidence must be sealed"

    def test_ungrounded_proposal_holds(self, client, auth):
        """No evidence → the engine must not silently commit it."""
        pid = _propose(client, auth, "unsupported http claim", evidence=[])
        r = client.post("/v1/memory/submit", headers=auth,
                        json={"proposal_id": pid})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["promoted"] is False
        assert body["memory_id"] is None
        assert body["outcome"] == "hold"

    def test_dry_run_route_reports_without_promoting(self, client, auth):
        pid = _propose(client, auth, "route me but write nothing")
        r = client.post("/v1/memory/promotion_route", headers=auth,
                        json={"proposal_id": pid})
        assert r.status_code == 200, r.text
        assert "outcome" in r.json()

        # still pending — the dry run wrote nothing, so submit can still act
        listed = client.get("/v1/memory/proposals", headers=auth,
                            params={"namespace": "v1memories",
                                    "status": "pending"})
        assert pid in {p["proposal_id"] for p in listed.json()["proposals"]}

    def test_submit_unknown_proposal_is_404(self, client, auth):
        r = client.post("/v1/memory/submit", headers=auth,
                        json={"proposal_id": "does-not-exist"})
        assert r.status_code == 404


class TestExplicitDecisionsOverHTTP:
    def test_promote_records_human_method(self, client, auth):
        # low confidence: the engine would hold, but a caller may still vouch
        pid = _propose(client, auth, "human vouches over http", confidence=0.3)
        r = client.post("/v1/memory/promote", headers=auth,
                        json={"proposal_id": pid})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["method"] == "human"
        assert body["memory_id"]

    def test_reject_keeps_proposal_distinguishable(self, client, auth):
        pid = _propose(client, auth, "rejected over http", confidence=0.3)
        r = client.post("/v1/memory/reject", headers=auth,
                        json={"proposal_id": pid, "reason": "not enough"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "rejected"

        listed = client.get("/v1/memory/proposals", headers=auth,
                            params={"namespace": "v1memories",
                                    "status": "rejected"})
        assert pid in {p["proposal_id"] for p in listed.json()["proposals"]}

    def test_promote_twice_conflicts(self, client, auth):
        pid = _propose(client, auth, "promote once only", confidence=0.3)
        first = client.post("/v1/memory/promote", headers=auth,
                            json={"proposal_id": pid})
        assert first.status_code == 200
        second = client.post("/v1/memory/promote", headers=auth,
                             json={"proposal_id": pid})
        assert second.status_code == 409, second.text


class TestAttachEvidenceOverHTTP:
    def test_another_agent_grounds_an_existing_memory(self, client, auth):
        pid = _propose(client, auth, "retry storm at 3am over http")
        memory_id = client.post("/v1/memory/submit", headers=auth,
                                json={"proposal_id": pid}).json()["memory_id"]

        r = client.post(f"/v1/memory/{memory_id}/evidence", headers=auth, json={
            "source": "second.log", "source_type": "directly_observed",
            "description": "independent confirmation",
        })
        assert r.status_code == 200, r.text
        assert r.json()["evidence_id"]

        listed = client.get(f"/v1/memory/{memory_id}/evidence",
                            headers=auth).json()["evidence"]
        assert {e["source"] for e in listed} == {"run.log", "second.log"}

    def test_attach_to_unknown_memory_is_404(self, client, auth):
        r = client.post("/v1/memory/nope/evidence", headers=auth,
                        json={"source": "x"})
        assert r.status_code == 404


class TestPromotionRoutesRequireAuth:
    """Every new route is behind agent authentication, like the rest of /v1."""

    def test_unauthenticated_calls_are_401(self, client):
        calls = [
            ("post", "/v1/memory/submit", {"json": {"proposal_id": "p"}}),
            ("post", "/v1/memory/promote", {"json": {"proposal_id": "p"}}),
            ("post", "/v1/memory/reject", {"json": {"proposal_id": "p"}}),
            ("post", "/v1/memory/promotion_route", {"json": {"proposal_id": "p"}}),
            ("get", "/v1/memory/proposals", {"params": {"namespace": "n"}}),
            ("post", "/v1/memory/m/evidence", {"json": {"source": "s"}}),
            ("get", "/v1/memory/m/evidence", {}),
        ]
        for method, path, kwargs in calls:
            resp = getattr(client, method)(path, **kwargs)
            assert resp.status_code == 401, f"{method} {path} → {resp.status_code}"
