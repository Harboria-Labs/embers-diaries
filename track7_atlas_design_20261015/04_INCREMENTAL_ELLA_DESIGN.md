# Incremental ELLA state: verified in-process head first

> **Reconciled 2026-10-15:** A new durable journal-head/generation field is not proven necessary for an initial warm-cache integration. Existing `all_ids()`, shared store lock, sealed events, native reduction and replay can establish new-event sequence at the cost of filename enumeration. Historical-byte tamper detection remains an explicit contract limit. See [existing-mechanism reconciliation](08_EXISTING_MECHANISM_RECONCILIATION.md).

## Current path

`EpistemicLedger.load` enumerates all matching RAW event IDs, reads/verifies each, checks sequence and seal chain, seeds policy and calls native `ella_reduce` for every event (`embers/cognitive/epistemic.py:25-40`). `read` calls `project` without state, which calls `load`; `apply` calls `load` under the store writer lock, searches events for retry identity before revision CAS, computes action-specific next state, uses native `ella_state` and `ella_reduce`, then writes an event containing full state and response (`:49-113,270-302`). Corrections, withdrawal, merge/split and carry-forward alter the complete state; projecting from a verified current state already handles their retrospective effects. The event journal remains authoritative.

## Candidate approaches

| Approach | Correctness/durability | Cost/complexity | Decision |
| --- | --- | --- | --- |
| Per-process verified head cache keyed by store+namespace | Seed by full replay once; under existing writer lock ingest every new sealed event with native reducer and chain checks; discard on mismatch/restart. Journal is sole truth. | Normal warm operations avoid prior event reread/reduce; cold/recovery replay remains. Requires reliable head/change detection and retry map. | Smallest initial integration, conditional on generation protocol. |
| Last event's embedded `state` as cold start | Last record hash and `ella_state` validate its bytes/shape, **not** prior chain or reducer history. Needs independently verified replay certificate/checkpoint. | Faster cold start but durable trust metadata and recovery complexity. | Research alternative; not safe alone. |
| Durable current-head projection/index | Can support cold restart if tied to journal generation and replay audit. Must never become second authority. | More files, ordering and crash states. | Defer until warm-cache evidence shows need. |
| Full replay per operation | Existing correctness baseline and recovery/audit reference. | Repeated O(history) IO/reduction. | Keep for independent verification and fallback, remove from normal warm path only after proof. |

## Proposed warm path (unimplemented)

On first use of a namespace in a process, run existing `load` exactly and retain state, revision, last seal and a compact `(actor, request_id) -> fingerprint, response/event ID` map. The cache must be scoped to the shared store/namespace or DB service, **not merely one `EpistemicLedger` object**: REST constructs a fresh ledger on each request (`embers/api/research_routes.py:57-67`), and MCP/DB callers also construct ledgers (`embers/mcp/server.py:299`, `embers/db.py:119`). Under the shared store lock, check a trustworthy journal generation/head. Read, hash-check and native-reduce every event since the cached revision in sequence; check previous seal and embedded next state. A gap, missing/duplicate revision, changed known head, stale generation, or audit-reported journal corruption invalidates cache and fails closed or triggers explicit full replay. A successful `apply` publishes the cache only **after** the RAW event commit succeeds; an interrupted write is resolved from the journal/WAL on next use. `read` uses the same verified state and still verifies target, evidence and conflict records it uses. `apply` preserves duplicate-before-CAS behavior and native transition validation (`embers/cognitive/epistemic.py:96-113,275-302`). Cross-handle/process writes must be observed before serving cached state.

The existing `all_ids()` can discover events without replaying them but still scans the physical directory (`embers/storage/store.py:277-290`); this is a possible interim path, not a proven O(1) head lookup. Store metadata `record_count` alone does not prove journal completeness because it is separate from native record commit/recovery (`rust/ember_core/src/lib.rs:641-738`). A durable journal head/generation could be added through the same write/recovery protocol as the conflict index, or validated by a complete filename sequence on each warm call. The former is preferable for stable cost but its atomicity is an implementation-contract question.

## Invariants and failure behavior

- Accepted state equals a full replay through the same revision under native `ella_reduce`. Corrections and retrospective changes are applied by the existing reducer and full-state payload; no new memory-history mechanism or Python reducer is introduced.
- Retry identity returns the original response before stale-revision rejection. A cache may index identities, but cannot discard required older retries without a specified retention contract.
- Source event record hash, journal sequence/seal and new transitions are verified before cache advance. The cache is disposable; cold start, recovery, independent audit and explicit reconciliation replay the journal.
- Independently detected historical corruption invalidates current-state trust. Immediate detection of arbitrary out-of-band edits to *old* event files on every O(1) read is impossible without a trusted authenticated checkpoint/change signal or rereading history. This threat-model choice is an open readiness gate, not a license to silently trust stale state.
- Observation delivery remains recoverable after source commit (`embers/cognitive/epistemic.py:287-302`, `embers/integration/observation_journal.py`); projection must not mutate its source.

Native `ella.rs` reducer, evidence authority, revision, idempotency and response format require no redesign. The proposed change is Python journal/head reuse plus a shared durable generation/recovery contract. Tests and compatibility checks are in `06_TEST_AND_VALIDATION_PLAN.md`.
