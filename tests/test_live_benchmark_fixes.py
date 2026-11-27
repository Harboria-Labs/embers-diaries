"""Regressions from the 2026-11-13 live benchmark.

Exact-token recall, REST content hashes, lobby/feedback 400s, write-path
scans, and an observation cursor that does not jump to the FUR revision.
"""

from embers import EmberDB
from embers.integration.batch_write import run_write_batch
from embers.integration.memory_protocol import MemoryProtocol
from embers.integration.observation_journal import read_stats
from embers.integration.server_memory import prepare_memory_services
from embers.integration.usefulness_service import snapshot
from embers.lobby import working
from tests.test_v1_promotion import ASGIClient


def test_rare_literal_token_outranks_vector_neighbors(tmp_path):
    db = EmberDB.connect(str(tmp_path / "store"))
    protocol = MemoryProtocol(db, default_namespace="lab")
    # Low confidence is the same merge failure the live run hit with fresh
    # rows: min-max stretches vector neighbors up to 1, then multiplies by
    # confidence, so a perfect BM25 hit falls out of the top 10.
    true = protocol.remember(
        "Mira Ell Chen discovered the Kell-ice isotope in 1998. "
        "Lab notebook TOK000010D7B.",
        namespace="lab", confidence=0.15)
    basil = protocol.remember(
        "basil in the greenhouse prefers morning water and shade",
        namespace="lab")
    for i in range(15):
        protocol.remember(
            f"North Compass Lab-{i} was founded in {1970 + i}. "
            f"Charter TOK9{i:04d}ZZ.",
            namespace="lab")

    query = "What does lab notebook TOK000010D7B refer to?"
    assert db.search(query, namespace="lab", top_k=1)[0][0].id == true
    recalled = protocol.recall(query, namespace="lab", top_k=10, format="structured")
    assert recalled[0]["id"] == true
    assert recalled[0]["content_hash"]

    paraphrase = protocol.recall(
        "greenhouse basil prefers morning water and shade",
        namespace="lab", top_k=5, format="structured")
    assert paraphrase[0]["id"] == basil


