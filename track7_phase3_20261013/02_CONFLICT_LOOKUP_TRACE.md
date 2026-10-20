# Conflict lookup source trace

## Storage and lifecycle

`EmberDB.map_conflict` (`embers/db.py:789-855`) validates two existing durable memories, namespace access and same-namespace scope; it writes a first-class `RecordType.CONFLICT` record with `Conflict.to_record_payload` (`embers/core/conflict.py:47-135`). It also creates a `CONTRADICTS` graph edge between the memories via `EmberDB.link` and Rust `graph_add_edge` (`embers/db.py:1807+`, `rust/ember_core/src/lib.rs:75+`). The pair is symmetric. A duplicate live mapping returns the existing conflict ID. Closing a conflict permits a new mapped conflict for the same pair.

`update_conflict_status` (`db.py:961-998`) validates the transition in native Rust `validate_conflict_transition` (`lib.rs:600+`) and writes a new immutable conflict record superseding the old one. The current head carries OPEN, INVESTIGATING, RESOLVED, ACCEPTED_BOTH or SUPERSEDED status. Historical heads remain stored.

## Reference read path

`EpistemicLedger.project` (`embers/cognitive/epistemic.py:49-101`) calls `db.conflicts_for(target_id)`, then overlays `open`, `investigating`, or `none` on the native ELLA projection. `conflicts_for` (`db.py:931-959`) reads the requested memory, checks optional caller access, calls `conflict_records(namespace=memory.namespace)`, filters by either endpoint, then excludes RESOLVED/ACCEPTED_BOTH/SUPERSEDED unless `include_closed=True`.

`conflict_records` (`db.py:903-929`) invokes `ReadEngine.get_namespace` with deprecated records included and superseded heads excluded. It then filters `record_type == CONFLICT`, resolves each current head, de-duplicates by current record ID, reconstructs `Conflict`, and applies optional status. `ReadEngine.get_namespace` (`embers/engine/reader.py:126+`) obtains namespace IDs from `MasterIndex.get_namespace_ids` (`embers/index/master.py:146+`), then calls `get_many`, which calls `get` for every ID. `get` invokes `EmberStore.read` (decode and sealed-record integrity verification) and overlays deprecation, supersession, annotation and access sidecars. Thus the reference reads and verifies unrelated DOCUMENT, RAW and EVIDENCE records in the namespace before testing type.

The Rust graph query API (`lib.rs:154+`) loads adjacency JSON when called, but `conflicts_for` does not call it. The separate `db.conflicts(memory_id)` method returns memories linked by raw CONTRADICTS edges; it does not provide mapped conflict record identity, status, resolution or supersession history.

## Necessary and unnecessary reads

The target memory read is necessary for namespace and optional access behavior. Each visible current CONFLICT record in that namespace is needed to preserve pair filtering, status, order, current-head resolution and errors from malformed conflict payloads. A current conflict record with a stale or missing endpoint is still returned by the reference if its other endpoint matches: lookup does not revalidate both endpoints.

Unrelated current non-CONFLICT records are unnecessary to compute the conflict *value* on healthy stores, but the reference's reads can surface integrity errors from them. Skipping those reads changes observable error behavior. The existing MasterIndex stores `record_type` and `namespace` metadata, so an external candidate can filter IDs before record reads without adding persistent state. The index is rebuilt only when empty (`db.py:175-187`); a nonempty but stale persisted metadata snapshot is not automatically reconciled. Exact equivalence therefore needs a clearly defined index-integrity and corruption contract.

`_all_namespaces` (`db.py:1002+`) used by unscoped `conflict_records` enumerates and reads all store records. This broader path was inspected but not capacity-benchmarked here; the targeted ELLA projection passes a namespace.
