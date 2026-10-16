# Conflict retrieval: use the existing master index with a trust gate

> **Reconciled 2026-10-15:** The durable dirty-marker/generation protocol below is a candidate, not a proven minimum. Existing WAL recovery, physical-ID enumeration, atomic supersession sidecars and index rebuild must be evaluated first. See [existing-mechanism reconciliation](08_EXISTING_MECHANISM_RECONCILIATION.md). The index-completeness requirement remains.

## Current path and reuse

`EmberDB.conflicts_for` authenticates the target memory and namespace, calls `conflict_records`, then filters endpoint/status (`embers/db.py:903-959`). `conflict_records` enumerates a namespace through `ReadEngine.get_namespace`, reads all selected physical records, filters CONFLICT, resolves current versions and deduplicates. `map_conflict` and status transitions create/update authoritative records; graph `CONTRADICTS` links are separate derived relationships (`embers/db.py:789-1000`). REST/MCP use the shared conflict contract (`embers/integration/conflict_protocol.py`; `embers/api/v1.py:362-470`; `embers/mcp/server.py`). Existing master metadata already has the fields needed to enumerate conflict candidates (`embers/index/master.py:98-159`).

## Smallest safe proposed path (unimplemented)

1. Under the existing store transaction lock, establish that the master snapshot is **certified for this physical store generation**. If not, fail closed with an index/recovery error or run an explicit maintenance rebuild before serving the query; never silently treat an uncertified empty candidate list as zero conflicts.
2. Select IDs whose **certified** metadata has the requested namespace and `record_type=CONFLICT`, including old versions needed for lineage resolution. Read and hash-verify each candidate, compare its actual namespace/type to metadata, resolve the current head via existing supersession logic, verify that head, deduplicate, then apply endpoint/status and existing caller ACLs. Keep `conflict_records`, `conflicts_for`, REST and MCP result semantics.
3. Move `_find_live_conflict` onto the same certified candidates so mapping does not retain a whole-store read path. Preserve stable pair identity and closed-conflict remapping behavior. Do not use tags or graph edges as candidate authority.

**Completeness invariant:** at certified generation G, every durable physical CONFLICT record committed through G appears in the master candidate set with correct namespace/type; every physical supersession sidecar through G is represented in lineage resolution. The certificate must become invalid **before** any subsequent physical write can be committed, and become valid only after index metadata and sidecars are durably reconciled. A callback warning cannot certify success. All handles/processes must observe invalidation/generation changes. Existing `writer._notify` and `MasterIndex.persist` do not meet this invariant (`embers/engine/writer.py:64-96`, `embers/index/master.py:86-114`).

An initial full physical reconciliation should build/verify the certificate, including comparing actual record IDs and type/namespace values with master metadata, not merely comparing counts. Recovery should recheck WAL and supersession sidecars, rebuild master from physical records if dirty/mismatched, persist the rebuilt snapshot, and publish healthy status last. A corrupt record is a storage-integrity failure; valid records with missing/wrong master metadata are a derived-index failure. Neither should become a false empty result. This can upgrade the existing MasterIndex/checkpoint/rebuild path; a dedicated conflict index is not justified by current evidence.

## Failure and concurrency details

- `PhysicalStore.write` commits the record before callback notification; native WAL transaction excludes `master.json` (`embers/storage/store.py:192-229`; `rust/ember_core/src/lib.rs:641-673`). A durable dirty marker/generation and ordered master snapshot publish are proposed integration work. The exact transaction protocol requires a focused implementation contract; a query must reject a stale/dirty generation.
- `map_conflict` performs duplicate lookup before its write without an enclosing operation lock (`embers/db.py:789-865`). `update_conflict_status` reads a head then calls `update` without `expected_hash` (`:961-1000`), although CAS exists (`embers/engine/writer.py:98-176`). Concurrency tests must establish whether existing pair/status semantics require locking/CAS before certification. Do not silently collapse legitimate memory branch semantics.
- Graph add follows conflict record creation and can fail separately; graph is repairable relationship data, not proof of record existence. Corrections/supersession must continue resolving record heads, and restart/recovery must retain valid old and new conflict versions.
- An external modification of a physical file after certification cannot be detected without re-reading it or an independently trusted change signal. Returned records are always verified; an independent audit must detect other corruption. The threat model for out-of-band file tampering is an open contract question.

## Alternatives

| Approach | Decision |
| --- | --- |
| Tag or graph prefilter | Rejected: both can be absent for valid conflict records; Phase 3 counterexamples and graph write ordering support this. |
| Type filter on current master, no certificate | Rejected: stale nonempty master can omit a conflict. |
| Scan all namespace records on each query | Correct current behavior, but violates the requested separation from unrelated records/auditing. Retain only as explicit recovery/reconciliation operation. |
| New dedicated conflict index | Adds another derived structure and consistency burden; no evidence it is required if MasterIndex gets a trustworthy generation protocol. |

Compatibility risks: result order is currently inherited from unsorted namespace IDs and is not a stable API guarantee established here; error behavior for unrelated corrupt records changes by the frozen audit contract; conflict-specific corrupt records must still fail. Existing REST/MCP ACL and serialization must remain identical. Tests are listed in `06_TEST_AND_VALIDATION_PLAN.md`.
