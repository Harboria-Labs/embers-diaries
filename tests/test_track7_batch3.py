"""Batch 3 independent physical/index audit on isolated stores."""

import json
import multiprocessing
import os
from datetime import datetime, timedelta, timezone

import pytest

from embers import AccessLevel, EmberDB, EmberRecord
from embers.maintenance import (get_integrity_audit, recover_master_index,
                                run_integrity_audit)
from embers.storage.format import decode, encode, decode_index, encode_index


def _db(tmp_path):
    return EmberDB.connect(tmp_path / "audit_store")


def _record(db, namespace="audit"):
    return db.write(EmberRecord(namespace=namespace, data={"claim": "audited"}))


def _run(db, namespaces=None, actor="agent-a"):
    return run_integrity_audit(db, namespaces or ["audit"], actor)


def _record_path(db, rid):
    return db._store.records_dir / f"{rid}.ember"


def _edit_index(db, change):
    path = db._path / "indexes" / "master.json"
    data = decode_index(path.read_bytes())
    change(data)
    path.write_bytes(encode_index(data))


def _kill_audit_worker(store_path):
    import embers.maintenance as maintenance
    db = EmberDB.connect(store_path)
    maintenance.decode = lambda raw: os._exit(57)
    maintenance.run_integrity_audit(db, ["audit"], "agent-a")


def test_clean_audit_is_durable_and_read_only(tmp_path):
    db = _db(tmp_path)
    rid = _record(db)
    before = _record_path(db, rid).read_bytes()
    source_before = {str(path.relative_to(db._path)): path.read_bytes()
                     for path in db._path.rglob("*") if path.is_file()}
    result = _run(db)
    assert result["status"] == "complete" and result["certified"]
    assert result["records_examined"] == 1
    assert result["finding_counts"] == {}
    assert _record_path(db, rid).read_bytes() == before
    source_after = {str(path.relative_to(db._path)): path.read_bytes()
                    for path in db._path.rglob("*") if path.is_file()
                    and "integrity_audits" not in path.parts}
    assert source_after == source_before
    reopened = EmberDB.connect(db._path)
    assert get_integrity_audit(reopened, result["run_id"], "agent-a")["certified"]


@pytest.mark.parametrize("fault,category", [
    ("hash", "hash_mismatch"),
    ("malformed", "decode_failure"),
    ("missing", "missing_physical"),
    ("unsealed", "legacy_unsealed"),
])
def test_physical_findings_are_distinct(tmp_path, fault, category):
    db = _db(tmp_path)
    rid = _record(db)
    path = _record_path(db, rid)
    if fault == "hash":
        raw = decode(path.read_bytes())
        raw["data"]["claim"] = "tampered"
        path.write_bytes(encode(raw))
    elif fault == "malformed":
        path.write_bytes(b"not an ember record")
    elif fault == "missing":
        path.unlink()
    else:
        raw = decode(path.read_bytes())
        raw["content_hash"] = None
        path.write_bytes(encode(raw))
    result = _run(db)
    assert result["status"] == "complete"
    assert not result["certified"]
    assert result["finding_counts"].get(category, 0) >= 1
    with pytest.raises(ValueError):
        recover_master_index(db, result["run_id"], local_operator=True)


@pytest.mark.parametrize("fault,category", [
    ("identity", "identity_mismatch"),
    ("type", "invalid_metadata"),
    ("metadata", "invalid_metadata"),
])
def test_identity_type_and_required_metadata(tmp_path, fault, category):
    db = _db(tmp_path)
    rid = _record(db)
    path = _record_path(db, rid)
    raw = decode(path.read_bytes())
    if fault == "identity":
        raw["id"] = "wrong-id"
    elif fault == "type":
        raw["record_type"] = "unknown-type"
    else:
        del raw["created_at"]
    path.write_bytes(encode(raw))
    result = _run(db)
    assert result["finding_counts"].get(category, 0) >= 1
    assert not result["certified"]


@pytest.mark.parametrize("fault", ["wrong_type", "wrong_namespace", "missing_row", "stale_epoch"])
def test_index_drift_rebuilds_only_after_explicit_recovery(tmp_path, fault):
    db = _db(tmp_path)
    rid = _record(db)

    def drift(data):
        if fault == "wrong_type":
            data["records"][rid]["record_type"] = "conflict"
        elif fault == "wrong_namespace":
            data["records"][rid]["namespace"] = "other"
        elif fault == "missing_row":
            del data["records"][rid]
        else:
            data["indexed_epoch"] -= 1

    _edit_index(db, drift)
    result = _run(db)
    assert result["finding_counts"].get("derived_index_inconsistency", 0) >= 1
    assert not result["certified"]
    with pytest.raises(PermissionError):
        recover_master_index(db, result["run_id"])
    followup = recover_master_index(db, result["run_id"], local_operator=True)
    assert followup["certified"]
    history = get_integrity_audit(db, result["run_id"], "agent-a")
    assert history["recovery"][-1]["followup_run_id"] == followup["run_id"]


