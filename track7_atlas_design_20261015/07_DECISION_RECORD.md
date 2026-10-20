# Research decision record — proposal only

**Status:** design research, unimplemented; no implementation contract frozen yet. Source: HEAD `f183346547cd210f58ca0a6b23df91a56be22b66` plus dirty working-tree limitations recorded in the Atlas. No test/build/runtime execution in this investigation.

1. **Prefer upgrading MasterIndex over a new conflict index.** It already contains namespace/type/lineage metadata, but needs a durable completeness/health protocol before candidate filtering is safe. Keep first-class conflict records as truth and graph/tags as derived views.
2. **Put independent audit in existing maintenance.** Add a strict read-only physical/index audit, durable progress/failure reporting and explicit recovery. Existing reflect/consolidate, checkpoint and WAL recovery remain distinct operations.
3. **Start ELLA with a verified in-process current state.** Full replay seeds and independently checks it; new events advance via the existing native reducer. Do not trust the last embedded state alone and do not create a second epistemic authority. Durable projection is a later option if cold replay proves material.
4. **Preserve existing memory history.** Agent-accessible version lineage and ELLA assessment events serve different semantics.

## Questions to resolve before freezing an implementation contract

- What durable generation/dirty-marker ordering guarantees cross-process index and ELLA-head completeness through every native WAL crash point? Does the generation need a native transaction change, or can a prewrite dirty marker plus ordered reconciliation under the store lock suffice?
- What is the required response to out-of-band physical tampering between independent audits? Returned records are verified, but no bounded query can prove all unrelated records untouched without a trusted change signal.
- Which legacy unsealed records count as audit failures, and who may view detailed audit status or initiate repair?
- Must concurrent `map_conflict` and status transitions be serialized/CAS to preserve unique pair and linear status history? Current code does not enclose duplicate lookup plus write in one operation lock and does not pass `expected_hash` for status updates.
- Is a warm ELLA cache plus directory/head check sufficient for the performance target, or is a durable head pointer needed? No benchmark was run here.

These are specific contract gates, not invitations to replace existing features. Smallest next action: review and freeze the generation/failure/authorization contract and the focused regression matrix, then request user approval before any source change.
