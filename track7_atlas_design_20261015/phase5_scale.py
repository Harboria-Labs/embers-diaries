"""Disposable Phase 5 operation-count probe; run from repository root."""

import json
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from embers import (ConflictStatus, EmberDB, EmberRecord, Evidence,
                    MemoryProposal, PromotionMode, PromotionPolicy, SourceType)
from embers.config import SearchConfig
from embers.integration import MemoryProtocol
from embers.maintenance import run_integrity_audit
from embers.index.master import MasterIndex
from embers.storage.store import PhysicalStore


def measure(db, label, operation, repetitions=5):
    store, master = db._store, db._master_index
    originals = (store.read, store.read_strict, store.all_ids,
                 master._candidate_fingerprint, db._reconcile_master_index)
    samples = []
    for _ in range(repetitions):
        counts = {"physical_read_calls": 0, "all_ids_enumerations": 0,
                  "candidate_fingerprints": 0, "fingerprint_rows": 0,
                  "fingerprint_cpu_ms": 0.0, "fingerprint_wall_ms": 0.0,
                  "source_reconciliations": 0}

        def read(rid):
            counts["physical_read_calls"] += 1
            return originals[0](rid)

        def strict(rid):
            counts["physical_read_calls"] += 1
            return originals[1](rid)

        def all_ids():
            counts["all_ids_enumerations"] += 1
            return originals[2]()

        def fingerprint():
            counts["candidate_fingerprints"] += 1
            counts["fingerprint_rows"] += len(master._records)
            cpu_start = time.process_time_ns()
            wall_start = time.perf_counter_ns()
            try:
                return originals[3]()
            finally:
                counts["fingerprint_cpu_ms"] += (time.process_time_ns() - cpu_start) / 1_000_000
                counts["fingerprint_wall_ms"] += (time.perf_counter_ns() - wall_start) / 1_000_000

        def reconcile():
            counts["source_reconciliations"] += 1
            return originals[4]()

        store.read, store.read_strict, store.all_ids = read, strict, all_ids
        master._candidate_fingerprint = fingerprint
        db._reconcile_master_index = reconcile
        start = time.perf_counter_ns()
        try:
            result = operation()
            elapsed = (time.perf_counter_ns() - start) / 1_000_000
            counts["fingerprint_cpu_ms"] = round(counts["fingerprint_cpu_ms"], 3)
            counts["fingerprint_wall_ms"] = round(counts["fingerprint_wall_ms"], 3)
            samples.append({"ms": round(elapsed, 3), **counts,
                            "result_count": len(result) if isinstance(result, list) else None})
        finally:
            (store.read, store.read_strict, store.all_ids,
             master._candidate_fingerprint, db._reconcile_master_index) = originals
    latencies = sorted(s["ms"] for s in samples)
    return {"operation": label, "samples": samples,
            "median_ms": statistics.median(latencies),
            "max_ms": latencies[-1]}


def measure_cold_open(path):
    """Count physical certification and fingerprint work on a second DB open."""
    counts = {"physical_read_calls": 0, "all_ids_enumerations": 0,
              "candidate_fingerprints": 0, "fingerprint_rows": 0,
              "fingerprint_cpu_ms": 0.0, "source_reconciliations": 0}
    read, strict, all_ids = PhysicalStore.read, PhysicalStore.read_strict, PhysicalStore.all_ids
    fingerprint = MasterIndex._candidate_fingerprint
    reconcile = EmberDB._reconcile_master_index

    def wrapped_read(self, rid):
        counts["physical_read_calls"] += 1
        return read(self, rid)

    def wrapped_strict(self, rid):
        counts["physical_read_calls"] += 1
        return strict(self, rid)

    def wrapped_ids(self):
        counts["all_ids_enumerations"] += 1
        return all_ids(self)

    def wrapped_fingerprint(self):
        counts["candidate_fingerprints"] += 1
        counts["fingerprint_rows"] += len(self._records)
        start = time.process_time_ns()
        try:
            return fingerprint(self)
        finally:
            counts["fingerprint_cpu_ms"] += (time.process_time_ns() - start) / 1_000_000

    def wrapped_reconcile(self):
        counts["source_reconciliations"] += 1
        return reconcile(self)

    start = time.perf_counter_ns()
    with patch.object(PhysicalStore, "read", wrapped_read), patch.object(
            PhysicalStore, "read_strict", wrapped_strict), patch.object(
            PhysicalStore, "all_ids", wrapped_ids), patch.object(
            MasterIndex, "_candidate_fingerprint", wrapped_fingerprint), patch.object(
            EmberDB, "_reconcile_master_index", wrapped_reconcile):
        EmberDB.connect(path)
    counts["fingerprint_cpu_ms"] = round(counts["fingerprint_cpu_ms"], 3)
    return {"operation": "cold_open", "ms": round((time.perf_counter_ns() - start) / 1_000_000, 3),
            **counts}


