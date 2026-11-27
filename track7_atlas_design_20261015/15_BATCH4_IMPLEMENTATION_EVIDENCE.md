# Batch 4 local implementation evidence — 2026-10-17

Branch `codex/ember-split-feedback-candidate04`; pre-commit HEAD `9996611d12ec101bcd50a0518fb4f9a8ba42037d`. The isolated interpreter loaded unchanged `embers/_native.cp313-win_amd64.pyd`, SHA-256 `452cc6d246c96311ddfe590f38947b6d386d39d4cf38f9650319e3833c6c19a5`. No Rust, installed native binary, live store, service or observer source was changed.

## Verified projection

`EpistemicLedger.load()` remains full authoritative replay. Normal `read`, `project` and `apply` use a disposable process cache keyed by canonical store path and namespace, further bound to the physical store directory identity, source epoch, journal revision, sealed head record hash/event seal and Batch 3 audit marker. The cache holds current native state and compact request identities, **not historical event payloads or responses**. It is limited to 16 store/namespace entries with LRU eviction. Cold access replays the entire sealed RAW journal through the unchanged Rust `ella_reduce`; warm access verifies the cached head. A changed source epoch causes sequence enumeration and strict native reduction of only new events. A mismatch discards the cache and replays or raises. The cache advances after the immutable event write returns; interrupted post-commit projection work is rediscovered from the journal. Retries read and verify their committed event before returning its saved response, ahead of revision CAS.

Batch 3 `active.json` now retains `last_run_id`; every audit start/completion changes this small existing marker. A changed marker forces ELLA full replay, including when the audit ran in another process. While an audit is running, ELLA replays rather than reusing the cache. Explicit `clear_verified_state(store_path, namespace)` supports local diagnostic invalidation. Audit findings never change ELLA truth or repair journal records.

## Isolated correctness evidence

All commands used `./diaries/Scripts/python.exe -B`, `-q -x -p no:cacheprovider`, and unique repository-local `--basetemp` stores. Before editing, `pytest tests/test_ella.py tests/test_research_observer.py` passed **60**. After implementation:

| Focused files | Result |
| --- | --- |
| `tests/test_track7_batch4.py` | Final rerun: 14 passed; includes cold/warm, REST ledger reconstruction, second handle/process catch-up, retry/CAS, revision and process race, correction/withdrawal/merge equivalence, audit invalidation, missing/corrupt old event, immediate cached-head corruption rejection, cache isolation/eviction, process death after commit |
| `tests/test_ella.py tests/test_track7_batch3.py tests/test_research_observer.py` | 84 passed |
| `tests/test_track7_batch1.py tests/test_track7_batch2.py tests/test_track7_batch3.py tests/test_conflict_interfaces.py tests/test_indexes.py tests/test_api_security.py tests/test_mcp_server.py` | 137 passed, 2 staged-native-only skips |
| `tests/test_consolidated.py tests/test_feedback_replay.py tests/test_pairing_v1.py tests/test_v1_memory_read_api.py tests/test_promotion_engine.py tests/test_research_observer.py tests/test_track7_batch4.py` | 121 passed before the final additional cached-head case; that case passed in the subsequent 14-test Batch 4 rerun |

The process-kill case exits after journal commit and before cache publication, then reopens and returns the original saved response. The independent-process writer race accepts exactly one revision-zero report. These are isolated process/interruption tests, not power-loss proof. Existing full-state and native transition tests remain the oracle; no ELLA classification equation changed.

Executed commands from the repository root (intermediate fault-discovery runs preceded these final passes):

```text
.\diaries\Scripts\python.exe -B -m pytest tests/test_ella.py tests/test_research_observer.py -q -x -p no:cacheprovider --basetemp .test_batch4_baseline_20261017
.\diaries\Scripts\python.exe -B -m pytest tests/test_ella.py tests/test_track7_batch3.py tests/test_research_observer.py -q -x -p no:cacheprovider --basetemp .test_batch4_regression_a_20261017
.\diaries\Scripts\python.exe -B -m pytest tests/test_track7_batch1.py tests/test_track7_batch2.py tests/test_track7_batch3.py tests/test_conflict_interfaces.py tests/test_indexes.py tests/test_api_security.py tests/test_mcp_server.py -q -x -p no:cacheprovider --basetemp .test_batch4_regression_b_20261017
.\diaries\Scripts\python.exe -B -m pytest tests/test_consolidated.py tests/test_feedback_replay.py tests/test_pairing_v1.py tests/test_v1_memory_read_api.py tests/test_promotion_engine.py tests/test_research_observer.py tests/test_track7_batch4.py -q -x -p no:cacheprovider --basetemp .test_batch4_regression_c_20261017
.\diaries\Scripts\python.exe -B -m pytest tests/test_track7_batch4.py -q -x -p no:cacheprovider --basetemp .test_batch4_final3_20261017
.\diaries\Scripts\python.exe -B track7_atlas_design_20261015/bench_batch4.py
```

## Isolated performance

`bench_batch4.py` uses a disposable store and instruments ELLA journal physical reads plus native `ella_reduce` calls. The final successful single-run timings below include target/evidence/conflict projection and platform noise; they are not production capacity estimates. At 5/10/20/40 events, cold read used 6/11/21/41 journal reads and 5/10/20/40 reductions; warm read used **1 journal read and 0 reductions** at every size. Warm append used **2 journal reads and 1 reduction**; cold append at the adjacent next revision used 8/13/23/43 reads and 7/12/22/42 reductions. Warm read latency was approximately 40/71/108/217 ms versus cold 59/95/304/501 ms. Warm append was approximately 473/641/787/1397 ms versus adjacent cold append 523/632/935/1479 ms. Cached native state occupied approximately 4.3/7.4/13.5/25.7 KB after the append pair. The 10-event warm append was slightly slower than adjacent cold append, so latency improvement is not claimed at every point.

After an independent process appended one event to the 42-event journal, the parent caught up with **one native reduction** in about **519 ms**, but observed **45 ELLA physical reads**: Batch 1's behind-handle MasterIndex reconciliation performed a full source-backed index check before ELLA projection. This is a distinct cross-process recovery cost, not an ELLA replay. A clean independent Batch 3 audit took about **346 ms**; the next ELLA read replayed 43 events in about **570 ms** because the audit marker changed. The earlier exploratory run emitted all measurements but Windows briefly held its disposable observer SQLite file during automatic cleanup; that fixture was subsequently deleted. The final run exited successfully. Repeated medians, larger scale and live-agent latency remain unverified.

## Limits and rollback

Healthy warm operations avoid historical replay but still project current evidence and serialize full post-event state, so total latency can grow with current epistemic state. Supported cross-process writes are detected by Batch 1's source epoch. Arbitrary out-of-band changes to an **old** journal file with an unchanged epoch/head cannot be detected on every warm request without rescanning history; Batch 3 audit or explicit full replay invalidates the cache. Missing/corrupt known head is detected immediately. Legacy ELLA journal events without a content seal now fail strict replay. Cache metrics are local process diagnostics, not durable attestations. Batch 3's DB-backed report availability limit on corrupt startup source remains. Real power loss, mixed-version writers, production-scale performance and live validation remain open.

Rollback requires reverting only the Batch 4 Python source changes or calling the existing full `load` path for normal operations. The immutable ELLA journal schema and all committed events are unchanged; no migration or data rollback is needed. Keep Batch 1–3 and their evidence intact.
