# Correctness and recovery checks

- Agent registration and token authentication succeeded in each completed isolated run. The token was never written to a report or printed.
- The claim was a durable DOCUMENT; five distinct EVIDENCE records were attached using Ember's real write path. Each report used a unique request ID and revision. `EpistemicLedger.apply` authenticated namespace writes and accepted all 50 reports.
- At each checkpoint, `ledger.load` returned the expected event and assessment count, and projection reported the same epistemic revision. This load verifies every sealed journal record twice, checks chain order/seals, and runs native `ella_reduce` on each event.
- The bounded `correct_evidence` action became event 51. Its override is present in reconstructed state, while the original evidence record's `to_dict` remained unchanged. No evidence history was deleted.
- Reopening the 50-event store with `EmberDB.connect`, re-authenticating the registered agent, and loading with a new `EpistemicLedger` reproduced the same state and 51-event count. Reopened projection reported revision 51.
- Read-only `observation_journal.read` returned 51 chain-validated SQLite observations for the completed 50-event store and 31 for the 30-event store. These match source ELLA event counts. The 50-event observation file was 1,372,160 bytes after correction.
- An earlier separate 50-event attempt failed at event 1 with `PermissionError: Access is denied`. Its isolated store contains a partial first event and is retained for diagnosis. No recovery, deletion or replay was attempted there, and it is excluded from successful-run aggregates. Exact failing filesystem call was not captured in that attempt.

These checks establish validity for the tested report/correction sequence, not for all ELLA action combinations or crash recovery scenarios.
