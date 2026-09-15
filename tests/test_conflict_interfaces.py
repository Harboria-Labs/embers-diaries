"""End-to-end conflict authorization, identity, REST, and MCP parity."""

import json

import pytest

from embers import AccessLevel, EmberDB, EmberRecord, RecordType
from embers.mcp.server import EmberMCP, TOOLS
from tests.test_v1_promotion import ASGIClient


@pytest.fixture
def system(tmp_path):
    import embers.api as api
    import embers.api.v1 as v1

    db = EmberDB.connect(tmp_path / "conflict-interface-store")
    api._db = db
    api._protocol = None
    api._registry = None
    v1._protocol = None
    v1._registry = None
    return ASGIClient(api.app), EmberMCP(db=db), db


def _register(client, name):
    response = client.post("/v1/agents/register", json={"name": name})
    assert response.status_code == 200, response.text
    body = response.json()
    return body["agent_id"], body["token"], {
        "X-Ember-Agent-Id": body["agent_id"],
        "X-Ember-Token": body["token"],
    }


def _memory(db, namespace, content, actor):
    return db.write(EmberRecord(
        namespace=namespace,
        record_type=RecordType.DOCUMENT,
        data={"content": content},
        written_by=actor,
        agent_id=actor,
    ))


def _mcp_call(mcp, name, arguments):
    response = mcp.handle({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })
    return response["result"]


def _mcp_body(result):
    return json.loads(result["content"][0]["text"])


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("POST", "/v1/conflicts/map", {"memory_a": "a", "memory_b": "b"}),
        ("GET", "/v1/conflicts/open", None),
        ("GET", "/v1/memory/a/conflicts", None),
        ("POST", "/v1/conflicts/c/resolve", {"resolution": "done"}),
        ("PUT", "/v1/memory/a", {"data": {}, "expected_hash": "stale"}),
    ],
)
def test_rest_conflict_and_storage_routes_require_authentication(
    system, method, path, body,
):
    client, _mcp, _db = system
    response = client._request(method, path, json_body=body)
    assert response.status_code == 401


def test_private_namespace_ownership_attribution_and_reverse_idempotency(system):
    client, _mcp, db = system
    owner_id, _owner_token, owner = _register(client, "owner")
    _intruder_id, _intruder_token, intruder = _register(client, "intruder")
    db.create_namespace(
        "private-project", access_level=AccessLevel.PRIVATE, owner=owner_id)
    memory_a = _memory(db, "private-project", "backend is postgres", owner_id)
    memory_b = _memory(db, "private-project", "backend is mongo", owner_id)

    denied = client.post("/v1/conflicts/map", headers=intruder, json={
        "memory_a": memory_a, "memory_b": memory_b,
    })
    assert denied.status_code == 403
    assert db.conflict_records(namespace="private-project") == []

    mapped = client.post("/v1/conflicts/map", headers=owner, json={
        "memory_a": memory_a, "memory_b": memory_b, "note": "owner triage",
    })
    assert mapped.status_code == 200, mapped.text
    first = mapped.json()
    reverse = client.post("/v1/conflicts/map", headers=owner, json={
        "memory_a": memory_b, "memory_b": memory_a,
    })
    assert reverse.status_code == 200, reverse.text
    second = reverse.json()
    assert first["conflict_id"] == second["conflict_id"]
    assert first["record_id"] == second["record_id"]

    conflict_record = db.get(first["record_id"])
    assert conflict_record.written_by == owner_id
    assert conflict_record.agent_id == owner_id
    assert first["conflict"]["detected_by"] == owner_id
    assert first["conflict"]["conflict_id"] == first["conflict_id"]
    assert first["conflict"]["record_id"] == first["record_id"]


