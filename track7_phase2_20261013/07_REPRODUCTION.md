# Reproduction, environment and limits

Repository: `codex/ember-split-feedback-candidate04`, HEAD `f183346547cd210f58ca0a6b23df91a56be22b66`. Installed `_native.cp313-win_amd64.pyd` SHA-256: `452CC6D246C96311DDFE590F38947B6D386D39D4CF38F9650319E3833C6C19A5`. Python 3.13.5, Windows 10 build 19045, Intel64 Family 6 Model 61 CPU. Existing working tree already had a dashboard modification and unrelated untracked artifacts; this phase added only `track7_phase2_20261013/`.

The first completed store was `track7_phase2_20261013/store_run_20261013_01` (30 reports plus correction). A separate attempted `store_run_20261013_02` failed at report 1 and was retained. The successful 50-report retry was `store_run_20261013_03`. All are distinct from the live API store. The benchmark refuses to reuse an existing store or CSV. Before starting it requires at least 320 MB free; after every measured operation it stops below 300 MB free or above 20 MB of experiment files. Run 01 began with 345,735,168 free bytes and ended with 342,548,480. Run 03 began with 340,074,496 and ended with 333,926,400. These are drive-wide measurements, so changes outside the fixture can affect them.

Exact commands from repo root:

```powershell
diaries/Scripts/python.exe track7_phase2_20261013/bench_real_ella.py --up-to 30
diaries/Scripts/python.exe track7_phase2_20261013/bench_real_ella.py --up-to 50 --run-id 02
diaries/Scripts/python.exe track7_phase2_20261013/bench_real_ella.py --up-to 50 --run-id 03
diaries/Scripts/python.exe track7_phase2_20261013/profile_projection.py
diaries/Scripts/python.exe track7_phase2_20261013/analyze_results.py
```

These exact commands are historical records: **do not rerun against the named stores**. For another run, first choose a new clearly named store and CSV in the research script; do not overwrite or delete the existing stores. `--run-id 02` is the failed attempt; `--run-id 03` is the successful 50-event data used for the main table.

Fixture: one registered, token-authenticated agent; one private namespace and DOCUMENT claim; five distinct evidence records, each with a separate source/reference; 50 unique report assessment/request identities cycling over those records; one admin-authorized lineage correction. This deliberately limits raw evidence diversity, so accepted-unit/trust behavior is not a general independence benchmark. The test measures `EpistemicLedger.apply` directly, rather than HTTP, to time internal stages without writing to the live API. Optional `write_observation.install` was not called; direct ELLA observation-journal append was exercised.

Each checkpoint has three same-object loads, three new-object loads and three full projections. Every append is recorded individually; no slow runs were removed. The instrumented timings for load, read, verify, native call, physical write and observation append are inclusive/nested. Process RSS is blank because `psutil` was unavailable; wall time and process CPU time are retained. A later read-side script profiled 31- and 51-event stores without writing new ELLA events.

`summary.json` aggregates both completed runs and includes the failed row. Raw completed 30-event data is `03_RAW_RESULTS.csv`; completed 50-event data is `03_RAW_RESULTS_50_RETRY.csv`; failed-attempt data is `03_RAW_RESULTS_50.csv`. `projection_profile.csv` and `profile_project_51.txt` contain the read-side follow-up.
