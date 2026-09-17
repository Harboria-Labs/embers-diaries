"""Native-engine persistence tests on isolated temporary Ember stores."""
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor

import pytest

from embers.db import EmberDB
from embers.db_feedback import bind
from embers.core.feedback import Feedback
from embers.core.record import EmberRecord
from embers.cognitive.feedback_replay import Credit, RelevanceDecision, RevisionConflict
from embers.cognitive.feedback_durable import DurableRelevanceJournal
from embers.core.errors import StorageLimitError

bind(EmberDB)
CONFIG = dict(namespace="memories", context_id="debug", context={"task": "debug"},
              authorized_resolvers=frozenset({"resolver"}),
              memory_rate=.25, pair_rate=.25)


def setup(tmp_path):
    path = str(tmp_path / "store")
    db = EmberDB.connect(path)
    mid = db.write(EmberRecord(namespace="memories", data={"content": "a claim"}))
    fb = Feedback.from_submission(mid, "agent", dict(
        schema_version=2, channel="relevance", outcome="useful",
        outcome_id="result", context_id="debug", context={"task": "debug"}, signal=1,
    ))
    fid = db.give_feedback(mid, fb)
    service = db.relevance_journal(**CONFIG)
    decision = RelevanceDecision(
        outcome_id="result", status="accepted", credits=(Credit(mid, 1),),
        report_ids=(fid,), reason="observed contribution",
    )
    return db, service, decision, path


def resolve(service, decision, request="r1", revision=0):
    return service.resolve(decision, actor="resolver", request_id=request,
                           expected_revision=revision)


def test_restart_and_two_handles_share_canonical_history(tmp_path):
    db, service, decision, path = setup(tmp_path)
    first = resolve(service, decision)
    other = EmberDB.connect(path).relevance_journal(**CONFIG)
    assert other.project() == service.project()
    assert resolve(other, decision) == first
    assert len(other.history()) == 1
    corrected = replace(decision, credits=(Credit(decision.credits[0].memory_version, -1),))
    resolve(other, corrected, request="fix", revision=1)
    assert service.project() == other.project()
    assert list(service.project().memory_bias.values()) == [-.25]


def test_lost_acknowledgement_retry_does_not_duplicate(tmp_path, monkeypatch):
    db, service, decision, _ = setup(tmp_path)
    original = db._writer.write
    def lost_ack(record):
        original(record)
        raise OSError("simulated lost acknowledgement after durable write")
    monkeypatch.setattr(db._writer, "write", lost_ack)
    with pytest.raises(OSError):
        resolve(service, decision)
    monkeypatch.setattr(db._writer, "write", original)
    resolve(service, decision)
    assert len(service.history()) == 1
    assert list(service.project().memory_bias.values()) == [.25]


def test_failed_admission_does_not_advance_projection(tmp_path):
    db, service, decision, _ = setup(tmp_path)
    db._store.max_record_bytes = 1
    with pytest.raises(StorageLimitError):
        resolve(service, decision)
    db._store.max_record_bytes = 0
    assert service.project().generation == 0
    assert service.history() == ()
    resolve(service, decision)
    assert service.project().generation == 1


def test_configuration_cannot_silently_reinterpret_history(tmp_path):
    db, service, decision, _ = setup(tmp_path)
    resolve(service, decision)
    with pytest.raises(ValueError, match="migration"):
        DurableRelevanceJournal(db, **dict(CONFIG, memory_rate=.5))
    with pytest.raises(ValueError, match="migration"):
        DurableRelevanceJournal(db, **dict(CONFIG, context={"task": "another"}))


def test_namespace_and_report_scope_checked_before_persistence(tmp_path):
    db, service, decision, _ = setup(tmp_path)
    outside = db.write(EmberRecord(namespace="other", data={"content": "outside"}))
    with pytest.raises(ValueError):
        resolve(service, replace(decision, credits=(Credit(outside, 1),)))
    with pytest.raises(ValueError):
        resolve(service, replace(decision, outcome_id="different-outcome"))
    with pytest.raises(PermissionError):
        service.resolve(decision, actor="untrusted", request_id="x", expected_revision=0)
    assert service.project().generation == 0


def test_competing_handles_enforce_revision_check(tmp_path):
    db, service, decision, path = setup(tmp_path)
    resolve(service, decision)
    other = EmberDB.connect(path).relevance_journal(**CONFIG)
    def attempt(pair):
        target, value = pair
        changed = replace(decision, credits=(Credit(decision.credits[0].memory_version, value),))
        try:
            resolve(target, changed, request=str(value), revision=1)
            return "accepted"
        except RevisionConflict:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, [(service, -1), (other, .5)]))
    assert sorted(outcomes) == ["accepted", "conflict"]
    assert len(service.history()) == 2


def test_missing_middle_event_fails_closed(tmp_path):
    db, service, decision, _ = setup(tmp_path)
    resolve(service, decision)
    changed = replace(decision, credits=(Credit(decision.credits[0].memory_version, -1),))
    resolve(service, changed, request="fix", revision=1)
    # Deliberate corruption confined to the test's isolated temporary store.
    for rid in db._store.all_ids():
        record = db._store.read(rid)
        if isinstance(record.data, dict) and record.data.get("sequence") == 1:
            (db._store.records_dir / (rid + ".ember")).unlink()
    with pytest.raises(ValueError, match="broken"):
        service.project()


def test_truth_memory_hash_and_confidence_unchanged(tmp_path):
    db, service, decision, _ = setup(tmp_path)
    mid = decision.credits[0].memory_version
    before = db.get(mid).to_dict()
    resolve(service, decision)
    assert db.get(mid).to_dict() == before
