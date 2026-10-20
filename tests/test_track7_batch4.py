"""Verified ELLA state reuse on isolated stores."""

import multiprocessing
import os

import pytest

from embers import EmberDB
from embers.core.evidence import Evidence
from embers.integration import MemoryProtocol
from embers.cognitive.epistemic import (EpistemicLedger, clear_verified_state,
                                        verified_state_stats, enable_epistemic)
from embers.maintenance import run_integrity_audit


def _rig(tmp_path, namespace="ella"):
    db = EmberDB.connect(tmp_path / "store")
    rid = MemoryProtocol(db, default_namespace=namespace).remember(
        {"content": "exact claim", "verify_status": "hypothesis"}, room="personal")
    eid = db.attach_evidence(rid, Evidence(reference="artifact"))
    return db, rid, eid, EpistemicLedger(db, namespace)


def _report(db, rid, eid, namespace="ella", revision=0, request_id="one"):
    return EpistemicLedger(db, namespace).apply("report", {
        "target_memory_id": rid,
        "target_memory_version": db._store.read(rid).content_hash,
        "evidence_id": eid, "polarity": "SUPPORTS", "strength": "WEAK",
        "assessment_note": "independent evidence report",
    }, actor="agent", request_id=request_id, expected_revision=revision)


def test_cold_replay_warm_read_append_and_authoritative_equivalence(tmp_path, monkeypatch):
    import embers.cognitive.epistemic as epistemic
    db, rid, eid, ledger = _rig(tmp_path)
    clear_verified_state()
    loads = []
    original = EpistemicLedger.load
    def counted(self):
        loads.append(self.namespace)
        return original(self)
    monkeypatch.setattr(EpistemicLedger, "load", counted)
    first = ledger.read(rid, "agent")
    assert loads == ["ella"]
    enumerations = []
    all_ids = db._store.all_ids
    def counted_ids():
        enumerations.append(True)
        return all_ids()
    monkeypatch.setattr(db._store, "all_ids", counted_ids)
    warm = EpistemicLedger(db, "ella").read(rid, "agent")
    assert warm == first and loads == ["ella"]
    assert not enumerations
    before = verified_state_stats()
    committed = _report(db, rid, eid)
    assert len(loads) == 1
    after = verified_state_stats()
    assert after["warm_reuses"] > before["warm_reuses"]
    assert after["projections"] == 1 and after["retry_identities"] == 1
    assert EpistemicLedger(db, "ella").read(rid, "agent")["score"] == committed["score"]
    state, events = original(ledger)
    assert len(events) == 1 and state == epistemic._VERIFIED[ledger._cache_key()].state


def test_retry_before_cas_and_stale_revision(tmp_path):
    db, rid, eid, ledger = _rig(tmp_path)
    first = _report(db, rid, eid)
    assert _report(db, rid, eid) == first
    assert len(ledger.load()[1]) == 1
    with pytest.raises(ValueError, match="request id reused"):
        ledger.apply("report", {
            "target_memory_id": rid,
            "target_memory_version": db._store.read(rid).content_hash,
            "evidence_id": eid, "polarity": "OPPOSES", "strength": "WEAK",
            "assessment_note": "changed retry payload",
        }, actor="agent", request_id="one", expected_revision=0)
    with pytest.raises(ValueError, match="stale epistemic revision"):
        _report(db, rid, eid, request_id="two")


def test_independent_handles_and_process_catch_up(tmp_path):
    db, rid, eid, ledger = _rig(tmp_path)
    ledger.read(rid, "agent")
    other = EmberDB.connect(db._path)
    _report(other, rid, eid)
    ledger.read(rid, "agent")
    assert ledger._current().revision == 1
    before = verified_state_stats()["incremental_events"]
    second_eid = db.attach_evidence(rid, Evidence(reference="second artifact"))
    ctx = multiprocessing.get_context("spawn")
    worker = ctx.Process(target=_process_report_second,
                         args=(str(db._path), rid, second_eid))
    worker.start();worker.join(30)
    assert worker.exitcode == 0
    ledger.read(rid, "agent")
    assert ledger._current().revision == 2
    assert verified_state_stats()["incremental_events"] == before + 1
    assert ledger.load()[0] == ledger._current().state


def _process_report_second(path, rid, eid):
    db = EmberDB.connect(path)
    _report(db, rid, eid, revision=1, request_id="two")