def test_rest_read_returns_content_hash_for_supersede(tmp_path):
    import embers.api as api
    import embers.api.v1 as v1

    api._db = EmberDB.connect(str(tmp_path / "store"))
    api._protocol = None
    v1._protocol = None
    v1._registry = None
    client = ASGIClient(api.app)
    registered = client.post(
        "/v1/agents/register",
        json={"name": "rest-cas", "provider": "local", "model": "test"},
    ).json()
    auth = {
        "X-Ember-Agent-Id": registered["agent_id"],
        "X-Ember-Token": registered["token"],
    }
    record_id = client.post(
        "/v1/memory/write", headers=auth,
        json={"content": "alpha comet observation", "namespace": "rest-parity"},
    ).json()["id"]
    stored = api._db.get(record_id)

    read = client.get(f"/v1/memory/read/{record_id}", headers=auth)
    assert read.status_code == 200, read.text
    assert read.json()["content_hash"] == stored.content_hash

    searched = client.get(
        "/v1/memory/search", headers=auth, params={"q": "alpha comet"})
    assert searched.status_code == 200, searched.text
    assert searched.json()["results"][0]["record"]["content_hash"] == stored.content_hash

    recalled = client.post(
        "/v1/memory/recall", headers=auth,
        json={"query": "alpha comet observation", "namespace": "rest-parity", "top_k": 5},
    )
    assert recalled.status_code == 200, recalled.text
    memories = recalled.json()["memories"]
    assert memories[0]["id"] == record_id
    assert memories[0]["content_hash"] == stored.content_hash

    missing = client._request(
        "PUT", f"/v1/memory/{record_id}", headers=auth,
        json_body={"data": {"content": "replaced comet"}},
    )
    assert missing.status_code == 400
    assert "expected_hash required" in missing.text
    assert "content_hash" in missing.text

    data = dict(read.json()["data"])
    data["content"] = "replaced comet"
    updated = client._request(
        "PUT", f"/v1/memory/{record_id}", headers=auth,
        json_body={"expected_hash": read.json()["content_hash"], "data": data},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["superseded_record_id"] == record_id
    assert updated.json()["content_hash"]


def test_feedback_and_lobby_400s_name_the_bad_fields(tmp_path):
    import embers.api as api
    import embers.api.v1 as v1

    api._db = EmberDB.connect(str(tmp_path / "store"))
    api._protocol = None
    v1._protocol = None
    v1._registry = None
    client = ASGIClient(api.app)
    registered = client.post(
        "/v1/agents/register",
        json={"name": "rest-feedback", "provider": "local", "model": "test"},
    ).json()
    auth = {
        "X-Ember-Agent-Id": registered["agent_id"],
        "X-Ember-Token": registered["token"],
    }
    record_id = client.post(
        "/v1/memory/write", headers=auth,
        json={"content": "Hall A clock", "namespace": "bench"},
    ).json()["id"]

    # The live harness posted channel/signal on the v1 shape.
    feedback = client.post(
        f"/v1/memory/{record_id}/feedback", headers=auth,
        json={"outcome": "useful", "signal": 1, "channel": "explicit"},
    )
    assert feedback.status_code == 400
    detail = feedback.json()["detail"]
    assert "schema_version=2" in detail
    assert "channel" in detail and "signal" in detail
    assert "relevance" in detail and "correctness" in detail
    assert "explicit" in detail

    plain = client.post(
        f"/v1/memory/{record_id}/feedback", headers=auth,
        json={"outcome": "useful"},
    )
    assert plain.status_code == 200, plain.text

    session_id = client.post(
        "/v1/sessions", headers=auth,
        json={"task": "bench task", "namespace": "bench"},
    ).json()["session_id"]
    scope_id = client.post(
        "/v1/lobby/v2/scopes", headers=auth,
        json={"session_id": session_id, "task": "bench task", "namespace": "bench"},
    ).json()["scope_id"]
    # The live harness posted a note with title/body/type.
    rejected = client.post(
        f"/v1/lobby/v2/scopes/{scope_id}/items", headers=auth,
        json={
            "session_id": session_id, "title": "bench item",
            "body": "a benchmark lobby item", "type": "note",
        },
    )
    assert rejected.status_code == 400
    message = rejected.json()["detail"]
    for field in ("client_submission_id", "share", "kind", "claim", "title", "body", "type"):
        assert field in message
    assert "finding|failure|question|outcome" in message

    accepted = client.post(
        f"/v1/lobby/v2/scopes/{scope_id}/items", headers=auth,
        json={
            "session_id": session_id, "client_submission_id": "bench-1",
            "share": True, "kind": "finding", "claim": "a benchmark lobby item",
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert working.get(
        api._db, scope_id, session_id, registered["agent_id"],
        accepted.json()["item_id"])["claim"] == "a benchmark lobby item"


def test_batch_writes_do_not_reread_the_journal(tmp_path):
    store = tmp_path / "store"
    db = EmberDB.connect(str(store))
    prepare_memory_services(db, store)
    protocol = MemoryProtocol(db)

    def write_one(item):
        return {"id": protocol.remember(
            f"lab notebook routine calibration note {item['i']}",
            namespace="bench", written_by="bench", agent_id="bench")}

    run_write_batch(
        db, [{"i": i} for i in range(8)], atomic=False, limit=100,
        write_item=write_one, measure_item=lambda *a: None,
        commit_measured=lambda *a: None)
    before_reads = read_stats()["full_reads"]
    before_scans = db._store._id_scans
    run_write_batch(
        db, [{"i": i} for i in range(8, 48)], atomic=False, limit=100,
        write_item=write_one, measure_item=lambda *a: None,
        commit_measured=lambda *a: None)
    assert read_stats()["full_reads"] - before_reads <= 3
    assert db._store._id_scans - before_scans <= 2
    assert read_stats()["cache_hits"] > 0


def test_empty_observation_page_keeps_its_cursor(tmp_path):
    store = tmp_path / "store"
    db = EmberDB.connect(str(store))
    prepare_memory_services(db, store)
    protocol = MemoryProtocol(db)
    protocol.remember("visible memory", namespace="bench", written_by="bench", agent_id="bench")
    db.grant_namespace_access("bench", "bench", "read")
    first = snapshot(db, "bench", "bench", observations=True)
    assert first["revision"] >= 1
    caught_up = snapshot(db, "bench", "bench", after=first["revision"], observations=True)
    assert caught_up["events"] == []
    assert caught_up["next_after"] == first["revision"]


def test_namespace_view_transport_resumes_and_backs_off():
    from pathlib import Path
    source = Path("embers/api/observer_transport.js").read_text()
    assert "new EmberFetchEventSource(this.scope.streamUrl())" in source
    assert "Last-Event-ID" in source
    assert "Math.min(1000 * 2 ** this._retry, 8000)" in source
    assert "Journal gap" in source
