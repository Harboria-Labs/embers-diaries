"""Small isolated ELLA cold/warm comparison; no live store access."""

import json
import multiprocessing
from pathlib import Path
import tempfile
import time

from embers import EmberDB
from embers.core.evidence import Evidence
from embers.integration import MemoryProtocol
from embers.cognitive.epistemic import (EpistemicLedger, clear_verified_state,
                                        verified_state_stats)
import embers.cognitive.epistemic as epistemic


def append_in_child(path, rid, revision):
    db = EmberDB.connect(path)
    evidence = db.attach_evidence(rid, Evidence(reference="cross-process-artifact"))
    EpistemicLedger(db, "perf").apply("report", {
        "target_memory_id": rid,
        "target_memory_version": db._store.read(rid).content_hash,
        "evidence_id": evidence, "polarity": "SUPPORTS", "strength": "WEAK",
        "assessment_note": "cross-process measurement",
    }, actor="agent", request_id="cross-process-report",
        expected_revision=revision)


def main():
    root = Path(__file__).resolve().parents[1]
    # Windows may briefly retain the observer SQLite handle until process exit.
    # The caller removes any named isolated fixture after the process exits.
    with tempfile.TemporaryDirectory(prefix=".test_batch4_perf_", dir=root,
                                     ignore_cleanup_errors=True) as directory:
        db = EmberDB.connect(Path(directory) / "store")
        rid = MemoryProtocol(db, default_namespace="perf").remember(
            {"content": "bounded evidence claim"}, room="personal")
        ledger = EpistemicLedger(db, "perf")
        version = db._store.read(rid).content_hash
        original_read = db._store.read_strict
        original_call = epistemic.call
        counts = {"journal_reads": 0, "native_reduces": 0}

        def read_counted(record_id):
            if record_id.startswith(ledger.prefix):
                counts["journal_reads"] += 1
            return original_read(record_id)

        def call_counted(operation, value):
            if operation == "ella_reduce":
                counts["native_reduces"] += 1
            return original_call(operation, value)

        db._store.read_strict = read_counted
        epistemic.call = call_counted
        try:
            revision = 0
            def append_report():
                nonlocal revision
                evidence = db.attach_evidence(rid, Evidence(reference=f"artifact-{revision+1}"))
                ledger.apply("report", {
                    "target_memory_id": rid, "target_memory_version": version,
                    "evidence_id": evidence, "polarity": "SUPPORTS",
                    "strength": "WEAK", "assessment_note": "isolated measurement",
                }, actor="agent", request_id=f"report-{revision+1}",
                    expected_revision=revision)
                revision += 1
            for number in (5, 10, 20, 40):
                while revision < number:
                    append_report()
                clear_verified_state(db._path, "perf")
                results = {}
                for label in ("cold_read", "warm_read", "authoritative_replay"):
                    counts.update(journal_reads=0, native_reduces=0)
                    start = time.perf_counter_ns()
                    if label == "authoritative_replay":
                        state, events = ledger.load()
                        assert len(events) == number
                    else:
                        ledger.read(rid, "agent")
                    elapsed = (time.perf_counter_ns() - start) / 1_000_000
                    results[label] = {"wall_ms": round(elapsed, 3), **counts}
                    if label == "authoritative_replay":
                        assert state == ledger._current().state
                counts.update(journal_reads=0, native_reduces=0)
                start = time.perf_counter_ns()
                append_report()
                results["warm_append"] = {
                    "wall_ms": round((time.perf_counter_ns()-start)/1_000_000, 3), **counts}
                clear_verified_state(db._path, "perf")
                counts.update(journal_reads=0, native_reduces=0)
                start = time.perf_counter_ns()
                append_report()
                results["cold_append"] = {
                    "wall_ms": round((time.perf_counter_ns()-start)/1_000_000, 3), **counts}
                results["projection_state_bytes"] = verified_state_stats()["state_bytes"]
                results["events"] = number
                print(json.dumps(results, sort_keys=True), flush=True)
            worker = multiprocessing.get_context("spawn").Process(
                target=append_in_child, args=(str(db._path), rid, revision))
            worker.start();worker.join(30)
            assert worker.exitcode == 0
            counts.update(journal_reads=0, native_reduces=0)
            start = time.perf_counter_ns()
            ledger.read(rid, "agent")
            print(json.dumps({"cross_process_catchup": {
                "wall_ms": round((time.perf_counter_ns()-start)/1_000_000, 3), **counts}},
                sort_keys=True), flush=True)
            from embers.maintenance import run_integrity_audit
            start = time.perf_counter_ns()
            audit = run_integrity_audit(db, ["perf"], "agent")
            audit_ms = (time.perf_counter_ns()-start)/1_000_000
            counts.update(journal_reads=0, native_reduces=0)
            start = time.perf_counter_ns()
            ledger.read(rid, "agent")
            print(json.dumps({"independent_audit": {
                "wall_ms": round(audit_ms, 3), "certified": audit["certified"]},
                "post_audit_replay": {"wall_ms": round((time.perf_counter_ns()-start)/1_000_000, 3),
                                      **counts}}, sort_keys=True), flush=True)
        finally:
            db._store.read_strict = original_read
            epistemic.call = original_call


if __name__ == "__main__":
    main()