def _kill_after_commit(path, rid, eid):
    db = EmberDB.connect(path)
    EpistemicLedger._publish = lambda self, state, event: os._exit(57)
    _report(db, rid, eid)


def _race_report(path, rid, eid, request_id, gate, results):
    db = EmberDB.connect(path)
    gate.wait(20)
    try:
        _report(db, rid, eid, request_id=request_id)
        results.put("committed")
    except ValueError as exc:
        results.put(str(exc))


def test_audit_boundary_invalidates_old_event_cache(tmp_path):
    db, rid, eid, ledger = _rig(tmp_path)
    _report(db, rid, eid)
    second = db.attach_evidence(rid, Evidence(reference="second"))
    _report(db, rid, second, revision=1, request_id="two")
    ledger.read(rid, "agent")
    first_id = ledger.prefix + "000000000001"
    (db._store.records_dir / f"{first_id}.ember").write_bytes(b"corrupt old event")
    # Old-file tampering is independent-audit territory, not a warm head check.
    report = run_integrity_audit(db, ["ella"], "agent")
    assert report["finding_counts"].get("decode_failure")
    with pytest.raises(ValueError, match="epistemic journal integrity"):
        ledger.read(rid, "agent")


def test_cache_isolation_eviction_and_explicit_reconstruction(tmp_path, monkeypatch):
    import embers.cognitive.epistemic as epistemic
    monkeypatch.setattr(epistemic, "_CACHE_LIMIT", 2)
    db, rid, _, ledger = _rig(tmp_path, "a")
    ledger.read(rid, "agent")
    for ns in ("b", "c"):
        other_rid = MemoryProtocol(db, default_namespace=ns).remember(
            {"content": ns}, room="personal")
        EpistemicLedger(db, ns).read(other_rid, "agent")
    assert verified_state_stats()["projections"] == 2
    assert ledger._cache_key() not in epistemic._VERIFIED
    ledger.read(rid, "agent")
    assert ledger._current().revision == 0
    clear_verified_state(db._path, "a")
    assert ledger._cache_key() not in epistemic._VERIFIED


def test_warm_revise_withdraw_merge_and_replay_equivalence(tmp_path):
    db, rid, eid, ledger = _rig(tmp_path)
    enable_epistemic(db, {"agent"})
    _report(db, rid, eid)
    second = db.attach_evidence(rid, Evidence(reference="second"))
    _report(db, rid, second, revision=1, request_id="two")
    version = db._store.read(rid).content_hash
    revised = ledger.apply("revise", {
        "target_memory_id": rid, "target_memory_version": version,
        "evidence_id": eid, "assessment_id": ledger.prefix+"000000000001:assessment",
        "polarity": "SUPPORTS", "strength": "MEDIUM",
        "assessment_note": "corrected strength",
    }, actor="agent", request_id="revise", expected_revision=2)
    assert revised["event_id"] == ledger.prefix+"000000000003"
    ledger.apply("withdraw", {
        "target_memory_id": rid, "target_memory_version": version,
        "assessment_id": ledger.prefix+"000000000002:assessment",
        "reason": "withdraw second assessment",
    }, actor="agent", request_id="withdraw", expected_revision=3)
    ledger.apply("merge", {
        "target_memory_id": rid, "target_memory_version": version,
        "evidence_ids": [eid, second], "reason": "shared origin",
    }, actor="agent", request_id="merge", expected_revision=4)
    assert ledger._current().state == ledger.load()[0]
    assert ledger._current().revision == 5


def test_failed_write_and_committed_before_projection_retry(tmp_path, monkeypatch):
    db, rid, eid, ledger = _rig(tmp_path)
    ledger.read(rid, "agent")
    original_write = db._writer.write
    def fail_before(record):
        raise RuntimeError("before source commit")
    monkeypatch.setattr(db._writer, "write", fail_before)
    with pytest.raises(RuntimeError, match="before source commit"):
        _report(db, rid, eid)
    assert ledger._current().revision == 0
    monkeypatch.setattr(db._writer, "write", original_write)
    original_publish = EpistemicLedger._publish
    def fail_after(self, state, event):
        raise RuntimeError("after source commit")
    monkeypatch.setattr(EpistemicLedger, "_publish", fail_after)
    committed = _report(db, rid, eid)
    assert ledger._current().revision == 1
    assert _report(db, rid, eid) == committed
    assert len(ledger.load()[1]) == 1
    monkeypatch.setattr(EpistemicLedger, "_publish", original_publish)