def test_mapping_errors_are_explicit_and_write_nothing(system):
    client, _mcp, db = system
    actor_id, _token, auth = _register(client, "mapper")
    db.create_namespace("one", AccessLevel.PRIVATE, owner=actor_id)
    db.create_namespace("two", AccessLevel.PRIVATE, owner=actor_id)
    memory_a = _memory(db, "one", "a", actor_id)
    memory_b = _memory(db, "one", "b", actor_id)
    memory_other = _memory(db, "two", "other", actor_id)

    cases = [
        ({"memory_a": memory_a, "memory_b": memory_a}, 400, "itself"),
        ({"memory_a": memory_a, "memory_b": "missing"}, 404, "not found"),
        ({"memory_a": memory_a, "memory_b": memory_other}, 400,
         "Cross-namespace"),
        ({"memory_a": memory_a, "memory_b": memory_b,
          "conflict_type": "storage"}, 400, "Storage conflicts"),
    ]
    for body, status, message in cases:
        response = client.post("/v1/conflicts/map", headers=auth, json=body)
        assert response.status_code == status, response.text
        assert message.lower() in response.text.lower()
    assert db.conflict_records() == []


def test_cross_namespace_check_does_not_bypass_read_authorization(system):
    client, _mcp, db = system
    first_id, _token, first = _register(client, "first-owner")
    second_id, _token2, _second = _register(client, "second-owner")
    db.create_namespace("first-private", AccessLevel.PRIVATE, owner=first_id)
    db.create_namespace("second-private", AccessLevel.PRIVATE, owner=second_id)
    memory_a = _memory(db, "first-private", "a", first_id)
    memory_b = _memory(db, "second-private", "b", second_id)

    response = client.post("/v1/conflicts/map", headers=first, json={
        "memory_a": memory_a, "memory_b": memory_b,
    })
    assert response.status_code == 403
    assert "Cross-namespace" not in response.text


