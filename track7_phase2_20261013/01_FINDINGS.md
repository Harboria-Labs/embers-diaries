# Track 7 Phase 2: real persisted ELLA, 2026-10-13

## Plain-English result

On the installed Windows build, an authenticated ELLA report became slower as the persisted history grew. In the completed 50-report run, the median of the last five append operations was 1.93 seconds, versus 0.35 seconds for the first five. A repeat load at 50 reports took a median 469 ms; a full projection took 834 ms. This is a small, isolated fixture, not a production capacity limit.

The cost is not just the final disk write. Each append replays the previous ELLA journal and calls projection multiple times. Projection also asks for conflicts; its current `conflict_records` path loads all records in the namespace, including ELLA journal records, even when the fixture has no conflicts. A read-only follow-up found this conflict scan took a median about 199 ms at 51 events during a full projection. This is a second avoidable-looking scan that merits its own correctness-preserving design study.

| Reports | Checkpoint append ms | Last-five append median ms | Repeat-load median ms | Full-project median ms | ELLA journal bytes |
|---:|---:|---:|---:|---:|---:|
| 5 | 348 | 348 | 36 | 99 | 32,040 |
| 10 | 456 | 456 | 117 | 137 | 84,759 |
| 20 | 708 | 623 | 133 | 189 | 242,099 |
| 30 | 872 | 903 | 169 | 301 | 468,649 |
| 50 | 1,934 | 1,929 | 469 | 834 | 1,129,349 |

Each load/project figure is the median of three retained runs. The checkpoint append is one operation; last-five median is provided to reduce sensitivity to that one operation. All raw rows, including slow runs, are retained. At 50 reports the WAL was 1,279,002 bytes and observation SQLite file 1,327,104 bytes. The record count was 57 before the correction and 58 afterward.

## Confidence and limits

High confidence: full replay, duplicate integrity verification and namespace-wide conflict scanning are present in this workload, confirmed by source and record-read counts. Moderate confidence: their relative share of append latency, because instrumentation times are nested and Windows scheduling caused spread. The isolated store and native module are the real implementation; no manually constructed ELLA events were used.

One separate 50-event attempt failed on its first append with `PermissionError: Access is denied` after one event record appeared. That partial store was retained and excluded from performance aggregates; its error row remains in `03_RAW_RESULTS_50.csv`. A fresh retry completed all 50 reports, correction, journal read and reopen checks. The cause of the transient permission error is unproven.