def test_missing_head_or_old_event_fails_after_audit(tmp_path):
    db, rid, eid, ledger = _rig(tmp_path)
    _report(db, rid, eid)
    second = db.attach_evidence(rid, Evidence(reference="second"))
    _report(db, rid, second, revision=1, request_id="two")
    ledger.read(rid, "agent")
    first_id = ledger.prefix+"000000000001"
    (db._store.records_dir / f"{first_id}.ember").unlink()
    result = run_integrity_audit(db, ["ella"], "agent")
    assert result["finding_counts"].get("missing_physical")
    with pytest.raises(ValueError, match="journal"):
        ledger.read(rid, "agent")


def test_corrupt_cached_head_fails_without_waiting_for_audit(tmp_path):
    db, rid, eid, ledger = _rig(tmp_path)
    _report(db, rid, eid)
    ledger.read(rid, "agent")
    head_id = ledger.prefix + "000000000001"
    (db._store.records_dir / f"{head_id}.ember").write_bytes(b"invalid head")
    with pytest.raises(ValueError, match="journal"):
        ledger.read(rid, "agent")


def test_rest_reconstructs_ledger_but_reuses_verified_state(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from embers import api
    from embers.identity.registry import AgentRegistry
    db, rid, eid, ledger = _rig(tmp_path)
    agent, token = AgentRegistry(db).register("reader")
    headers = {"X-Ember-Agent-Id": agent.agent_id, "X-Ember-Token": token}
    monkeypatch.setattr(api, "_get_db", lambda: db)
    clear_verified_state(db._path)
    calls = []
    original = EpistemicLedger.load
    def counted(self):
        calls.append(self.namespace)
        return original(self)
    monkeypatch.setattr(EpistemicLedger, "load", counted)
    client = TestClient(api.app)
    for _ in range(3):
        response = client.get("/v1/epistemic/state/ella", headers=headers,
                              params={"memory_id": rid})
        assert response.status_code == 200, response.text
    assert calls == ["ella"]


def test_killed_process_after_commit_replays_and_retries(tmp_path):
    db, rid, eid, ledger = _rig(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    worker = ctx.Process(target=_kill_after_commit, args=(str(db._path), rid, eid))
    worker.start();worker.join(30)
    assert worker.exitcode == 57
    reopened = EmberDB.connect(db._path)
    fresh = EpistemicLedger(reopened, "ella")
    state, events = fresh.load()
    assert len(events) == 1 and state == fresh._current().state
    assert _report(reopened, rid, eid) == events[0]["response"]


def test_different_stores_with_same_namespace_never_share_state(tmp_path):
    db_a, rid_a, eid_a, ledger_a = _rig(tmp_path / "a")
    db_b, rid_b, eid_b, ledger_b = _rig(tmp_path / "b")
    _report(db_a, rid_a, eid_a)
    assert ledger_a._current().revision == 1
    assert ledger_b._current().revision == 0
    _report(db_b, rid_b, eid_b)
    assert ledger_a._current().revision == ledger_b._current().revision == 1
    assert ledger_a._cache_key() != ledger_b._cache_key()


def test_repeated_incremental_results_equal_full_replay(tmp_path):
    db, rid, eid, ledger = _rig(tmp_path)
    for revision in range(8):
        evidence = eid if revision == 0 else db.attach_evidence(
            rid, Evidence(reference=f"artifact-{revision}"))
        _report(db, rid, evidence, revision=revision,
                request_id=f"report-{revision}")
        assert ledger._current().state == ledger.load()[0]
        assert ledger._current().revision == revision + 1


def test_independent_process_writers_preserve_revision_cas(tmp_path):
    db, rid, eid, ledger = _rig(tmp_path)
    second = db.attach_evidence(rid, Evidence(reference="second"))
    ctx = multiprocessing.get_context("spawn")
    gate, results = ctx.Event(), ctx.Queue()
    workers = [ctx.Process(target=_race_report,
                           args=(str(db._path), rid, evidence, request, gate, results))
               for evidence, request in ((eid, "first"), (second, "second"))]
    for worker in workers:worker.start()
    gate.set()
    for worker in workers:
        worker.join(30)
        assert worker.exitcode == 0
    outcomes = sorted(results.get(timeout=5) for _ in workers)
    assert outcomes == ["committed", "stale epistemic revision"]
    assert len(ledger.load()[1]) == 1