def test_open_queue_per_memory_lookup_include_closed_and_namespace_filter(system):
    client, _mcp, db = system
    actor_id, _token, auth = _register(client, "triage")
    for namespace in ("alpha", "beta"):
        db.create_namespace(namespace, AccessLevel.PRIVATE, owner=actor_id)

    alpha_a = _memory(db, "alpha", "alpha-a", actor_id)
    alpha_b = _memory(db, "alpha", "alpha-b", actor_id)
    beta_a = _memory(db, "beta", "beta-a", actor_id)
    beta_b = _memory(db, "beta", "beta-b", actor_id)
    alpha = client.post("/v1/conflicts/map", headers=auth, json={
        "memory_a": alpha_a, "memory_b": alpha_b,
    }).json()
    client.post("/v1/conflicts/map", headers=auth, json={
        "memory_a": beta_a, "memory_b": beta_b,
    })

    investigating = client.post(
        f"/v1/conflicts/{alpha['conflict_id']}/resolve",
        headers=auth,
        json={"status": "investigating", "resolution": "checking"},
    )
    assert investigating.status_code == 200, investigating.text
    queue = client.get(
        "/v1/conflicts/open", headers=auth, params={"namespace": "alpha"})
    assert queue.status_code == 200, queue.text
    assert queue.json()["count"] == 1
    assert queue.json()["conflicts"][0]["status"] == "investigating"

    resolved = client.post(
        f"/v1/conflicts/{alpha['conflict_id']}/resolve",
        headers=auth,
        json={"status": "resolved", "resolution": "alpha-a wins",
              "winner_id": alpha_a},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["conflict"]["winner_id"] == alpha_a
    assert client.get(
        "/v1/conflicts/open", headers=auth,
        params={"namespace": "alpha"}).json()["count"] == 0

    live = client.get(f"/v1/memory/{alpha_a}/conflicts", headers=auth)
    assert live.status_code == 200
    assert live.json()["conflicts"] == []
    history = client.get(
        f"/v1/memory/{alpha_b}/conflicts", headers=auth,
        params={"include_closed": True})
    assert history.status_code == 200
    assert history.json()["conflicts"][0]["status"] == "resolved"
    assert history.json()["conflicts"][0]["winner_id"] == alpha_a


@pytest.mark.parametrize(
    "public_status,stored_status",
    [
        ("resolved", "resolved"),
        ("accepted_both", "accepted_both"),
        ("dismissed", "superseded"),
        ("superseded", "superseded"),
    ],
)
def test_all_terminal_lifecycle_decisions_are_supported(
    system, public_status, stored_status,
):
    client, _mcp, db = system
    actor_id, _token, auth = _register(client, f"resolver-{public_status}")
    namespace = f"status-{public_status}"
    memory_a = _memory(db, namespace, "a", actor_id)
    memory_b = _memory(db, namespace, "b", actor_id)
    mapped = client.post("/v1/conflicts/map", headers=auth, json={
        "memory_a": memory_a, "memory_b": memory_b,
    }).json()

    response = client.post(
        f"/v1/conflicts/{mapped['conflict_id']}/resolve",
        headers=auth,
        json={"status": public_status, "resolution": "decision recorded"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == stored_status
    assert db.get(memory_a).data == {"content": "a"}
    assert db.get(memory_b).data == {"content": "b"}

    repeated = client.post(
        f"/v1/conflicts/{mapped['conflict_id']}/resolve",
        headers=auth,
        json={"status": "investigating", "resolution": "reopen"},
    )
    assert repeated.status_code == 400
    assert "already closed" in repeated.text


def test_stable_conflict_identity_is_distinct_from_append_only_versions(system):
    client, _mcp, db = system
    actor_id, _token, auth = _register(client, "identity-reviewer")
    memory_a = _memory(db, "identity", "a", actor_id)
    memory_b = _memory(db, "identity", "b", actor_id)
    mapped = client.post("/v1/conflicts/map", headers=auth, json={
        "memory_a": memory_a, "memory_b": memory_b,
    }).json()
    root_id = mapped["conflict_id"]
    first_record_id = mapped["record_id"]
    assert root_id == first_record_id

    changed = client.post(
        f"/v1/conflicts/{root_id}/resolve", headers=auth,
        json={"status": "investigating", "resolution": "triage started"})
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body["conflict_id"] == root_id
    assert body["record_id"] != root_id
    assert body["superseded_record_id"] == first_record_id
    assert body["conflict"]["record_version"] == 2

    history = db.get_history(root_id)
    assert [record.data["status"] for record in history] == [
        "open", "investigating"]
    assert history[-1].agent_id == actor_id
    assert history[-1].written_by == actor_id


def test_invalid_status_resolution_and_winner_are_rejected(system):
    client, _mcp, db = system
    actor_id, _token, auth = _register(client, "validator")
    memory_a = _memory(db, "validation", "a", actor_id)
    memory_b = _memory(db, "validation", "b", actor_id)
    mapped = client.post("/v1/conflicts/map", headers=auth, json={
        "memory_a": memory_a, "memory_b": memory_b,
    }).json()
    path = f"/v1/conflicts/{mapped['conflict_id']}/resolve"

    invalid = [
        ({"status": "unknown", "resolution": "x"}, "status must"),
        ({"status": "resolved", "resolution": ""}, "resolution is required"),
        ({"status": "resolved", "resolution": "winner", "winner_id": "bad"},
         "winner_id must"),
        ({"status": "accepted_both", "resolution": "both",
          "winner_id": memory_a}, "only valid for resolved"),
    ]
    for payload, message in invalid:
        response = client.post(path, headers=auth, json=payload)
        assert response.status_code == 400, response.text
        assert message in response.text
    assert len(db.get_history(mapped["conflict_id"])) == 1


def test_direct_mcp_conflict_tools_and_rest_share_one_contract(system):
    client, mcp, db = system
    actor_id, token, auth = _register(client, "multi-transport")
    mcp_auth = {"agent_id": actor_id, "token": token}
    memory_a = _memory(db, "parity", "a", actor_id)
    memory_b = _memory(db, "parity", "b", actor_id)

    for tool, arguments in [
        ("ember_map_conflict", {"memory_a": memory_a, "memory_b": memory_b}),
        ("ember_conflicts_for", {"memory_id": memory_a}),
        ("ember_open_conflicts", {"namespace": "parity"}),
        ("ember_resolve_conflict", {
            "conflict_id": "unknown", "resolution": "x"}),
    ]:
        assert _mcp_call(mcp, tool, arguments)["isError"] is True

    mapped_result = _mcp_call(mcp, "ember_map_conflict", {
        "memory_a": memory_a, "memory_b": memory_b, **mcp_auth,
    })
    assert mapped_result["isError"] is False
    mapped = _mcp_body(mapped_result)
    rest_reverse = client.post("/v1/conflicts/map", headers=auth, json={
        "memory_a": memory_b, "memory_b": memory_a,
    }).json()
    assert rest_reverse["conflict"] == mapped["conflict"]

    mcp_queue = _mcp_body(_mcp_call(mcp, "ember_open_conflicts", {
        "namespace": "parity", **mcp_auth,
    }))
    rest_queue = client.get(
        "/v1/conflicts/open", headers=auth,
        params={"namespace": "parity"}).json()["conflicts"]
    assert mcp_queue == rest_queue

    transitioned = _mcp_body(_mcp_call(mcp, "ember_resolve_conflict", {
        "conflict_id": mapped["conflict_id"],
        "status": "resolved", "resolution": "a wins",
        "winner_id": memory_a, **mcp_auth,
    }))
    assert transitioned["conflict_id"] == mapped["conflict_id"]
    assert transitioned["record_id"] != mapped["record_id"]
    assert transitioned["conflict"]["winner_id"] == memory_a

    mcp_all = _mcp_body(_mcp_call(mcp, "ember_conflicts_for", {
        "memory_id": memory_b, "include_closed": True, **mcp_auth,
    }))
    rest_all = client.get(
        f"/v1/memory/{memory_b}/conflicts", headers=auth,
        params={"include_closed": True}).json()["conflicts"]
    assert mcp_all == rest_all


def test_rest_and_mcp_return_structured_storage_conflicts(system):
    client, mcp, db = system
    actor_id, token, auth = _register(client, "cas-writer")
    mcp_auth = {"agent_id": actor_id, "token": token}

    rest_root = _memory(db, "cas", "v1", actor_id)
    stale_hash = db.get(rest_root).content_hash
    winner = client._request("PUT", f"/v1/memory/{rest_root}", headers=auth,
                             json_body={
                                 "data": {"content": "rest winner"},
                                 "expected_hash": stale_hash,
                             })
    assert winner.status_code == 200, winner.text
    rejected = client._request("PUT", f"/v1/memory/{rest_root}", headers=auth,
                               json_body={
                                   "data": {"content": "rest loser"},
                                   "expected_hash": stale_hash,
                               })
    assert rejected.status_code == 409, rejected.text
    storage_conflict = rejected.json()
    assert storage_conflict["error"] == "ConcurrentModificationError"
    assert storage_conflict["conflict_type"] == "storage"
    assert storage_conflict["status"] == "rejected"
    assert storage_conflict["current_id"] == winner.json()["record_id"]
    assert len(db.get_history(rest_root)) == 2

    mcp_root = _memory(db, "cas", "mcp-v1", actor_id)
    mcp_stale = db.get(mcp_root).content_hash
    first = _mcp_call(mcp, "ember_update", {
        "record_id": mcp_root, "data": {"content": "mcp winner"},
        "expected_hash": mcp_stale, **mcp_auth,
    })
    assert first["isError"] is False
    second = _mcp_call(mcp, "ember_update", {
        "record_id": mcp_root, "data": {"content": "mcp loser"},
        "expected_hash": mcp_stale, **mcp_auth,
    })
    assert second["isError"] is True
    mcp_conflict = _mcp_body(second)
    assert mcp_conflict["conflict_type"] == "storage"
    assert mcp_conflict["status"] == "rejected"
    assert len(db.get_history(mcp_root)) == 2

    assert "ember_update" in {tool["name"] for tool in TOOLS}


def test_private_conflicts_cannot_escape_through_generic_memory_routes(system):
    client, mcp, db = system
    owner_id, _owner_token, _owner = _register(client, "private-owner")
    _intruder_id, _intruder_token, intruder = _register(client, "outsider")
    intruder_id = intruder["X-Ember-Agent-Id"]
    intruder_token = intruder["X-Ember-Token"]
    mcp_intruder = {"agent_id": intruder_id, "token": intruder_token}

    db.create_namespace(
        "sealed", access_level=AccessLevel.PRIVATE, owner=owner_id)
    memory_a = _memory(db, "sealed", "secret alpha claim", owner_id)
    memory_b = _memory(db, "sealed", "secret beta claim", owner_id)
    conflict_id = db.map_conflict(memory_a, memory_b, detected_by=owner_id)

    rest_requests = [
        ("GET", f"/v1/memory/read/{conflict_id}", None),
        ("POST", "/v1/memory/query", {"namespace": "sealed"}),
        ("GET", f"/v1/memory/history/{conflict_id}", None),
        ("GET", f"/v1/memory/graph/{conflict_id}", None),
        ("POST", "/v1/memory/recall", {
            "query": "secret", "namespace": "sealed"}),
        ("POST", "/v1/memory/write", {
            "content": "unauthorized", "namespace": "sealed"}),
    ]
    for method, path, payload in rest_requests:
        response = client._request(
            method, path, headers=intruder, json_body=payload)
        assert response.status_code == 403, (method, path, response.text)
    search = client.get(
        "/v1/memory/search", headers=intruder,
        params={"q": "secret", "namespace": "sealed"})
    assert search.status_code == 403, search.text

    mcp_requests = [
        ("ember_read", {"record_id": conflict_id}),
        ("ember_query", {"namespace": "sealed"}),
        ("ember_search", {"query": "secret", "namespace": "sealed"}),
        ("ember_get_history", {"record_id": conflict_id}),
        ("ember_get_graph", {"record_id": conflict_id}),
        ("ember_recall", {"query": "secret", "namespace": "sealed"}),
        ("ember_write", {"content": "unauthorized", "namespace": "sealed"}),
        ("ember_reflect", {"namespace": "sealed"}),
    ]
    for tool, arguments in mcp_requests:
        result = _mcp_call(mcp, tool, {**arguments, **mcp_intruder})
        assert result["isError"] is True, tool

    assert db.get_conflict(conflict_id, caller=owner_id) is not None
    with pytest.raises(PermissionError):
        db.get_conflict(conflict_id, caller=intruder_id)


def test_broad_search_and_cross_namespace_graph_filter_private_records(system):
    client, mcp, db = system
    owner_id, _owner_token, _owner = _register(client, "graph-owner")
    outsider_id, outsider_token, outsider = _register(client, "graph-outsider")
    mcp_outsider = {"agent_id": outsider_id, "token": outsider_token}

    db.create_namespace(
        "graph-private", access_level=AccessLevel.PRIVATE, owner=owner_id)
    public_id = _memory(db, "graph-public", "visible marker", owner_id)
    private_id = _memory(db, "graph-private", "hidden marker", owner_id)
    db.link(public_id, private_id, label="references")

    rest_search = client.get(
        "/v1/memory/search", headers=outsider,
        params={"q": "marker"}).json()["results"]
    assert private_id not in {row["record"]["id"] for row in rest_search}
    rest_graph = client.get(
        f"/v1/memory/graph/{public_id}", headers=outsider).json()["neighbors"]
    assert private_id not in {row["id"] for row in rest_graph}

    mcp_search = _mcp_body(_mcp_call(mcp, "ember_search", {
        "query": "marker", **mcp_outsider,
    }))
    assert private_id not in {row["id"] for row in mcp_search}
    mcp_graph = _mcp_body(_mcp_call(mcp, "ember_get_graph", {
        "record_id": public_id, **mcp_outsider,
    }))
    assert private_id not in {row["id"] for row in mcp_graph}
