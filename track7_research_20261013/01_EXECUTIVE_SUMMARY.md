# Track 7: bounded memory research, 2026-10-13

## Verdict

ELLA load reconstructs its journal from the beginning on every call. Each stored event contains a full post-event state, so history grows in both event count and event size. On this Windows machine a diskless fixture of 10, 20, 30 and 50 report events took median 19, 52, 109 and 289 ms to load. Reusing the ledger object did not remove the work. This supports the Colab direction but does not establish its exact scaling or persisted latency.

The current bottleneck is combined repeated decoding, two record-integrity checks, chain hashing, JSON transfer, and native transition validation/replay. The experiment cannot assign all native time to Rust computation because the Python/Rust boundary serializes input and output. In particular, source inspection shows the full state is embedded in every ELLA event. That makes total bytes across a history grow approximately quadratically with assessment count in this fixture.

Ordinary `MemoryProtocol.recall` does not invoke `EpistemicLedger.load` directly. Its selected record rendering can request ELLA projections via `explicit_truth`, while consolidated research recall explicitly includes those projections for admitted memories. Thus ELLA cost may affect recall surfaces that request epistemic metadata; the exact ordinary recall impact was not measured here.

## What should happen next

Restore sufficient free disk capacity, then rerun a small persisted, authenticated ELLA append/load experiment using the real `EpistemicLedger.apply`, with fresh disposable stores and stage counters. Keep the journal, evidence independence, corrections and native authority intact. See `06_RECOMMENDATIONS.md` and `08_LIMITATIONS.md`.
