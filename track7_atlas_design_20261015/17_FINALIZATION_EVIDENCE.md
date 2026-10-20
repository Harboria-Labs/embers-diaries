# Track 7 finalization — 2026-10-19

Source baseline: Candidate 04 `aec1a46ddfd76fe296c4a0190300654aa5608546`,
pulled before changes. Earlier four batches and Phase 5 remain implemented;
this task does not reopen their architecture. All execution used isolated stores
on this remote Linux environment. No private store, deployment, local user CLI,
real agent session or machine power-loss experiment was used.

## Verified implementation and exact authority boundaries

| Batch | Authoritative source and verification |
|---|---|
| 1: index/conflict | Python `embers/storage/store.py`, `embers/engine/writer.py`, `embers/index/master.py`, `embers/db.py`; Rust `rust/ember_core/src/lib.rs`. Pre-change source epoch, startup strict physical reconciliation, warm epoch checks, sealed type/namespace candidates, locked conflict mapping/status and CAS are present. Existing API namespace checks exercised. |
| 2: promotion | Python `embers/engine/promotion.py` and `embers/db.py`. Current mapped OPEN/INVESTIGATING outcomes hold; RESOLVED admits only winner through existing gates; ACCEPTED_BOTH/SUPERSEDED do not themselves hold. Locked admission/recheck, deterministic memory identity, retry and proposal CAS tested. HUMAN conflict overrides remain blocked. |
| 3: independent audit | Python `embers/maintenance.py`, API `embers/api/feedback_routes.py`, MCP `embers/mcp/feedback_surface.py`. Physical-source audit persists reports, detects source/index/lineage discrepancies and stale generations. Repair remains an explicit local-operator action for eligible derived-index findings, followed by re-audit. No automatic repair of corrupt authoritative records. Startup's existing uniquely inferable sidecar recovery is distinct from audit. |
| 4: ELLA reuse | Python `embers/cognitive/epistemic.py`; unchanged Rust `rust/ember_core/src/domain/ella.rs`. Store/namespace cache seeded by full replay, strict cached-head checks, incremental new-event reduction, epoch catch-up, audit invalidation, retry before CAS, and native verification semantics tested. |

The unchanged Phase 5 integration test runs write/search → mapped conflict hold →
resolution → promotion → physical audit → ELLA report/warm projection. Batch tests
exercise concurrent processes, interrupted commits, replay, explicit recovery,
authorization and retry. These are executed automated tests, not live-agent proof.

## Genuine defects and narrow changes

1. **Batch 2 compatibility defect:** `conflict_hold_reasons` allowed only NODE and
   DOCUMENT derivations, rejecting the existing `promote_failure` pipeline, which
   deliberately derives a lesson from a FAILURE record. Three existing failure
   promotion regressions failed. Permit FAILURE as a derivation source without
   adding it to durable-memory types. Evidence, namespace, graph-conflict and
   ordinary admission gates still run. Added five cases proving accepted source
   types, rejected RAW/PROPOSAL/EVIDENCE sources, graph hold and cross-namespace hold.
   Existing HUMAN/evidence/restart failure tests pass after the correction.
2. **Test isolation defect:** `test_thin_surfaces` changed EMBER_STORE but retained
   singleton DB/protocol/registry objects from preceding tests. Its write reached
   an old protocol store while feedback read another store (404). It passes alone.
   The test now binds a disposable DB and resets/restores both API caches through
   monkeypatch. No API behavior changed.

No FUR, ELLA, LADC, Pairing equation, authorization bypass or new admission policy
was introduced. Lobby milestone remains unchanged.

## Validation commands and outcomes

Python: `../lobby-venv/bin/python`, Python 3.12.14 on Linux x86_64.
Native loaded: `embers/_native.cpython-312-x86_64-linux-gnu.so`, source-epoch
protocol **1**, SHA-256
`88ee62b42f45355021f2ad881ce61231ac9be0bd24b6e23707c55eedeb1f6ee0`.
This is the remote test extension, not certification of the user's installed DLL.

```sh
python -m pytest tests/test_track7_batch1.py tests/test_track7_batch2.py tests/test_track7_batch3.py tests/test_track7_batch4.py tests/test_track7_phase5.py -q -ra
python -m pytest -q -ra
python -m pytest tests/test_failures.py tests/test_track7_batch2.py -q --tb=short
python track7_atlas_design_20261015/final_fingerprint_probe.py
```

