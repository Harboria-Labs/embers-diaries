# Component map

| Owner | Interface and dependencies | Durable state / boundary |
|---|---|---|
| `embers/api/__init__.py`, `v1.py`, `research_routes.py`, `feedback_routes.py`, `observer_routes.py`, `usefulness_routes.py`, `http_parity.py`, `mcp_http.py` | FastAPI routes, session gate, token headers; call protocol, DB, ledgers and observer | HTTP transport; no independent memory authority |
| `embers/mcp/server.py`, `tools.py`, surface installers | JSON-RPC stdio and Streamable HTTP dispatch, schemas, authentication; call same DB/protocol and ledgers | Tool transport; module `install()` wrappers extend dispatch at import time |
| `embers/identity/registry.py`, `namespace/manager.py`, `api/session_gate.py` | Hashed agent token records, namespace read/write ACL, active-session checks | Identity and ACL metadata; public adapters authenticate before scoped operations |
| `embers/integration/memory_protocol.py`, `context.py`, `orientation.py`, `consolidated.py`, `pairing.py` | Ordinary remember/recall, rendering, orientation, research recall and Pairing; call DB and native domain ops | Memory writes and FUR activation events; context rendering is separate from truth authority |
| `embers/db.py` | SDK facade for write, update, query, history, conflicts, evidence, proposals, sessions, indexes | Coordinates engines and callbacks; conflict records and memory records are first-class immutable records |
| `embers/engine/writer.py`, `reader.py`, `wal.py`, `storage/store.py`, `storage/format.py` | CAS update, sidecars, visibility, WAL, physical read/write and codec | Immutable record files, WAL, supersession/deprecation/annotation/access sidecars |
| `embers/index/master.py`, `graph.py`, `fulltext.py`, `vector.py`, `timeline.py`, `query/engine.py` | Namespace/type/tag/lineage metadata; relation graph; postings, vectors, time queries | Persisted secondary views. Master is rebuilt on startup only if empty; graph uses native storage. These are not epistemic journals. |
| `embers/core/*` | Record/evidence/conflict/proposal/session/feedback models, hash and validation | Schema and canonical identity; `core/domain.py` calls native Rust and supplies bounded public truth summaries |
| `embers/cognitive/usefulness.py`, `epistemic.py` | FUR and ELLA namespace ledgers; invoke Rust reduction, admission inputs and projection | Distinct append-only RAW journals. FUR caches reduced state per ledger object; ELLA reconstructs per `load()`. |
| `rust/ember_core/src/lib.rs`, `domain/*`, `quota.rs` | PyO3 codec/hash/transaction/CAS/graph functions and `domain_call`; native LADC, FUR, Pairing, ELLA, context and orientation decisions | Correctness-critical transition validation; loaded extension compatibility is a separate runtime condition. |
| `embers/integration/observation_journal.py`, `observation_stream.py`, `research_observer.py`, `write_observation.py` | Committed observation mirror, SSE catch-up, scoped observer capability, write receipts | SQLite observation journal and capability files are projections/delivery state, not source memory or ELLA authority. |
| `embers/lobby/store.py`, `context.py`, MCP lobby adapter | Session-board posts, corroboration, promotion to proposal, close | Process-local ephemeral board; durable memory begins at explicit proposal/promotion. |
| `embers/api/*.html`, `*.js`, `*.css`, `observer_build.py` | Visualizer, research settings, observer and status UIs | Browser presentation; routes and event streams supply data. |

## Ownership rules observed in source

- Rust handles FUR/ELLA domain transitions, Pairing choice, activation/admission, graph operations, and native store transactions (`rust/ember_core/src/domain/mod.rs:59`, `rust/ember_core/src/lib.rs:641-738,1130-1147`). Python validates authenticated context and coordinates IO (`embers/cognitive/epistemic.py:92-102,272-297`).
- `MemoryProtocol.recall` is the ordinary query path (`embers/integration/memory_protocol.py:183-317`). `integration/consolidated.py:52-147` is the research path with explicit activation journal and token admission. Their separate semantics are intentional.
- `EmberDB.conflicts()` traverses graph edges; `conflicts_for()` reads mapped conflict records (`embers/db.py:760-787,903-959`). They answer different questions.
- `get_history()` returns an immutable supersession chain; ELLA assessments live in a separate namespace journal (`embers/engine/reader.py:81-96`, `embers/cognitive/epistemic.py:20-40`).

## Existing-mechanism reconciliation — 2026-10-15

`embers/engine/promotion.py` owns **admission policy**, with Automatic, Consensus, Human and Hybrid routing; it calls `db.promote` and does not assert epistemic truth. `embers/db.py:443-560` owns the read-only `audit_write` provenance/history composition. These are distinct from the physical record-integrity check in `storage/store.py`, from maintenance in `maintenance.py`, and from first-class semantic conflict review in `db.py:789-1000`. Promotion checks derivation graph `CONTRADICTS` edges (`promotion.py:302-321`); mapped conflict status is held in CONFLICT records. See `track7_atlas_design_20261015/08_EXISTING_MECHANISM_RECONCILIATION.md` for the current guarantees and limits.

## Planned ownership boundaries — not implemented

The seven-upgrade [contract](../../track7_atlas_design_20261015/09_COMPLETE_UPGRADE_ROADMAP.md) assigns MasterIndex candidate certification/recovery to the existing DB/index/storage boundary; conflict pair/status serialization to the existing Conflict Engine using shared lock/CAS; admission decisions to the existing Promotion Engine using current mapped status; independent physical/index diagnostics and scoped findings to maintenance; and disposable verified-state reuse to the ELLA ledger/service boundary. The native WAL, immutable history, reducer, observer source, FUR, Pairing and LADC retain their present owners. Policy decisions and proof obligations remain in `11_REUSE_AND_POLICY_DECISIONS.md` and the coverage register; none of these planned paths is currently a runtime feature.

**Batch 1 status correction, 2026-10-16:** The first two ownership assignments above now exist in local source: native/store code owns pre-change source epoch; DB/MasterIndex owns source certification and applied epoch; Conflict Engine owns candidate filtering, pair serialization, status CAS and idempotent graph-link repair. This is isolated-test evidence, not deployed behavior. Promotion, independent auditing and ELLA warm state remain planned.
