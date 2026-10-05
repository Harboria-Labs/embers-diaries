# ELLA V1 working checkpoint — incomplete, not for deployment

Starting commit: 5a1d6b8b76a69589876c9c9057a5b3f7b0b2c8c3. User authorized Option 1: retain MemoryRoom values and explicit filtering. GENERAL-RECALL PERSONAL ISOLATION IS A PRE-EXISTING ROOM/RECALL GAP, outside ELLA.

Uncommitted draft, isolated from the existing test checkout. No push, deployment, or live server run. Native Rust builds. Twelve focused ELLA tests passed. Full suite at the first integration checkpoint: 715 passed, 13 failed. Some failures are intentional old promotion/truth expectations; four are retrieval/admission regressions and must not be accepted. Later observer projection edits have only been syntax-checked.

Implemented in draft, not claimed complete: Rust policy/assessment validation, dependency grouping and score projection; legacy-compatible Evidence V1 hashes and lineage V2; separate RAW epistemic journal with idempotency/CAS; preliminary assessment/confirmation/withdrawal/merge/split/lineage correction/carry-forward operations; API/MCP surfaces; promotion confidence no longer selects a verified state; annotation authority removed; preliminary observer delivery.

Still incomplete: comprehensive invariant/security tests, Rust ownership audit of all transitions, resolution edge cases, legacy API migration/docs, policy migration, efficient replay, bounded observer delivery review, full passing integration and live validation. Do not represent the draft as frozen or production-ready.

## Integration boundary requiring resolution

Current research recall renders `truth_projection` inside each memory row and passes that rendering through its existing exact-token admission checks. The draft made that projection the full ELLA state. This creates a forbidden coupling: changing epistemic evidence enlarges the serialized row and may evict an otherwise identical direct memory or pair.

Isolated real-native reproduction, configured per-memory cap 600, unchanged content and query scores:
- Before epistemic assessment: 525 item tokens; admitted.
- After one WEAK supporting assessment: 623 item tokens; rejected.
- No W or relevance change caused this; larger truth metadata alone did.

The default 512-token cap also rejected previously admitted Pairing fixtures when the expanded projection was inserted.

Smallest proposed integration decision: expose full canonical ELLA state through the distinct epistemic-state API/observer and a separate response metadata field, outside the rendered memory block used for admission. Decide the compatibility representation for existing inline truth fields without introducing verdict-dependent capacity behavior. Do not increase budgets or weaken capacity checks to hide this problem.

No retrieval change should ship until this boundary is settled. Existing math, MemoryRoom and selection algorithms must remain unchanged.