def test_obsolete_index_entry_is_visible_without_source_repair(tmp_path):
    db = _db(tmp_path)
    rid = _record(db)
    def add_obsolete(data):
        data["records"]["absent-id"] = dict(data["records"][rid])
    _edit_index(db, add_obsolete)
    result = _run(db)
    assert result["finding_counts"].get("missing_physical", 0) >= 1
    assert all(f.get("record_id") != "absent-id" for f in result["findings"])
    # Index-only identity cannot prove whether source was lost or metadata is
    # obsolete; recovery must not erase the only surviving evidence.
    with pytest.raises(ValueError):
        recover_master_index(db, result["run_id"], local_operator=True)


def test_lineage_and_deprecation_sidecar_findings(tmp_path):
    db = _db(tmp_path)
    parent = _record(db)
    child, _ = db.update(parent, {"claim": "new"})
    (db._path / "supersessions" / f"{parent}.superseded").unlink()
    result = _run(db)
    assert result["finding_counts"].get("inconsistent_lineage", 0) >= 1
    assert not result["certified"]
    db.deprecate(child)
    sidecar = db._path / "deprecations" / f"{child}.deprecated"
    sidecar.write_bytes(b"malformed")
    result = _run(db)
    assert result["finding_counts"].get("inconsistent_lineage", 0) >= 1


def test_missing_derivation_and_ambiguous_sidecar_cannot_be_repaired(tmp_path):
    db = _db(tmp_path)
    parent = _record(db)
    db.write(EmberRecord(namespace="audit", data={"claim": "derived"},
                         derived_from=["missing-source"]))
    db.update(parent, {"claim": "branch-a"})
    db.update(parent, {"claim": "branch-b"})
    (db._path / "supersessions" / f"{parent}.superseded").unlink()
    result = _run(db)
    assert result["finding_counts"].get("missing_source_reference", 0) >= 1
    assert result["finding_counts"].get("inconsistent_lineage", 0) >= 1
    with pytest.raises(ValueError):
        recover_master_index(db, result["run_id"], local_operator=True)


def test_concurrent_source_change_marks_report_stale(tmp_path, monkeypatch):
    import embers.maintenance as maintenance
    db = _db(tmp_path)
    _record(db)
    original = maintenance.decode
    written = False

    def write_mid_scan(raw):
        nonlocal written
        value = original(raw)
        if not written:
            written = True
            _record(db)
        return value

    monkeypatch.setattr(maintenance, "decode", write_mid_scan)
    result = _run(db)
    assert result["status"] == "stale"
    assert not result["certified"]


def test_later_write_makes_completed_report_stale(tmp_path):
    db = _db(tmp_path)
    _record(db)
    result = _run(db)
    assert result["certified"]
    _record(db)
    current = get_integrity_audit(db, result["run_id"], "agent-a")
    assert current["status"] == "stale" and not current["certified"]


def test_second_audit_rejected_while_first_is_running(tmp_path, monkeypatch):
    import embers.maintenance as maintenance
    db = _db(tmp_path)
    _record(db)
    original = maintenance.decode
    attempted = False

    def during_scan(raw):
        nonlocal attempted
        if not attempted:
            attempted = True
            with pytest.raises(RuntimeError, match="another integrity audit"):
                _run(db)
        return original(raw)

    monkeypatch.setattr(maintenance, "decode", during_scan)
    assert _run(db)["status"] == "complete"


def test_failed_and_interrupted_status_survives_restart(tmp_path, monkeypatch):
    import embers.maintenance as maintenance
    db = _db(tmp_path)
    _record(db)
    original = maintenance.decode_index

    def fail_index(raw):
        if b'"records"' in raw:
            raise RuntimeError("injected diagnostic failure")
        return original(raw)

    monkeypatch.setattr(maintenance, "decode_index", fail_index)
    result = _run(db)
    assert result["status"] == "complete" and not result["certified"]
    monkeypatch.setattr(maintenance, "decode_index", original)
    path = db._store.meta_dir / "integrity_audits" / f"{result['run_id']}.json"
    persisted = decode_index(path.read_bytes())
    persisted["status"] = "running"
    persisted["heartbeat_at"] = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    path.write_bytes(encode_index(persisted))
    reopened = EmberDB.connect(db._path)
    assert get_integrity_audit(reopened, result["run_id"], "agent-a")["status"] == "interrupted"


