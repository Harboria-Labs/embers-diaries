"""Behavioral coverage for the provider-neutral /v1 memory read surface."""

import pytest

from tests.test_v1_promotion import ASGIClient


@pytest.fixture
def api_client(tmp_path):
    import embers.api as api
    import embers.api.v1 as v1
    from embers import EmberDB

    api._db = EmberDB.connect(str(tmp_path / "store"))
    api._protocol = None
    api._registry = None
    v1._protocol = None
    v1._registry = None
    return ASGIClient(api.app), api


def _register(client):
    response = client.post(
        "/v1/agents/register",
        json={"name": "rest-reader", "provider": "local", "model": "test"},
    )
    assert response.status_code == 200, response.text
    registered = response.json()
    return {
        "X-Ember-Agent-Id": registered["agent_id"],
        "X-Ember-Token": registered["token"],
    }


def _write(client, auth, content, *, tags=None, namespace="rest-parity"):
    response = client.post(
        "/v1/memory/write",
        headers=auth,
        json={"content": content, "tags": tags or [], "namespace": namespace},
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


@pytest.mark.parametrize(
    "method,path,kwargs",
    [
        ("get", "/v1/memory/read/missing", {}),
        ("post", "/v1/memory/query", {"json": {"namespace": "rest-parity"}}),
        ("get", "/v1/memory/search", {"params": {"q": "secret"}}),
        ("get", "/v1/memory/history/missing", {}),
        ("get", "/v1/memory/graph/missing", {}),
    ],
)
def test_v1_memory_read_routes_require_authentication(
    api_client, method, path, kwargs,
):
    client, _api = api_client
    response = getattr(client, method)(path, **kwargs)
    assert response.status_code == 401


def test_read_query_and_search_mirror_core_results(api_client):
    client, _api = api_client
    auth = _register(client)
    wanted_id = _write(
        client, auth, "alpha comet observation", tags=["science", "reviewed"]
    )
    _write(client, auth, "beta accounting note", tags=["finance"])

    read = client.get(f"/v1/memory/read/{wanted_id}", headers=auth)
    assert read.status_code == 200, read.text
    assert read.json()["id"] == wanted_id
    assert read.json()["data"]["content"] == "alpha comet observation"

    queried = client.post(
        "/v1/memory/query",
        headers=auth,
        json={"namespace": "rest-parity", "tags": ["science"], "limit": 1},
    )
    assert queried.status_code == 200, queried.text
    assert queried.json()["count"] == 1
    assert [record["id"] for record in queried.json()["records"]] == [wanted_id]

    searched = client.get(
        "/v1/memory/search",
        headers=auth,
        params={"q": "alpha comet", "namespace": "rest-parity", "top_k": 1},
    )
    assert searched.status_code == 200, searched.text
    assert searched.json()["query"] == "alpha comet"
    assert searched.json()["results"][0]["record"]["id"] == wanted_id
    assert isinstance(searched.json()["results"][0]["score"], float)


def test_query_without_namespace_uses_the_protocol_namespace(api_client):
    client, api = api_client
    auth = _register(client)
    written = client.post(
        "/v1/memory/write",
        headers=auth,
        json={"content": "protocol-default query target"},
    )
    assert written.status_code == 200, written.text
    record_id = written.json()["id"]
    assert api._db.get(record_id).namespace == "memories"

    queried = client.post(
        "/v1/memory/query", headers=auth, json={"limit": 100})
    assert queried.status_code == 200, queried.text
    assert record_id in {record["id"] for record in queried.json()["records"]}


def test_read_and_query_honor_superseded_visibility(api_client):
    client, api = api_client
    from embers.core.types import DeprecationReason

    auth = _register(client)
    original_id = _write(client, auth, "original", tags=["lineage"])
    current_id, _ = api._db.update(
        original_id,
        {"content": "current"},
        written_by=auth["X-Ember-Agent-Id"],
        agent_id=auth["X-Ember-Agent-Id"],
    )
    deprecated_id = _write(client, auth, "retired", tags=["lineage"])
    assert api._db.deprecate(
        deprecated_id,
        DeprecationReason.MANUAL,
        "retired for visibility test",
        auth["X-Ember-Agent-Id"],
    )

    hidden = client.get(f"/v1/memory/read/{original_id}", headers=auth)
    assert hidden.status_code == 404
    visible = client.get(
        f"/v1/memory/read/{original_id}",
        headers=auth,
        params={"include_superseded": True},
    )
    assert visible.status_code == 200, visible.text
    assert visible.json()["id"] == original_id
    retired = client.get(f"/v1/memory/read/{deprecated_id}", headers=auth)
    assert retired.status_code == 404
    retired_visible = client.get(
        f"/v1/memory/read/{deprecated_id}",
        headers=auth,
        params={"include_deprecated": True},
    )
    assert retired_visible.status_code == 200, retired_visible.text
    assert retired_visible.json()["id"] == deprecated_id

    current_only = client.post(
        "/v1/memory/query",
        headers=auth,
        json={"namespace": "rest-parity"},
    )
    assert [record["id"] for record in current_only.json()["records"]] == [
        current_id
    ]
    with_history = client.post(
        "/v1/memory/query",
        headers=auth,
        json={
            "namespace": "rest-parity",
            "include_deprecated": True,
            "include_superseded": True,
        },
    )
    assert {record["id"] for record in with_history.json()["records"]} == {
        original_id,
        current_id,
        deprecated_id,
    }


def test_history_and_graph_preserve_core_order_and_depth(api_client):
    client, api = api_client
    auth = _register(client)
    original_id = _write(client, auth, "version one")
    current_id, _ = api._db.update(
        original_id,
        {"content": "version two"},
        written_by=auth["X-Ember-Agent-Id"],
        agent_id=auth["X-Ember-Agent-Id"],
    )
    neighbor_id = _write(client, auth, "direct neighbor")
    distant_id = _write(client, auth, "distant neighbor")
    assert api._db.link(current_id, neighbor_id)
    assert api._db.link(neighbor_id, distant_id)

    history = client.get(
        f"/v1/memory/history/{original_id}", headers=auth,
    )
    assert history.status_code == 200, history.text
    assert [record["id"] for record in history.json()["history"]] == [
        original_id,
        current_id,
    ]

    direct = client.get(
        f"/v1/memory/graph/{current_id}", headers=auth,
    )
    assert direct.status_code == 200, direct.text
    assert direct.json()["depth"] == 1
    assert [record["id"] for record in direct.json()["neighbors"]] == [
        neighbor_id
    ]

    nested = client.get(
        f"/v1/memory/graph/{current_id}",
        headers=auth,
        params={"depth": 2},
    )
    assert nested.status_code == 200, nested.text
    assert {record["id"] for record in nested.json()["neighbors"]} == {
        neighbor_id,
        distant_id,
    }


def test_v1_memory_reads_accept_an_active_session_header(api_client):
    client, _api = api_client
    auth = _register(client)
    record_id = _write(client, auth, "session-readable")
    started = client.post(
        "/v1/sessions", headers=auth, json={"task": "read memory"},
    )
    assert started.status_code == 200, started.text

    response = client.get(
        f"/v1/memory/read/{record_id}",
        headers={"X-Ember-Session-Id": started.json()["session_id"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["id"] == record_id
