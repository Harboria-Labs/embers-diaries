# Independent integrity audit: extend maintenance

> **Reconciled 2026-10-15:** `EmberDB.audit_write` already provides read-only per-write provenance/history, and Promotion/Conflict paths already route admission or semantic review. Their roles are separate from the physical/index audit discussed below. See [existing-mechanism reconciliation](08_EXISTING_MECHANISM_RECONCILIATION.md).

## Existing pieces and missing behavior

Record sealing/hash verification happens on physical reads (`embers/core/record.py:210-231`, `embers/storage/store.py:260-275`). Native WAL recovery runs on store startup; checkpoint persists indexes/compacts WAL (`rust/ember_core/src/lib.rs:695-738`, `embers/db.py:1901-1909`). The opt-in maintenance scheduler calls reflect/consolidate (`embers/api/session_gate.py:61-94`, `embers/maintenance.py`). Observation-journal sync repairs the observer delivery mirror, not records or MasterIndex (`embers/integration/observation_journal.py`). None currently runs an independent strict namespace-wide audit or retains audit progress/failures.

## Minimal integration (unimplemented)

Add an explicit **read-only audit operation** to existing maintenance, callable on demand and optionally scheduled through its existing lifecycle. Enumerate physical record files independently of MasterIndex. Strictly decode each, check filename ID, namespace/type, seal/hash and relevant version/sidecar relationships, and compare its metadata with MasterIndex. Report four distinct outcomes: clean, corrupt/unreadable physical record, stale/missing derived index, and incomplete/interrupted audit. Rebuild derived indexes only in a separate recovery operation after an operator-visible failure; never rewrite or silently forgive a corrupt source record.

`PhysicalStore.read` currently prints and returns `None` on non-hash decode failures, so the audit needs a strict read/diagnostic mode or direct strict decoder; a missing file, decode failure, unsealed legacy record and hash mismatch must not be conflated. Legacy unsealed-record policy remains to be specified. Store audit cursor, source generation, start/end times, counts, failed IDs/reasons and last recovery outcome in durable **maintenance metadata** (a small status sidecar or existing status mechanism if implementation inspection proves one suitable). Publish `running` before scan and `complete` only after a full pass; interruption leaves visible `incomplete`. Logs alone are insufficient. A protected status interface may expose it; the current agent-facing `/maintenance` route is not automatically an authorization model for audit details (`embers/api/feedback_routes.py:76-87`).

Run bounded batches so ordinary operations remain responsive; track an audit generation and restart/reconcile if concurrent writes invalidate the snapshot. Recovery should use existing WAL recovery and index rebuild/checkpoint operations, then independently re-audit and certify. Avoid writing FUR/ELLA journals, LADC or Pairing state, memory usefulness, or observation source. Do not call reflect/consolidate as part of the audit itself; those are state-changing maintenance operations. No new scheduler or database is established as necessary.

## Invariants and alternatives

- Agent recall and conflict retrieval verify records they use; neither scans unrelated namespace records for audit purposes.
- Audit progress/failure survives process restart and is queryable by authorized operators. A stale index is repairable derived state; corrupt physical records remain explicit source failures.
- A completed audit attests only to its recorded source generation. Subsequent writes invalidate the relevant certification until reconciled. Avoid treating a periodic timer or log line as proof of health.
- Alternative of continuing incidental full scans is rejected by the frozen responsibility boundary. Alternative of a new standalone audit service is unnecessary on current evidence; existing maintenance lifecycle can own scheduling and status.

Open: exact operator authorization/status surface, legacy unsealed policy, cross-process generation protocol, and whether WAL recovery failures should prevent startup or mark health degraded. These must be frozen before implementation.