def test_killed_audit_process_never_claims_completion(tmp_path):
    db = _db(tmp_path)
    _record(db)
    ctx = multiprocessing.get_context("spawn")
    worker = ctx.Process(target=_kill_audit_worker, args=(str(db._path),))
    worker.start()
    worker.join(20)
    assert worker.exitcode == 57
    directory = db._store.meta_dir / "integrity_audits"
    active = decode_index((directory / "active.json").read_bytes())
    path = directory / f"{active['run_id']}.json"
    report = decode_index(path.read_bytes())
    assert report["status"] == "running" and not report["certified"]
    report["heartbeat_at"] = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    path.write_bytes(encode_index(report))
    reopened = EmberDB.connect(db._path)
    assert get_integrity_audit(reopened, active["run_id"], "agent-a")["status"] == "interrupted"


def test_private_and_mixed_scope_denied_before_report_write(tmp_path):
    db = _db(tmp_path)
    db.create_namespace("audit", access_level=AccessLevel.PRIVATE, owner="agent-a")
    _record(db)
    reports = db._store.meta_dir / "integrity_audits"
    with pytest.raises(PermissionError):
        _run(db, actor="agent-b")
    with pytest.raises(PermissionError):
        _run(db, namespaces=["other", "audit"], actor="agent-b")
    assert not reports.exists()
    result = _run(db)
    with pytest.raises(PermissionError):
        get_integrity_audit(db, result["run_id"], "agent-b")


def test_unknown_scope_decode_error_redacts_other_namespace_identity(tmp_path):
    db = _db(tmp_path)
    _record(db)
    private_id = _record(db, "other")
    _record_path(db, private_id).write_bytes(b"malformed private bytes")
    result = _run(db)
    assert result["finding_counts"].get("decode_failure", 0) >= 1
    assert all(private_id not in json.dumps(finding) for finding in result["findings"])
    assert not result["certified"]


def test_recovery_failure_is_durable_and_does_not_claim_success(tmp_path, monkeypatch):
    db = _db(tmp_path)
    rid = _record(db)
    _edit_index(db, lambda data: data["records"].pop(rid))
    result = _run(db)
    def interrupted():
        raise RuntimeError("injected recovery interruption")
    monkeypatch.setattr(db, "_reconcile_master_index", interrupted)
    with pytest.raises(RuntimeError, match="interruption"):
        recover_master_index(db, result["run_id"], local_operator=True)
    saved = get_integrity_audit(db, result["run_id"], "agent-a")
    assert saved["recovery"][-1]["status"] == "failed"
    assert saved["recovery"][-1]["error"] == "RuntimeError: index recovery failed"


def test_rest_mcp_audit_parity_and_redaction(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from embers import api
    from embers.identity.registry import AgentRegistry
    from embers.mcp.server import EmberMCP

    db = _db(tmp_path)
    owner, owner_token = AgentRegistry(db).register("audit-owner")
    outsider, outsider_token = AgentRegistry(db).register("audit-outsider")
    db.create_namespace("audit", access_level=AccessLevel.PRIVATE, owner=owner.agent_id)
    _record(db)
    monkeypatch.setattr(api, "_get_db", lambda: db)
    client = TestClient(api.app)
    headers = {"X-Ember-Agent-Id": owner.agent_id, "X-Ember-Token": owner_token}
    denied = {"X-Ember-Agent-Id": outsider.agent_id, "X-Ember-Token": outsider_token}
    assert client.post("/v1/maintenance/integrity/audits", headers=denied,
                       json={"namespaces": ["audit"]}).status_code == 403
    assert client.post("/v1/maintenance/integrity/audits", headers=denied,
                       json={"namespaces": ["open", "audit"]}).status_code == 403
    assert client.post("/v1/maintenance/integrity/audits", headers=headers,
                       json={"namespaces": "audit"}).status_code == 400
    response = client.post("/v1/maintenance/integrity/audits", headers=headers,
                           json={"namespaces": ["audit"]})
    assert response.status_code == 200, response.text
    run_id = response.json()["run_id"]
    assert client.get(f"/v1/maintenance/integrity/audits/{run_id}",
                      headers=denied).status_code == 403
    mcp = EmberMCP(db=db)
    call = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "ember_integrity_audit_status", "arguments": {
                           "run_id": run_id, "agent_id": owner.agent_id,
                           "token": owner_token}}})
    assert not call["result"]["isError"]
    assert json.loads(call["result"]["content"][0]["text"])["run_id"] == run_id
    started = mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                          "params": {"name": "ember_integrity_audit", "arguments": {
                              "namespaces": ["audit"], "agent_id": owner.agent_id,
                              "token": owner_token}}})
    assert not started["result"]["isError"]
    assert json.loads(started["result"]["content"][0]["text"])["certified"]
    denied_mcp = mcp.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                             "params": {"name": "ember_run_maintenance", "arguments": {
                                 "namespaces": ["audit"], "agent_id": outsider.agent_id,
                                 "token": outsider_token}}})
    assert denied_mcp["result"]["isError"]
