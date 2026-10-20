# Reproduction and boundaries

Candidate04 HEAD: `f183346547cd210f58ca0a6b23df91a56be22b66`; installed native module SHA-256 `452CC6D246C96311DDFE590F38947B6D386D39D4CF38F9650319E3833C6C19A5`. Python 3.13.5 on Windows 10 build 19045. The pre-existing working tree had `embers/api/research_dashboard.js` modified and other unrelated untracked material. This phase added only `track7_phase3_20261013/`.

Fresh disposable stores `store_run_20261013_01` through `_03` were created inside this directory. No active API store or live endpoint was written. Run 01 contains staged valid, malformed-reference and growing-record cases (215 records, about 396 KB of store files); run 02 contains missing-edge, authorization and tampered-unrelated-record negative controls; run 03 contains a deliberately altered persisted MasterIndex type after checkpoint and restart. These stores are retained and must not be reused as fresh fixtures. The benchmark checked free disk space and a 25 MB fixture budget; it started at 546,840,576 free bytes and ended at 542,822,400 after run 01.

Historical commands, run from repo root:

```powershell
diaries/Scripts/python.exe track7_phase3_20261013/bench_conflicts.py
diaries/Scripts/python.exe track7_phase3_20261013/counterexamples.py
diaries/Scripts/python.exe track7_phase3_20261013/restart_metadata.py
diaries/Scripts/python.exe track7_phase3_20261013/finalize_results.py
```

The first three commands refuse existing fixture paths. Use new distinct names for reproduction; do not delete or overwrite existing stores. `bench_conflicts.py` writes 20 staged cases, four approaches and three repetitions each. The reference is `db.conflicts_for`; candidates live only in that external script. Exact results include ordered `Conflict.to_dict()` values or exception class/message. `03_EQUIVALENCE_MATRIX.csv` combines 99 comparisons from main and counterexample runs. `04_PERFORMANCE_RESULTS.csv` has 240 raw measured operations from run 01, including physical read counts, bytes, decode/verification timing, graph timing, wall/CPU time and `tracemalloc` peak Python allocations. `tracemalloc` instruments the timed calls and can inflate wall time; use read/byte counts as the stronger comparison. Its peak excludes native allocations and whole-process RSS.

An optional `profile_reference.py` was prepared for an additional healthy 101-record cProfile run but **did not execute**: its 500 MB free-space preflight failed when drive-wide free space unexpectedly fell to about 103 MB. Phase 3 files totalled about 0.65 MB at that check, so the approximately 440 MB drop was not explained by these fixtures. Free space was around 89 MB during report assembly. No further benchmark or profiler run should be attempted until disk headroom is restored and the cause checked.
