"""Isolated metadata CPU probe; no physical store or production capacity claim."""
import json
import platform
import statistics
import tempfile
import time
from pathlib import Path
from embers.index.master import MasterIndex


def main():
    with tempfile.TemporaryDirectory(prefix="ember-fingerprint-") as directory:
        index = MasterIndex(Path(directory))
        for count in (1000, 10000, 100000):
            index.reset()
            for i in range(count):
                index.index_record(f"record-{i:09d}", f"namespace-{i % 10}",
                                   "DOCUMENT", "2026-10-19T00:00:00", [])
            index._candidate_fingerprint()
            samples = []
            for _ in range(7):
                cpu = time.process_time_ns()
                wall = time.perf_counter_ns()
                index._candidate_fingerprint()
                samples.append({"cpu_ms": (time.process_time_ns()-cpu)/1e6,
                                "wall_ms": (time.perf_counter_ns()-wall)/1e6})
            print(json.dumps({"rows": count, "samples": samples,
                              "median_cpu_ms": statistics.median(s["cpu_ms"] for s in samples),
                              "median_wall_ms": statistics.median(s["wall_ms"] for s in samples),
                              "python": platform.python_version(), "platform": platform.platform()}))

if __name__ == "__main__":
    main()
