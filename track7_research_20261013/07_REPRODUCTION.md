# Reproduction and environment

## Environment and boundaries

- Branch: `codex/ember-split-feedback-candidate04`; HEAD `f183346547cd210f58ca0a6b23df91a56be22b66`.
- Working tree before this research: see the exact `git_status_at_run.txt` snapshot. This research added only `track7_research_20261013/`.
- Native installed file: `embers/_native.cp313-win_amd64.pyd`, SHA-256 `452CC6D246C96311DDFE590F38947B6D386D39D4CF38F9650319E3833C6C19A5`.
- Python 3.13.5; Rust toolchain `rustc 1.90.0` and `cargo 1.90.0` (Rust source was inspected; no Rust code was built or changed).
- Windows 10 10.0.19045; CPU reported `Intel64 Family 6 Model 61 Stepping 4, GenuineIntel`.
- Free space before experiment: 344,399,872 bytes on C:, below the earlier Track 7 2 GB reserve. The script opens no EmberStore and makes no API writes.
- The live `http://127.0.0.1:9200/health` returned `{"status":"ok","version":"0.2.0"}` in a read-only check. The active service and tunnel were not restarted or reconfigured.

## Exact command

From repository root in PowerShell:

```powershell
diaries/Scripts/python.exe track7_research_20261013/bench_memory.py --counts 10 20 30 50 --reps 5
```

The fixture creates a synthetic ELLA report journal in a Python dictionary. Each event has one additional accepted assessment, full `state`, chain seal and a real `EmberRecord` content seal. It uses production `encode`, `decode`, `EmberRecord`, `EpistemicLedger.load`, native `ella_policy_default` and `ella_reduce`. It validates each synthetic transition with the native reducer before timing. The in-memory store mimics `EmberStore.read` decode and first integrity verification, but performs no disk IO, WAL, observation delivery or authenticated `EpistemicLedger.apply`. Response bodies are empty; actual responses can be larger.

Each count gets one first load, five warm repeated loads on the same object and five on new objects, plus five independent passes for enumeration, decode/first verify, explicit second verify, chain hashing and native replay boundary. Fixture creation is recorded separately. All runs are retained in `03_BENCHMARK_RESULTS.csv`. Wall and process CPU time are recorded. A single 50-event `cProfile` output and `tracemalloc` peak are recorded. No cold disk cache experiment is possible with this fixture; the first load is a first object load, not a cold filesystem load.

`summary.json` is the machine-readable summary. `profile_ella_50.txt` is the raw profiler output. No existing store, API configuration, Git ref, deployed module or native build was modified.
