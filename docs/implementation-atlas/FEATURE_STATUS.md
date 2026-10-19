# Capability status

“Tested” here means a targeted test exists and its asserted behavior is inspectable; no tests were executed during this audit. Loaded native extension and live service behavior remain unverified.

| Capability | Classification | Basis / limit |
|---|---|---|
| Immutable record write, sealed read, CAS versions, supersession history | Fully implemented and targeted tests present | `embers/engine/writer.py`, `reader.py`; `tests/test_native_store.py`, `test_native_versioning.py`, `test_versioning_graph.py` |
| Agent registration, token auth, namespace ACL and v1 history access | Fully implemented and targeted tests present | `embers/identity/registry.py`, `api/v1.py`, `mcp/server.py`; `tests/test_agent_identity.py`, `test_namespace_access.py`, `test_v1_memory_read_api.py` |
| Text/vector/graph/timeline/master indexes and checkpoint | Implemented with known limitations | `embers/db.py:175-260,1901-1909`; `tests/test_indexes.py`; nonempty stale snapshots are not comprehensively rebuilt |
| Ordinary recall and context formatting | Implemented with known limitations | `memory_protocol.py:183-317`; room selection and optional ELLA rendering have distinct limits; targeted `tests/test_integration.py`, `test_primary_context.py`, `test_ella.py` |
| Native LADC/contextual activation and bounded research admission | Implemented and targeted tests present; loaded binary unknown | `consolidated.py`, `dynamics.rs`; `tests/test_consolidated.py`; runtime binary freshness not checked |
| Pairing V1 one-hop selection, no substitution, contextual W | Implemented and targeted tests present; loaded binary unknown | `pairing.py`, `pairing.rs`; `tests/test_pairing_v1.py` |
| FUR evidence-derived usefulness journal | Implemented with known scaling limits | `usefulness.py`, `fur.rs`; `tests/test_consolidated.py`, `test_feedback_replay.py`; fresh-object replay and event-ID enumeration remain |
| ELLA assessment, correction, independence, confirmation and conflict overlay | Implemented with known scaling and epistemic limits | `epistemic.py`, `ella.rs`; `tests/test_ella.py`; full replay per load, hidden common source and assessor-independence limitations |
| Mapped semantic conflict records/status and graph relation | Implemented with known lookup limitation | `db.py:789-998`; `tests/test_conflict_engine.py`, `test_conflict_interfaces.py`; namespace-wide record reads |
| Observer journal, capability, cursor/SSE and browser surfaces | Implemented with known delivery/runtime limits | `observation_journal.py`, `research_observer.py`, `observer_routes.py`; `tests/test_research_observer.py`, `test_observation_stream.py`; no live browser verification here |
| Session lobby and explicit proposal promotion | Implemented with known process-local boundary | `lobby/store.py`, MCP/REST adapters; `tests/test_lobby.py`, `test_lobby_rest_mcp_parity.py` |
| Native WAL recovery, per-record integrity, index checkpoint, maintenance loop | Implemented and targeted tests present | `storage/store.py`, `core/record.py`, `maintenance.py`; `tests/test_native_wal.py`, `test_verify_integrity.py`; these do not certify every secondary index |
| Reusable, independently validated ELLA projection/checkpoint | Designed/recommended but not implemented | No checkpoint read/write in `EpistemicLedger.load`; full replay remains |
| Narrow conflict lookup with exact old integrity/error semantics | Not implemented; proposed integration | Existing lookup is correct reference path; Phase 3 prefilters have counterexamples |
| Dedicated public paginated ELLA event audit-history surface | Unknown requirement / not found | ELLA source journal exists; state endpoint exposes projection, not event pages |
| Current loaded native extension, live service and browser behavior | Unknown pending verification | No runtime probe in this audit; the 2026-10-11 benchmark reported an older extension lacking ELLA, while the later isolated persisted Phase 2 run used a native build with ELLA; current loaded identity was not checked |
| Production-scale persisted ELLA behavior | Unknown pending verification | An isolated 50-report persisted fixture was measured in Phase 2, but no production capacity limit follows from it |

## Existing-mechanism status correction — 2026-10-15

