# Highest-priority next investigation

Profile and then prototype a **read-only conflict lookup** on an isolated copy of the fixture that returns the same conflict set without loading all namespace records. Compare exact results across open, resolved, superseded and cross-namespace conflicts before considering any implementation change. The current zero-conflict 51-event projection still spent roughly 0.2 seconds scanning namespace records, and `apply` projects more than once. This is a concrete additional cost beyond journal replay.

Second priority: design an ELLA checkpoint/replay experiment with equivalence proofs for corrections, invalidation, confirmation, idempotency, ordering and tamper detection. Full replay and full-state event copies clearly grow, but changing them has greater integrity risk. Preserve the journal as audit authority, evidence independence, exact claim version, and native transition validation.

No optimization was implemented in this phase.
