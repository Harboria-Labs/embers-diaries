# Track 7 Phase 3 findings

## Verdict

Yes, ordinary conflict lookup reads avoidable records on healthy stores. The reference `conflicts_for` loads every current record in the target namespace and filters for CONFLICT afterward. At 200 unrelated records, it made 214 physical record reads versus 9 for a research-only type-metadata prefilter, while returning the same conflicts in the tested valid fixture. Measured instrumented medians were 817 ms and 45 ms, respectively; these include `tracemalloc` overhead and Windows filesystem variation.

**No tested narrow candidate was exactly equivalent across all conditions.** Type metadata filtering missed a conflict when index type metadata was stale, including after a deliberately altered persisted index was reopened. It also skipped an integrity error from a corrupted unrelated DOCUMENT record that the reference raised. A tag filter missed a valid untagged CONFLICT record. A graph gate missed a mapped conflict after its `CONTRADICTS` edge was absent, including after restart. These are counterexamples, not implementation changes.

The semantic conflict authority is the CONFLICT record and its current supersession head. The graph edge is an additional relationship view, not sufficient to recover all conflict statuses or histories. Existing MasterIndex type and namespace metadata could support a narrower read under a validated-index/healthy-store contract, but it is not a drop-in exact replacement for the current observable error behavior.

## Main measured comparison

| Unrelated records added | Reference reads | Type prefilter reads | Reference median | Type prefilter median | Reference bytes read | Type bytes read |
|---:|---:|---:|---:|---:|---:|---:|
| 0 | 14 | 9 | 53 ms | 28 ms | 12,127 | 8,927 |
| 25 | 39 | 9 | 219 ms | 33 ms | 28,218 | 8,927 |
| 100 | 114 | 9 | 536 ms | 31 ms | 76,519 | 8,927 |
| 200 | 214 | 9 | 817 ms | 45 ms | 141,019 | 8,927 |

These are three warm repetitions per candidate/case. The candidate still iterates namespace IDs in the in-memory master index, so the physical record-read reduction does **not** prove constant-time lookup. Graph degree 0, 10 and 30 did not change the reference's 214-read count; it makes no Rust graph query during `conflicts_for`. Latency at fixed record count varied substantially, so read/byte counts are stronger evidence of the mechanism than small wall-time differences.

## Safety stop

The research stores and scripts occupied about 0.65 MB when checked. Free drive space nevertheless fell unexpectedly from about 543 MB after the main run to about 101 MB, then 89 MB during report assembly. The optional fresh-store cProfile script refused to run at its 500 MB preflight threshold. No further fixtures or tests were started. The cause of the drive-wide change was not established and must be investigated before more persisted research.
