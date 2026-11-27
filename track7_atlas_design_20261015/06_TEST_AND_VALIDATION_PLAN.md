# Required validation before implementation acceptance

These are proposed regressions, **not tests run on 2026-10-15**. Existing assertions give partial baselines: `tests/test_conflict_engine.py:52-207`, `tests/test_conflict_interfaces.py:246-398`, `tests/test_ella.py:118-159,294-321,399-494,761-821`, `tests/test_indexes.py:180-200`, `tests/test_verify_integrity.py`, `tests/test_native_wal.py`, `tests/test_native_store.py`. Review exact assertion bodies again when implementing.

| Area | Required new/expanded cases |
| --- | --- |
| Conflict equivalence | Open/closed/status histories, superseded heads, corrections, remapping, private namespace ACL, REST/MCP parity, graph/tag absence, restart. Assert that ordinary query reads only target and conflict candidate physical records on healthy certified index. |
| Index failure/recovery | Missing conflict metadata; wrong type/namespace for a physical conflict; stale nonempty snapshot; callback failure after durable commit; crash at each dirty/snapshot/clean boundary; two handles/processes; WAL recovery and sidecar mismatch. Must fail closed or rebuild explicitly, never return false zero. |
| Audit | Strict decode failure vs hash mismatch vs missing file vs unsealed legacy, stale index vs source corruption, interrupted/resumed progress, auth/status visibility, concurrent writes, index repair and re-audit. Assert no FUR/ELLA/LADC/Pairing/observer-source writes. |
| ELLA | Full replay versus warm path equality for every action including revise, withdrawal, evidence correction/invalidation, merge/split, carry-forward and policy change; exact response/revision; duplicate retry before CAS; stale revision; cross-handle new event; interrupted event commit, restart and journal tamper. Count historical reads/native reductions in a focused fixture to prove warm-path benefit without persisted stress benchmarks. |

Validation sequence after **separate user approval**: targeted unit/native tests in isolated temporary stores, crash-injection tests, then optional isolated persisted performance comparison. Live or production-store validation is a separate explicit gate. Do not use broad suites or persisted stress tests during this research task.
