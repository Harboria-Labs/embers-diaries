"""Focused provisional Lobby foundation tests using disposable stores."""

import pytest

from embers.db import EmberDB
from embers.lobby import working


@pytest.fixture
def setup(tmp_path):
    db = EmberDB.connect(str(tmp_path / "isolated"))
    a = db.start_session("author", task="shared", namespace="work")
    b = db.start_session("reader", task="shared", namespace="work")
    outsider = db.start_session("outsider", task="shared", namespace="work")
    scope = working.create_scope(db, a, "author", task="shared", namespace="work")
    return db, a, b, outsider, scope["scope_id"]


def _item(key="one", claim="parser failed", evidence=None):
    return {"client_submission_id": key, "share": True, "kind": "failure",
            "claim": claim, "uncertainty": "unverified",
            "evidence_refs": evidence or [], "evidence_status": "unavailable"}


def test_membership_restart_and_context(setup):
    db, a, b, outsider, scope = setup
    with pytest.raises(PermissionError):
        working.query(db, scope, outsider, "outsider")
    working.grant(db, scope, a, "author", "reader")
    item = working.submit(db, scope, a, "author", _item())
    assert working.submit(db, scope, a, "author", _item())["item_id"] == item["item_id"]
    with pytest.raises(working.WorkingError):
        working.submit(db, scope, a, "author", _item(claim="changed"))
    assert working.query(db, scope, b, "reader", context="parser")[0]["item_id"] == item["item_id"]
    reopened = EmberDB.connect(str(db._path))
    assert working.get(reopened, scope, b, "reader", item["item_id"])["claim"] == "parser failed"
    working.revoke(db, scope, a, "author", "reader")
    with pytest.raises(PermissionError):
        working.get(reopened, scope, b, "reader", item["item_id"])


def test_author_correction_withdrawal_and_private_evidence(setup):
    db, a, b, _, scope = setup
    working.grant(db, scope, a, "author", "reader")
    ev = [{"reference": "https://example.org/result/1", "observed_at": "2026-10-18T00:00:00Z",
           "execution_context": "pytest isolated"}]
    item = working.submit(db, scope, a, "author", _item(evidence=ev))
    assert "reference" not in working.get(db, scope, b, "reader", item["item_id"])["evidence_refs"][0]
    assert working.get(db, scope, b, "reader", item["item_id"], evidence=True)["evidence_refs"][0]["reference"] == ev[0]["reference"]
    assert working.get(db, scope, b, "reader", item["item_id"], evidence=True)["evidence_refs"][0]["session_id"].startswith("session-sha256:")
    with pytest.raises(PermissionError):
        working.change(db, scope, b, "reader", item["item_id"], version=1, correction="wrong")
    corrected = working.change(db, scope, a, "author", item["item_id"], version=1, correction="parser timed out")
    assert corrected["version"] == 2 and corrected["uncertainty"] == "unverified"
    working.change(db, scope, a, "author", item["item_id"], version=2, withdraw=True)
    assert working.query(db, scope, b, "reader") == []
    with pytest.raises(working.WorkingError):
        working.submit(db, scope, a, "author", _item("secret", evidence=[{"reference": "https://example.org/?token=secret", "observed_at": "now"}]))


def test_structural_share_and_session_scope(setup):
    db, a, b, _, scope = setup
    with pytest.raises(working.WorkingError):
        working.submit(db, scope, a, "author", {**_item(), "share": False})
    with pytest.raises(PermissionError):
        working.submit(db, scope, b, "author", _item())
    other_task = db.start_session("author", task="private", namespace="work")
    with pytest.raises(PermissionError):
        working.submit(db, scope, other_task, "author", _item())


def test_close_expires_working_items(setup):
    db, a, b, _, scope = setup
    working.grant(db, scope, a, "author", "reader")
    working.submit(db, scope, a, "author", _item())
    assert working.close_scope(db, scope, a, "author")["expired"] == 1
    with pytest.raises(working.WorkingError):
        working.query(db, scope, b, "reader")


def test_rest_scope_submission_and_evidence_authorization(tmp_path):
    import embers.api as api
    import embers.api.v1 as v1
    from tests.test_v1_promotion import ASGIClient

    api._db = EmberDB.connect(str(tmp_path / "rest"))
    v1._registry = None
    client = ASGIClient(api.app)

    def agent(name):
        row = client.post("/v1/agents/register", json={"name": name}).json()
        headers = {"X-Ember-Agent-Id": row["agent_id"], "X-Ember-Token": row["token"]}
        sid = client.post("/v1/sessions", headers=headers,
                          json={"task": "rest task", "namespace": "shared"}).json()["session_id"]
        return row["agent_id"], headers, sid

    author, ah, a = agent("v2-author")
    reader, rh, b = agent("v2-reader")
    scope_response = client.post("/v1/lobby/v2/scopes", headers=ah,
                                 json={"session_id": a, "task": "rest task", "namespace": "shared"})
    assert scope_response.status_code == 200, scope_response.text
    scope = scope_response.json()["scope_id"]
    denied = client.get(f"/v1/lobby/v2/scopes/{scope}/items", headers=rh, params={"session_id": b})
    assert denied.status_code == 403
    granted = client.post(f"/v1/lobby/v2/scopes/{scope}/members", headers=ah,
                          json={"session_id": a, "agent_id": reader})
    assert granted.status_code == 200, granted.text
    body = {**_item(evidence=[{"reference": "https://example.org/test", "observed_at": "2026-10-18T00:00:00Z"}]),
            "session_id": a}
    posted = client.post(f"/v1/lobby/v2/scopes/{scope}/items", headers=ah, json=body)
    assert posted.status_code == 200, posted.text
    item_id = posted.json()["item_id"]
    evidence_url = f"/v1/lobby/v2/scopes/{scope}/items/{item_id}/evidence"
    assert client.get(evidence_url, headers=rh, params={"session_id": b}).status_code == 200
    working.revoke(api._db, scope, a, author, reader)
    assert client.get(evidence_url, headers=rh, params={"session_id": b}).status_code == 403
