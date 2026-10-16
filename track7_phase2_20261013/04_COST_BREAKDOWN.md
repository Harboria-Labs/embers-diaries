# Cost breakdown and attribution limits

## Measured append stages

At report 50, full append was 1,934 ms. The instrumented call to `ledger.load` inside it was 645 ms; the physical store write was 101 ms; direct SQLite observation append was 29 ms. Calls to `EmberStore.read` accumulated 1,037 ms across the complete append, and `verify_integrity` 614 ms. Those read and verification timings are nested inside load, projection and other work, so **do not add these numbers**. Native ELLA calls accumulated 347 ms including JSON boundary work; 50 report replay calls plus the final transition were part of that path.

At report 5, append was 348 ms, nested load 37 ms, store write 81 ms, observation append 28 ms. By report 50, the store-write and direct observation timings remained roughly tens of milliseconds while repeated reads, verification, projection and replay grew. This supports replay/projection as the main growth mechanism in this fixture. It does not isolate an exclusive WAL time: native `write_record_transaction` includes WAL append, record publication, metadata and flush work in the timed store write.

## Projection surprise

At 51 events (50 reports plus correction), a separate read-side profile found median full projection 648 ms and 120 store reads. Of these, 51 reads came from ELLA load; a supplied-state projection still made 69 reads and spent a median 199-276 ms in `conflict_records`, depending on the profiled call grouping. The source shows `conflict_records` loads all namespace records before filtering for CONFLICT. The profiler's single 51-event projection attributed 239 ms cumulative to `conflicts_for`/`get_namespace` and 454 ms to `load`. The full profile is in `profile_project_51.txt`.

The append implementation projects before and after the new assessment and projects the final response. With no conflicts, each of these can still enumerate/read namespace records. This explains why append read counts were 258 at report 50, much larger than the 49 prior ELLA events alone. The direct `project` benchmark itself had median 118 store reads and 169 integrity verifications at 50 reports.

## Persistence growth

At 5, 10, 20, 30 and 50 reports, ELLA RAW records occupied 32,040, 84,759, 242,099, 468,649 and 1,129,349 bytes. Later events embed larger full states and response projections. WAL and observation SQLite also grow because they persist event content. Repetition of full state in each event is source-backed; five sizes do not establish a universal exponent.

`03_RAW_RESULTS_50_RETRY.csv` records per-operation wall/CPU time, free space, journal/store/WAL/observation bytes, read and verification counts, and nested instrumented timings. Process RSS was unavailable because `psutil` was not installed. CPU time on Windows has coarse resolution at short durations. All slow runs were retained; for example, 50-event repeat loads ranged from 369 to 788 ms.
