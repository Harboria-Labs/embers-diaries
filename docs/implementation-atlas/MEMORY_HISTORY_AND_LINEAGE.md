# Memory history and lineage

## Record identity and write history

`EmberRecord` carries ID, version, content hash, provenance, `supersedes`, `derived_from`, causal links and annotations; `seal()`/`verify_integrity()` use canonical hashing (`embers/core/record.py:20-107,144-231`). `WriteEngine.update` creates a new record under CAS, preserves the old immutable record and records supersession in sidecars (`embers/engine/writer.py:98-273`; native CAS in `rust/ember_core/src/lib.rs:1016-1069`). `ReadEngine.get` overlays deprecation, supersession, annotation and access sidecars; defaults hide deprecated/superseded records (`embers/engine/reader.py:25-67`).

`ReadEngine.get_history(id)` follows the writer's supersession chain and returns oldest-to-newest records, including deprecated/superseded versions (`embers/engine/reader.py:81-96`). `get_current` follows to the head and `get_at` chooses the last record created by a timestamp (`:69-113`). MasterIndex also stores a *branching* version graph, with parent/children, ancestors, descendants, heads and branch points (`embers/index/master.py:222-277`; `embers/db.py:679-721`). This graph can represent relationships beyond the linear supersession-chain reader. Causal `derived_from`, `caused_by`, `led_to`, and `CONTRADICTS` edges are separate graph relations (`embers/db.py:723-787,1807-1836`).

## Agent-accessible history

The SDK exposes `EmberDB.get_history` (`embers/db.py:392-394`); MCP exposes `ember_get_history` with authentication and namespace read checks (`embers/mcp/server.py:374-394`, schema `embers/mcp/tools.py:136-145`); REST exposes `/v1/memory/history/{record_id}` with the same scoped read boundary (`embers/api/v1.py:313-333`). Legacy `/records/{record_id}/history` exists in `embers/api/__init__.py:219-222`, subject to the legacy route gate there. Tests cover the v1 route order/depth and authentication (`tests/test_v1_memory_read_api.py:159-204`) and version-chain behavior (`tests/test_versioning_graph.py:48-153`).

History is a list of *memory record versions*. It does not include every evidence attachment, ELLA assessment, FUR event, observer observation or conflict status unless that item is itself queried through its own record/journal interface. `audit_write`, provenance, evidence and session APIs provide other lineage views (`embers/db.py:436-658,1145-1179,1633-1729`).

## ELLA-specific history

`EpistemicLedger` identifies a namespace journal with a deterministic `epistemic-...` prefix and sequence IDs. `load()` checks every matching RAW record, its immutable integrity seal and hash chain, then calls native `ella_reduce` for every event (`embers/cognitive/epistemic.py:20-40`). An event embeds action, payload, full next `state`, response, revision, actor and request ID (`:275-286`). Assessments target an exact memory ID plus content hash, and reference attached or explicitly carried evidence records (`:42-72,109-113,243-254`). Corrections, withdrawals, merge/split and carry-forward preserve this audit chain (`:165-271`).

The public ELLA endpoints return current projection (`embers/api/research_routes.py:53-65`, `embers/mcp/server.py:296-302`). The source journal is agent-readable indirectly only insofar as a caller can inspect raw records through authorized generic record APIs; no dedicated paginated ELLA event-history tool is established by the inspected public schemas. This is a surface gap only if that history is an explicit product requirement; it is not evidence that ELLA lacks a journal.

## Conflict and evidence lineage

Semantic conflicts are `RecordType.CONFLICT` records with their own supersession history and status transition validation (`embers/db.py:789-998`, `embers/core/conflict.py:47-135`). A `CONTRADICTS` graph edge is added for navigation, but lacks conflict ID/status/resolution. Evidence is stored as record-linked evidence with content identity (`embers/db.py:1633-1729`, `embers/core/evidence.py:36-171`); ELLA lineage overrides are journal events, not edits to evidence records.

## Write-audit distinction — 2026-10-15

`audit_write(record_id)` is already a read-only composed trace of one durable write, including exact hashes, branch/version context, causal graph, proposal and evidence provenance (`embers/db.py:443-560`; `tests/test_write_audit.py:42-194`). It is **not** a namespace-wide physical integrity scan. Native WAL, sealed record verification and supersession sidecars already protect their respective source/version paths; their roles are reconciled in `track7_atlas_design_20261015/08_EXISTING_MECHANISM_RECONCILIATION.md`.