- **Implemented with targeted tests:** native WAL record recovery and atomic publication; native supersession sidecars; sealed immutable memory versions, branching/linear history and memory CAS; `audit_write` per-write provenance trace; Promotion Engine Automatic/Consensus/Human/Hybrid routing; first-class Conflict Engine mapping/status/review. See `track7_atlas_design_20261015/08_EXISTING_MECHANISM_RECONCILIATION.md` and `tests/{test_native_wal,test_native_store,test_native_versioning,test_versioning_graph,test_concurrency,test_write_audit,test_promotion_engine,test_conflict_interfaces}.py`. Tests were inspected, not executed in this reconciliation.
- **Existing feature needing integration:** nonempty stale MasterIndex reconciliation before narrow conflict candidate use; optional conflict map/status serialization using existing lock/CAS if concurrent tests establish the gap; ELLA cross-operation state reuse using existing journal/native reducer/lock. A new durable ELLA head or conflict index is not established as necessary.
- **Missing operation, if the frozen independent-audit requirement remains:** strict namespace-wide physical/index audit with observable completion/failure status. `audit_write` and promotion/conflict review do not perform it. Immediate detection of arbitrary old-file tampering during accelerated reads remains an unresolved contract, not a solved capability.

## Seven-upgrade planning status — 2026-10-15

All changes in `track7_atlas_design_20261015/09_COMPLETE_UPGRADE_ROADMAP.md` remain **planned, unimplemented and untested in this planning task**. Existing WAL/immutable history/CAS, Conflict and Promotion Engines, ELLA native reducer, per-record integrity and maintenance lifecycle are reuse targets. MasterIndex trust certification, narrow conflict candidate retrieval, conflict-only concurrency hardening if tests expose races, current-status promotion integration, independent audit/reporting, and cross-operation ELLA cache are modifications or new operation surfaces as classified in `11_REUSE_AND_POLICY_DECISIONS.md`. No status in the original table above should be read as certifying these future behaviors.

## Batch 1 source status — 2026-10-16

**Implemented in the local candidate source, isolated-test validated:** conservative source/applied epoch protocol for MasterIndex; strict startup and behind-epoch reconciliation; CONFLICT-only candidate retrieval through existing MasterIndex; conflict pair/status serialization under shared lock with status CAS; graph-link repair on idempotent map retry. Existing native WAL, immutable memory branching/history, REST/MCP adapters and ACLs remain. Evidence: `track7_atlas_design_20261015/12_BATCH1_IMPLEMENTATION_EVIDENCE.md`. The installed production native module and live service were not updated, so production/live status is **unverified**. Native direct callers require the shared lock; mixed old/new writer processes cannot share this new epoch guarantee. Batches 2–4 remain unimplemented.

## Batch 2 source status — 2026-10-17

Implemented in local candidate source and isolated-test validated: current mapped-conflict status/outcome gate for Automatic/Consensus/Hybrid; Human HOLD with conflict reasons; graph-only investigation hold; fail-closed relevant derivation/CONFLICT reads; locked route and final promotion recheck; proposal terminal CAS; retry identity for promoted NODE; REST/MCP actor parity. Existing evidence/trust/confidence gates and ELLA truth boundary remain. Partial limit: public explicit HUMAN promotion retains legacy authenticated-writer behavior for non-conflict proposals; no verified human role, recorded override reason/version or Control Center exists, so conflict-related overrides are blocked. Independent-process race and real power-loss behavior remain unverified. See track7_atlas_design_20261015/13_BATCH2_IMPLEMENTATION_EVIDENCE.md. Batches 3-4 remain unimplemented; no live validation. Earlier planning rows are historical snapshots.

Batch 2 validation supplement: a spawned-process losing-resolution versus submit case now passes, and the full Batch 2 test file passes 23 tests. This is partial process coverage; other interleavings and real power loss remain unverified. Focused FUR/LADC boundary tests passed 29. See the appended evidence in 13_BATCH2_IMPLEMENTATION_EVIDENCE.md.
## Batch 3 source status — 2026-10-17

