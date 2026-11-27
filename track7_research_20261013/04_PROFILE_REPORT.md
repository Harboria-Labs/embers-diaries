# ELLA profile and stage measurements

Five repetitions per count, diskless in-memory encoded records. Medians in ms; min-max ranges are in `summary.json` and each observation is in `03_BENCHMARK_RESULTS.csv`.

| Events | Same ledger load | New ledger load | Encoded bytes |
|---:|---:|---:|---:|
| 10 | 18.94 | 23.04 | 32,104 |
| 20 | 51.64 | 52.46 | 98,729 |
| 30 | 108.82 | 114.53 | 199,864 |
| 50 | 289.10 | 377.85 | 505,634 |

At 50 events, isolated stage medians were: enumeration 0.04 ms, read/decode/first verification 76.26 ms, explicit second verification 54.68 ms, chain digest 18.70 ms, native replay boundary 151.14 ms. These are independently timed passes, so their sum is not a decomposition of a single load. The native replay boundary includes Python JSON encode, Rust JSON decode, Rust transition validation, Rust JSON encode, and Python JSON decode.

Within the 50-event fixture, encoded event 1 was 1,608 bytes and event 50 was 18,566 bytes. The total 505,634 bytes for 50 events is 15.75 times the 10-event fixture's 32,104 bytes, while the event count is 5 times larger. This directly supports the source explanation that later events carry progressively larger full states.

`profile_ella_50.txt` reports one 430 ms load: 50 `MemoryStore.read` calls; 100 `EmberRecord.verify_integrity`, `canonical_record_bytes` and `sha256_hex` calls; 50 chain digests; 50 `ella_reduce` calls plus one policy default. Cumulative native domain call 150 ms, canonical bytes 120 ms, JSON dumps 73 ms, native record decode 23 ms. These overlapping profile totals are clues, not exclusive shares. `tracemalloc` observed 2,208,898 peak Python-tracked bytes during a 50-event load; native allocations and whole-process RSS are not included.

The 10-to-30 median ratio was 5.75 on this fixture, versus Colab V3's reported ~9.1 at 10-to-30. OS, CPU, fixture content, native build and disk path differ. Neither result supports a universal exponent from so few sizes. Windows process CPU time was recorded in the CSV but has coarse ~15.6 ms granularity at these durations; use wall time for sub-stage comparisons.

No slow run was discarded. For example, the 30-event same-object load ranged 102.11-226.97 ms. The run was retained.
