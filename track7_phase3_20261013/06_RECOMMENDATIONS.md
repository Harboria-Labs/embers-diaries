# Design recommendation, not an implementation

The smallest promising design is to use the existing MasterIndex namespace IDs and record-type metadata to select candidate CONFLICT IDs, while leaving current-head resolution, record verification, authorization, ordering, filtering and error construction unchanged. That removes unrelated physical reads on healthy indexed stores without new persistent state. However, the tested type prefilter is **not yet acceptable as an exact replacement**: it missed stale-type conflicts and unrelated-record integrity errors.

Before implementation, choose and prove one of these contracts:

1. Preserve the current fail-closed behavior for unrelated corrupt namespace records. In that case a narrow lookup needs a separate integrity guarantee for skipped records, or must scan them; the current experiment has not found a cheap exact solution.
2. Explicitly narrow the lookup contract to conflict-record integrity after a separately validated index/store health check. This would be a deliberate semantic change requiring review, not a transparent optimization.

Then test concurrent writes and callback failures, nonempty stale persisted indexes, restart/recovery, supersession chains and cross-namespace authorization. A graph-only gate and tag-only index should not be used as conflict authority. Avoid a new persistent conflict index until the existing MasterIndex metadata and its recovery guarantees are understood.

Do not alter ELLA evidence or correction mathematics to solve this lookup cost. Conflict overlay is a separate projection input.
