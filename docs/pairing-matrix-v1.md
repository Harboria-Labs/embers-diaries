# Pairing Matrix V1 implementation

## Frozen selection and integration

Rust `domain/pairing.rs::select` implements the frozen rule: inspect only stored outgoing edges of the first successfully admitted direct memory; require explicitly recorded `metadata.primary_context` equal to the request's exact context, accepted effective FUR evidence, W greater than the configured neutral prior, and a target absent from the direct returned set. Choose the greatest W and at most one target. Exact ties use a SHA-256 canonical edge identity (source, target, type, context, persisted edge ID), with no semantic relation priority. No Q, U, activation, truth, target relevance, incoming paths, or graph weight enters selection.

`fur.rs` is unchanged. Its existing derive function excludes unresolved evidence and recomputes W from accepted evidence. An unresolved experience does not veto other independent accepted experiences; no new aggregate pair-resolution policy is introduced. An unresolved-only state remains neutral and ineligible.

The consolidated `ember_research_recall` / `POST /v1/research/recall/{namespace}` path chooses A after existing native direct admission and before pair traversal. Its existing tokenizer, item/neighborhood caps, result cap and total capacity remain authoritative. The best pair is attempted once after all direct rows. If it fails admission, nothing substitutes for it. Pair targets are not added to activation_batch or activation state. Original request retries replay their original committed response.

Ordinary `ember_recall` / `POST /v1/memory/recall` uses the same selector after its existing context builder has admitted direct memories. Its existing top_k/max_results capacity applies; text and messages additionally retain their existing approximate character/token budget. Structured/raw ordinary recall historically has no tokenizer cap; this implementation does not invent one. Existing direct sorting, filtering and access accounting remain unchanged. Pair targets are loaded without access tracking. Legacy Candidate 04's separately configured candidate-recall harness is unchanged; it is not the consolidated LADC path.

Existing edges without explicit recorded context are ineligible, including edges whose source/target memory happens to have the requested context. Explicit null is a recorded unset context and matches only an unset request. Unavailable or cross-namespace winning targets are not returned, and do not cause fallback to a lesser-W edge.

## Relationships and existing feedback

- SDK: `db.link(A, B, 'explains', primary_context='research')` adds explicit context to existing graph metadata. Omitting the keyword preserves legacy link behavior.
- HTTP: `POST /v1/memory/pair-relationship` with `source`, `target`, `relation`, and required `primary_context`.
- MCP: `ember_pair_relationship` with the same fields and existing agent/session authentication.

The authenticated operation requires write access to both endpoints and one shared namespace. It reuses the existing edge on exact retries. Supported pair relation types remain `explains`, `requires`, `warns_about`, `alternative`. Creating an edge does not add FUR evidence or initialize a second weight.

Later feedback uses **existing** `ember_usefulness_update` (or the existing usefulness HTTP command) with:

```json
{
  "action": "report",
  "namespace": "experiment",
  "request_id": "feedback-request-id",
  "payload": {
    "target": {"kind": "pair", "memory_ids": ["A", "B"], "relation": "explains"},
    "context": "research",
    "feedback_type": "PAIR_HELPED",
    "query_request_id": "original-retrieval-id"
  }
}
```

`PAIR_IRRELEVANT` and `UNUSED` keep their existing meanings. There is no new `PAIR_MISLEADING` label: the existing FUR API does not support it. Memory feedback remains a separate memory target. Existing resolution, merge, split, deduplication, identity and correction commands are unchanged.

## Provenance and observation

Responses expose `direct_ids`, `primary_memory_id` and nullable `pair_expansion`. A returned pair carries source, target, relation, exact context, W used, persisted and canonical edge identities, role PAIRED and query_request_id where available. Consolidated structured output also embeds this receipt in the paired row. Legacy recall has no persisted query request identity in its response; that field is explicitly null there rather than guessed.

Consolidated committed observation events and ordinary recall observation events preserve direct_ids and pair_expansion. Research Observer exposes them and marks pair expansion OBSERVED only when a pair actually returned. Namespace observation events contain the same receipt. No H is manufactured for a paired memory. SSE transport is unchanged.

## Read-only boundary

Selection uses existing graph reads, FUR derive, and record reads with track_access=False. It writes no feedback, W, U, evidence mass, truth, model time or memory state. Normal direct recall retains its pre-existing activation/access and observation commits. Relationship creation and later explicit feedback remain deliberate separate writes.

## Validation

Tests in `tests/test_pairing_v1.py` cover selection, exact context/direction/type, explicit unset versus absent context, neutral and negative W, already-direct targets, empty sets, deterministic ties, one hop, a 10,000-edge hub, incoming-path irrelevance, corrections, conflicts, duplicates, merge/split, exposure without learning, truth preservation, capacity rejection without substitution, unavailable targets, restart, read-only file hashes, ordinary recall formats, API/MCP authorization and provenance.

No server was started and no deployment was performed. Results and commit are reported in the implementation handoff.

### Final local results

- Full suite: **703 passed**, one dependency deprecation warning (Starlette's httpx TestClient integration).
- Added **32 Pairing V1 tests** (including parametrized cases). Existing consolidated assertion and MCP discovery count updated to reflect the integration.
- Native release wheel built successfully; tests exercised the compiled Rust selector.
- Initial full run: 699 passed, one stale 44-tool count assertion failed; corrected to 45, final full run above passed.
- In-process TestClient requires cross-thread event-loop socket operations unavailable in the restricted test sandbox. API tests were run with approved sandbox escalation; no listening server was started.

Changed files:

- `rust/ember_core/src/domain/pairing.rs`, `domain/mod.rs`: native selector and dispatch.
- `embers/integration/pairing.py`, `embers/db.py`: existing graph/FUR IO and contextual link metadata.
- `embers/integration/consolidated.py`, `memory_protocol.py`: post-direct pair admission.
- `embers/api/research_routes.py`, `embers/mcp/server.py`, `embers/mcp/tools.py`: normal-server relationship operation.
- `embers/integration/usefulness_service.py`, `research_observer.py`: retrieval receipts in existing observations.
- `tests/test_pairing_v1.py`, `test_consolidated.py`, `test_mcp_http_transport.py`: validation.
- This document.

For later installation, rebuild the Rust extension in the existing server environment after pulling the branch (for example, `python -m pip install -e .`). A Python-only source refresh leaves the previous native module without `pair_select`. This work does not deploy or restart the server. Live validation remains pending.