def measure_fingerprint_cpu(db, repetitions=1000):
    """Amortize Windows process CPU-clock quantization over many pure metadata hashes."""
    start_cpu = time.process_time_ns()
    start_wall = time.perf_counter_ns()
    for _ in range(repetitions):
        db._master_index._candidate_fingerprint()
    return {"operation": "fingerprint_only_cpu", "repetitions": repetitions,
            "rows_per_call": len(db._master_index._records),
            "cpu_total_ms": round((time.process_time_ns() - start_cpu) / 1_000_000, 3),
            "wall_total_ms": round((time.perf_counter_ns() - start_wall) / 1_000_000, 3)}


def main():
    tiers = [int(n) for n in sys.argv[1:]] or [50, 150, 300]
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix=".test_phase5_scale_", dir=root,
                                     ignore_cleanup_errors=True) as directory:
        db = EmberDB.connect(Path(directory) / "store",
                             promotion_mode=PromotionMode.AUTOMATIC,
                             promotion_policy=PromotionPolicy())
        proto = MemoryProtocol(db, default_namespace="phase5",
                               search_config=SearchConfig(semantic_enabled=False))
        a = db.write(EmberRecord(namespace="phase5",
                                 data={"content": "phase5target claim A"}))
        b = db.write(EmberRecord(namespace="phase5",
                                 data={"content": "phase5other claim B"}))
        cid = db.map_conflict(a, b)
        db.update_conflict_status(cid, ConflictStatus.RESOLVED,
                                  resolution="A supported", winner_id=a)
        pid = db.propose(MemoryProposal(
            namespace="phase5", discovery={"claim": "promote A"},
            confidence=0.95, agent_id="agent-a", derivation=[a],
            evidence=[Evidence(source="tool://phase5",
                               source_type=SourceType.EXPERIMENTALLY_VERIFIED,
                               reference="fixed", agent_id="agent-a")]))
        count = 0
        for tier in tiers:
            while count < tier:
                db.write(EmberRecord(namespace="phase5",
                                     data={"content": f"noise-{count:06d}"}))
                count += 1
            rows = []
            rows.append(measure(db, "recall_lexical_top3",
                                lambda: proto.recall("phase5target", top_k=3,
                                                     format="raw")))
            rows.append(measure(db, "conflicts_for_one",
                                lambda: db.conflicts_for(a, include_closed=True)))
            rows.append(measure(db, "promotion_route",
                                lambda: db.promotion_route(pid)))
            start = time.perf_counter_ns()
            audit = run_integrity_audit(db, ["phase5"], "agent-a")
            rows.append({"operation": "independent_audit", "ms": round(
                (time.perf_counter_ns() - start) / 1_000_000, 3),
                "records_examined": audit["records_examined"],
                "status": audit["status"], "certified": audit["certified"],
                "findings": audit["finding_counts"]})
            rows.append(measure_fingerprint_cpu(db))
            print(json.dumps({"unrelated_memories": tier,
                              "physical_records": db._store.record_count(),
                              "measurements": rows}), flush=True)

        # These lifecycle events run once at the largest tier. The child is an
        # actual independent writer; only the parent handles are instrumented.
        path = Path(directory) / "store"
        cold = measure_cold_open(path)
        healthy = measure(db, "healthy_before_external_write",
                          lambda: db.conflicts_for(a, include_closed=True), 1)
        child = "from embers import EmberDB, EmberRecord; import sys; d=EmberDB.connect(sys.argv[1]); d.write(EmberRecord(namespace='phase5', data={'content':'external'}))"
        subprocess.run([sys.executable, "-B", "-c", child, str(path)],
                       check=True, capture_output=True, text=True)
        catch_up = measure(db, "first_lookup_after_external_write",
                           lambda: db.conflicts_for(a, include_closed=True), 1)
        steady = measure(db, "healthy_after_catch_up",
                         lambda: db.conflicts_for(a, include_closed=True), 1)
        explicit = measure(db, "explicit_reconciliation",
                           lambda: db._reconcile_master_index(), 1)
        print(json.dumps({"lifecycle_at_unrelated_memories": tiers[-1],
                          "cold_open": cold, "healthy_before": healthy,
                          "cross_process_catch_up": catch_up,
                          "healthy_after": steady,
                          "explicit_reconciliation": explicit}), flush=True)


if __name__ == "__main__":
    main()
