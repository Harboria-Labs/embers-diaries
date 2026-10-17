# Batch 3 local implementation evidence — 2026-10-17

Branch `codex/ember-split-feedback-candidate04`; pre-commit HEAD `8a7e3c1a89875dc5497edac00ce2d76550bc4fbb`. The test interpreter loaded `embers/_native.cp313-win_amd64.pyd`, SHA-256 `452cc6d246c96311ddfe590f38947b6d386d39d4cf38f9650319e3833c6c19a5`. No native source or binary changed.

## Implementation

`embers/maintenance.py` now provides an independent, opt-in physical audit and local-operator-only MasterIndex recovery. The audit enumerates physical `.ember` sources independently of the index, strictly decodes and checks scoped records, compares source identity/type/namespace, hashes, lineage, derivation references, sidecars, and persisted MasterIndex metadata. It records legacy unsealed records separately. An index-only ID is treated as ambiguous source loss versus obsolete metadata and blocks automatic recovery. Normal read and conflict paths are unchanged.

Reports are atomically published under store `meta/integrity_audits/<uuid>.json`; `active.json` prevents simultaneous runs. Reports include scope, initiator, timestamps, source epoch boundary, status (`running`, `complete`, `failed`, `interrupted`, or `stale`), certification flag, examined count, category counts, bounded findings, truncation count, and recovery actions. A scan that sees changed source epoch or physical IDs is stale. A later write makes a completed report appear stale when read. A lease-expired running report is persisted as interrupted. A complete scan with findings is **not** certified. Report metadata is diagnostic, not memory authority or an index.

`embers/api/feedback_routes.py` adds authenticated POST `/v1/maintenance/integrity/audits` and GET `/v1/maintenance/integrity/audits/{run_id}`. Starting an audit requires WRITE ACL for the entire requested namespace list before any scan; report access requires READ ACL for its entire scope. REST offloads scanning to a worker thread. `embers/mcp/feedback_surface.py` exposes the same backend as `ember_integrity_audit` and `ember_integrity_audit_status`. Its existing `ember_run_maintenance` now validates the complete namespace list and WRITE ACL before reflect/consolidate. No store-wide agent audit or agent repair endpoint exists. Recovery uses existing `_reconcile_master_index`, then re-audits, and is callable only from a trusted local-operator code path; source findings prohibit it.

## Executed isolated tests

Each command used `./diaries/Scripts/python.exe -B -m pytest`, `-q -x -p no:cacheprovider`, and a unique `--basetemp` under the repository. Baseline before edits: `tests/test_verify_integrity.py tests/test_write_audit.py` **12 passed**; `tests/test_api_security.py tests/test_config.py` **33 passed**. Post-edit:

| Files | Result |
| --- | --- |
| `tests/test_track7_batch3.py` | 24 passed, 1 dependency deprecation warning |
| `tests/test_verify_integrity.py tests/test_write_audit.py tests/test_api_security.py tests/test_config.py tests/test_indexes.py tests/test_track7_batch1.py tests/test_conflict_interfaces.py` | 97 passed, 2 skipped |
| `tests/test_track7_batch2.py tests/test_promotion_engine.py tests/test_mcp_server.py tests/test_research_observer.py` | 71 passed, 1 dependency deprecation warning |
| `tests/test_consolidated.py tests/test_feedback_replay.py tests/test_pairing_v1.py tests/test_v1_memory_read_api.py tests/test_ella.py` | 125 passed, 1 dependency deprecation warning |

The 24 new cases include corrupt hash, malformed/missing physical source, legacy unsealed, wrong type/namespace, stale and missing index entries, sidecar and lineage faults, ACL/redaction, REST/MCP parity, interrupted and failed reports, explicit recovery and re-audit, concurrent source changes, and a killed child process. Earlier intermediate runs exposed a test monkeypatch that intercepted audit metadata and an incorrect expectation that an index-only ID was safely repairable; both were corrected and the full file passed. A killed process is not a power-loss test.

## Limits and rollback

The API needs a DB handle; if startup fails closed on corrupt physical source during Batch 1 reconciliation, stored reports remain on disk but these API routes cannot open that store. Local operator diagnosis remains possible; a separate safe diagnostic bootstrap would need authorization and design. Scoped audits cannot attribute undecodable records to a namespace, so they record a redacted finding and withhold certification. Source changes during a scan produce stale status; the scan is not a long-held snapshot. Report metadata is not cryptographically sealed. A real power-loss boundary, a production-scale audit, and live service behavior remain unverified. Scheduling audits automatically was not added because no approved principal/scope exists for private namespaces. Batch 4 and the Control Center remain unimplemented.

Rollback: revert only the Batch 3 source/API/MCP changes after stopping new audit requests. Preserve the `integrity_audits` reports for review; they are independent of memory records and the Batch 1 MasterIndex. No live store, service, native binary, or ngrok configuration changed.