- Baseline focused: **83 passed**, no skips, one dependency warning. Includes both
  native epoch assertions previously skipped on the older installed Windows binary.
- Baseline full discovery: **887 passed, 4 failed**, one dependency warning, 79.93s.
  Three failures were the promotion defect; one was API fixture isolation.
- After promotion correction: **47 passed**, one dependency warning in failure and
  Batch 2 tests, including all five new regression cases.
- Final full repository suite: **896 passed, 0 failed, 0 skipped**, one dependency
  warning in 76.86s. Raw output: `final_pytest_20261020.txt`.
- Native Rust unit suite: **16 passed, 0 failed, 0 ignored**, 68.97s.
  Raw output: `final_rust_20261020.txt`. Exact environment command below.

## CPU boundary measured, not redesigned

`final_fingerprint_probe.py` constructs only in-memory metadata in a disposable
index and measures seven calls after warmup. Raw samples:
`final_fingerprint_raw_20261019.jsonl`.

| Metadata rows | Median process CPU / fingerprint | Median elapsed / fingerprint |
|---|---:|---:|
| 1,000 | 0.628 ms | 0.628 ms |
| 10,000 | 9.007 ms | 9.012 ms |
| 100,000 | 111.738 ms | 111.745 ms |

This includes sorting, representation and hashing in `_candidate_fingerprint`.
Healthy conflict lookup still runs it per request. There is no constant total-CPU
claim: bounded physical reads and an in-memory full-index fingerprint coexist.
These are one-host synthetic metadata measurements, with other tests running;
not whole-query throughput, actual production data or capacity certification.
No arbitrary performance threshold was imposed and no integrity check removed.

## Disposition of outstanding validation

| Area | Classification and final boundary |
|---|---|
| Production scale | Operational validation open. Larger metadata CPU measured; full production workload, latency SLA and capacity not established. |
| Mixed versions | Deployment constraint open. Current native protocol tested; historical Python compatibility evidence retained. No old/new writer pair was executed. Writers that bypass epoch participation cannot be assumed safe. |
| Actual power loss | Environment unavailable. Existing killed-process/interruption recovery tests execute; they do not prove disk/controller/power-loss durability. |
| Live agents | Environment unavailable for a supported end-to-end agent harness. Isolated REST/MCP and process tests are not deployed live-agent validation. Lobby capture boundary remains closed as previously documented, not reclassified. |
| Historical journal edits | Documented architectural limitation: unchanged old bytes' epoch/head can escape warm checks until audit/full replay. Audit-triggered detection passes. No immediate historical-tamper guarantee or audit freshness policy introduced. |
| Full suite/native | Full-suite run and loaded protocol-1 native tests performed; final totals below. Remote Linux evidence does not prove Windows installed-extension rollout. |
| Fingerprint CPU | Known design cost measured through 100k metadata rows; no bounded total CPU contract. Retained fail-closed seal semantics. |

Finalization closes source-level implementation and isolated regression work only.
Production/operational certification remains explicitly separate. No architecture
replacement, deployment, main merge, new branch or human identity work occurred.

## Resumed verification — 2026-10-20

The interrupted run's temporary logs were unavailable on resume. Its unfinished
final totals were not assumed: both suites were rerun, retaining output beside
this report. Remote branch was re-fetched and remained at the baseline above.

Plain `cargo test --offline --manifest-path rust/ember_core/Cargo.toml` initially
failed to link the Python extension's test executable (undefined Python symbols).
A first explicit link attempt encountered a broken environment library symlink.
The completed retry uses the existing real shared library directly; Cargo source,
features and native domain code were not modified:

```sh
CARGO_HOME=/workspace/scratch/03d58ddd69af/toolchain/cargo \
RUSTUP_HOME=/workspace/scratch/03d58ddd69af/toolchain/rustup \
PATH=/workspace/scratch/03d58ddd69af/toolchain/cargo/bin:$PATH \
RUSTFLAGS='-C link-arg=/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/libpython3.12.so.1.0 -C link-arg=-Wl,-rpath,/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib' \
cargo test --offline --manifest-path rust/ember_core/Cargo.toml
```

These link errors are environment/test-build issues, not failed domain assertions.