**Implemented in local candidate source and isolated-test validated:** independent namespace-scoped physical audit, durable bounded findings/status, source-generation staleness, MasterIndex comparison, protected REST/MCP report/run surfaces, and a separate local-operator-only index reconciliation followed by re-audit. Existing read-time integrity, `audit_write`, WAL recovery and ordinary conflict lookup retain separate roles. Scan completion with findings is not certification. A store with corrupt source may fail during DB startup reconciliation, making the stored report unavailable through the DB-backed API until local diagnosis. Automatic scheduler integration and agent-exposed repair are not implemented because a verified administrative authorization contract is absent. Batch 4 and live validation remain unimplemented. Evidence: `track7_atlas_design_20261015/14_BATCH3_IMPLEMENTATION_EVIDENCE.md`.
## Batch 4 source status — 2026-10-17

**Implemented in local candidate source and isolated-test validated:** bounded shared ELLA projection, cold authoritative replay, warm head verification, source-epoch new-event catch-up through native reduction, exact retry response, revision CAS, audit-triggered invalidation and explicit cache clearing. No ELLA event schema, native math, memory history, FUR, Pairing, LADC or Promotion Engine change. Full replay still occurs on first access, explicit `load()`, audit boundary changes, invalid continuity and restart. Historical out-of-band old-file edits with unchanged epoch/head require independent audit or replay; immediate per-request detection is not claimed. See `15_BATCH4_IMPLEMENTATION_EVIDENCE.md`. Live-agent and production status remain unverified.

## Track 7 current milestone status — 2026-10-17

**Local implementation and isolated validation complete** for the four approved implementation batches and Phase 5 integrated checkpoint on `codex/ember-split-feedback-candidate04` through `fe0a001`. The batch and Phase 5 evidence files remain the detailed record; earlier planning and per-batch pending statements above are historical snapshots. This status does not certify deployment, production behavior or a general capacity bound. Live-agent validation, production-scale performance, mixed-version compatibility and real power-loss durability remain **unverified**. Historical ELLA tamper detection between audits and the documented audit/administrative limits also remain as stated in the batch evidence. No Phase 6 work is included in this milestone.

## Lobby V2 design status — 2026-10-18

The ten Lobby V2 principles and five architectural decisions are **approved**. Implementation, focused tests, harness integration and live validation are tracked by batch. The existing Lobby baseline is a process-local request/response board with explicit promotion. Source reconciliation and requirements are in `paper/lobby_v2_architecture.md` and `paper/lobby_v2_gap_matrix.md`. The Research Observatory remains read only and separate. This status does not reopen Track 7 or start Phase 6.

### Lobby V2 Batch 1 — isolated source milestone

Shared-task scope/membership, bounded restart-resilient provisional items, idempotent submission, author correction/withdrawal, expiry/close, contextual query and separately authorized evidence-reference retrieval are implemented in `embers/lobby/working.py` and `embers/api/lobby_v2.py`. Focused isolated command: **25 passed**; evidence and limits in `paper/lobby_v2_batch1_evidence.md`. Server-pushed delivery, retry-safe durable promotion, attributed peer contributions and automatic harness capture remain pending. No production or live validation is claimed.

### Lobby V2 Batch 2 — local single-process delivery

Lobby-specific SSE, post-commit wakeups, bounded ordered event replay, reconnect cursors, reset, per-delivery authorization and revocation behavior are implemented and locally validated: **28 focused tests passed**, including prior Batch 1/V1 checks. See `paper/lobby_v2_batch2_evidence.md`. Cross-worker live push is not implemented; another worker's commit is visible after reconnect only. Multi-worker delivery, live service and external clients remain unverified. Batch 3–4 remain pending.

### Lobby V2 Batch 3 — explicit durable handoff

Peer support/contradiction remains attributed; proposal/failure handoff reuses existing durable mechanisms with deterministic retry IDs, retained local receipts and claim uncertainty. **31 focused isolated tests passed**, including a lost-acknowledgment retry. See `paper/lobby_v2_batch3_evidence.md`. Handoff is one accepted durable ID per provisional item; later corrections need a new submission for a new handoff. External artifact authorization/reachability, full-suite behavior, cross-worker wake and live service remain unverified. Batch 4 harness integration is pending.

### Lobby V2 Batch 4 — adapter boundary implemented, real hook gate open

