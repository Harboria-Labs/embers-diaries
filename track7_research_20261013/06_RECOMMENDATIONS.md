# Proposals only: no implementation changes

| Rank | Candidate next step | Expected benefit | Confidence | Risk to semantics |
|---:|---|---|---|---|
| 1 | Measure real persisted `apply` and load stages at 10-50 events after disk reserve is restored. Count verified/read records, actual journal bytes and WAL activity. | Establishes whether disk, duplicate verification, embedded state, projection or observation delivery dominates true append latency. | High value, no optimization claim | Low if fully isolated and read-only instrumentation |
| 2 | Prototype a separately verified checkpoint/index design, then prove replay equivalence for corrections, invalidations, order, independence and retries. | Could bound repeated reconstruction while retaining complete journal audit. | Medium: source shows replay; no prototype proof | High until integrity/equivalence proof exists |
| 3 | Evaluate whether one integrity check can be shared between store read and ELLA load without weakening fail-closed behavior. | May remove repeated canonicalization/hashing on load. | High that duplicate work exists; medium benefit estimate | Medium because read trust boundary changes |
| 4 | Benchmark a streaming/lazy event representation and compact state storage in an isolated research prototype. | Could reduce total journal bytes and JSON transfer. | Medium | High due historical-format and recovery compatibility |
| 5 | Profile query postings, FUR identity replay and Pairing outgoing-degree separately using bounded in-memory fixtures. | Locates non-ELLA capacity risks. | Source-backed, not yet locally measured | Low for research |

Do not replace evidence history with exposure counts, conflate U/N_eff with truth, weaken correction lineage, change the Pairing direct-result rule, or move reasoning into Ember. Any optimization must reproduce exact existing projections and journals before deployment.