REST/MCP share the working-memory operations; an opt-in Codex Stop adapter accepts an agent-selected finding and bounded redacted artifact. An isolated simulated Stop payload drove upload, submission, SSE change and authorized peer retrieval. The focused Batch 4 plus prior-boundary group passed **39 tests, 1 dependency warning**. The actual Codex runtime hook was not installed or exercised; automatic real-agent capture and second live-agent delivery remain **unverified**. Batch 3 handoff was hardened with a committed intent and retained artifact before durable write, then a durable receipt; isolated close/expiry retry evidence is in the Batch 4 focused tests. See `paper/lobby_v2_batch4_evidence.md`. Cross-worker wake and live deployment remain open.

### Lobby V2 operational follow-up and Live Lobby Observatory — 2026-10-19

The test branch now includes an OS file-change wake after committed Lobby events. A separate-process reader received a writer's change without polling, including a WAL-only variant with the separate signal disabled; an HTTP SSE client received a committed change and recovered a missed change with `Last-Event-ID`. The API dependency is pinned to `watchfiles==1.3.0`, whose native notifier is used for the wake; the SQLite event journal remains the ordering and reset source. The final combined focused group passed **62 tests, 1 dependency warning**. These are **isolated local tests**, not deployment or production-scale proof. A notifier failure produces a reset/error rather than silent cross-worker delay.

The existing Research Observatory now offers a delegated **read-only Live Lobby view**. The issuing agent must explicitly grant one shared scope while holding an active, matching Lobby session; current membership, task, namespace ACL and observer capability are rechecked. The projection uses read-only SQLite, exposes no artifact body or evidence reference, and reports scope members, provisional status, uncertainty, evidence count, correction version, timestamps and durable handoff. Client-side filters and SSE cursor/reconnect/reset states are present. Isolated tests checked authorization, revocation and unchanged Lobby file digest after viewing. Browser visual behavior and deployed SSE remain unverified.

Two real isolated Codex CLI turns, each with its own authenticated agent/session, completed the actual `Stop` hook and contributed provisional memories. The first attached a harness-observed redacted test output; the second's harness retrieved the first finding, and the first's harness retrieved the second. A connected HTTP SSE client received the second change. The final real-runtime probe returned all gates true. These are two instances of one supported Codex harness, with harness-mediated retrieval; they do not prove Claude or hosted ChatGPT hooks, background agent wakeups, or live deployment. The first attempt to make Codex execute the probe itself was rejected by the current automatic approval policy, so the integrated harness executed it and supplied the observed evidence. See `paper/lobby_v2_operational_evidence.md` for exact boundaries. Track 7 remains at its closed local milestone.


### Live Lobby Observatory completion follow-up — 2026-10-19

Inspected Candidate 04 at `dcd898e`; changes remain on that branch. Existing storage/SSE/adapter/handoff mechanisms retained. The panel now reports safe evidence/contributor metadata and explicit uncertainty/handoff limits; fixes cover atomic read-only row/cursor snapshots, Last-Event-ID, serialized event-triggered refresh/recovery, local expiry, scope-close notifications and MCP Lobby delegation. **45 focused Python tests passed**, including seven client state-machine cases; standalone Node run also passed seven. Real isolated HTTP SSE/replay and cross-process wakeup ran. Browser access to the isolated server was refused, so visual/deployed validation remains open. No Track 7 validation or deployed changes. Full source, limits and operator startup instructions: [Live Lobby Observatory](../live-lobby-observatory.md).


### Final focused harness-capture validation — 2026-10-19

Inspected/pulled Candidate 04 at `b3159d052f3354d250f5bfdf7c48b0bc90a265d0`.
**Automatic tool-result capture remains unproven**: current Stop adapter uploads a
selected preexisting outbox file; this remote environment has no supported
completed-tool hook integration or Codex CLI on PATH. Historical real CLI turns
used harness-prepared evidence. Simulated adapter plus isolated REST/MCP/SSE,
authorization, retry, read-only and client tests: **42 passed, 0 failed, 0 skipped**,
one dependency warning. New assertions explicitly distinguish synthetic fixtures
from execution receipts. Runtime source unchanged; no deployment/browser proof,
no Track 7 work. Full source boundary, command and evidence grades:
[final capture validation](../../paper/lobby_v2_final_capture_validation.md).
