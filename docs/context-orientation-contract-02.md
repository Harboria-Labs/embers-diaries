# Contract 02: context orientation and agent resolution

Implemented on the existing Experimental V0 server. Contract 01 and Candidate
04 equations are unchanged. The agent still chooses the context; this operation
only reports likely existing territory. It performs no writes or access-count
reinforcement, creates no contexts or relationships and uses no LLM.

## API

- SDK: `MemoryProtocol.orient(clues, namespace=None, hints=None, limits=None, signals=None)`
- MCP: `ember_orient`
- REST: `POST /v1/memory/orient`

MCP/REST require the existing authenticated identity/session and namespace read
permission. The SDK, like other direct DB operations, is trusted application code.

```json
{
  "clues": "latent reactivation query heat old memories",
  "namespace": "my-test",
  "hints": {"subject": "LADC"},
  "signals": ["subject", "primary_context", "content", "tags", "relations"],
  "limits": {"subjects": 5, "contexts": 10, "memories_per_context": 3,
             "records": 20, "candidates": 100, "relationships": 24,
             "preview_chars": 240, "response_bytes": 32768}
}
```

`clues` is required text, at most 2048 UTF-8 bytes. Optional hint values are exact
strings (up to 256 UTF-8 bytes) or null; hint keys are subject/primary_context.
Hints contribute search terms, never filters, assignments or identity decisions.
Ablation disables fields through `signals`; at least one lexical field is needed.
Omit `relations` to disable graph expansion. Unknown fields in limits/hints,
unsupported signals, duplicate signals and invalid bounds are rejected.

## Exact baseline

1. Tokenize clues and hints using the existing full-text tokenizer: lowercase
   ASCII alphanumeric terms with length at least two. Query the existing BM25
   index for the union of those terms within the requested namespace. This is
   OR-style lexical matching, not exact context matching or semantic inference.
   Read at most `candidates + 1` hits to expose shortlist saturation, retaining
   `candidates`. Reuse existing persisted/rebuilt indexes; no second graph/index.
2. Inspect current, non-deprecated durable NODE/DOCUMENT records only, with
   retrieval_candidate enabled and the correct namespace. Internal journal,
   policy, feedback and query-receipt records are excluded.
3. For each enabled field, expose matched clue terms. Subject/context fields
   also expose matched terms from their corresponding hints. Content is the
   existing content/text/summary payload; tags are separate. Discard initial
   candidates with no enabled field match. There are no hidden confidence or
   truth multipliers.
4. Rank by descending unique matched clue-term count, then descending matched
   hint-term count, then descending number of matching fields, then record ID.
   Each component is returned under `ranking`, and terms are under `signals`.
   BM25 is a shortlist mechanism only; its score is not the final ranking.
5. If relations are enabled, inspect outgoing edges from lexical seeds in that
   order, at most the relationship budget in total. Existing native-graph order
   is retained within a seed. Only one hop; no recursive expansion, reverse-edge
   inference, link creation or transfer of feedback. A same-namespace current
   durable target may enter even with zero lexical overlap; its supporting edge
   is shown. Total candidates including neighbors never exceeds `candidates`.
6. Group ranked rows by the EXACT stored `(subject, primary_context)` pair.
   Subject groups and context groups appear according to their highest-ranked
   representative. Same subject/different contexts stay distinct. The same
   memory appears once, even if several seeds point to it.
7. Enforce subject count, TOTAL context count, per-context representatives and
   total record count. Return a `territory` map, short previews, stored labels,
   provenance/annotation indicators, candidate IDs, returned IDs and diagnostics.
   No attached evidence histories are fetched; the response says `not_scanned`.
8. Measure compact UTF-8 JSON for the COMPLETE result object. If too large,
   remove tail candidate IDs first (report omitted count), then tail memories
   and empty groups until it fits. `response_bytes` includes its own field.
   Transport envelopes/pretty-print whitespace are outside this payload budget.

Labels are never truncated into false context identities. Records with invalid
or >256-byte stored labels are skipped, with a diagnostic count. Unset subject
or primary context appears as JSON null and is a real group, not an invented
name. The agent can copy an exact returned primary_context into Contract 01
recall; orientation never performs that choice automatically.

## Bounds

| Bound | Default | Hard maximum |
|---|---:|---:|
| Subjects | 5 | 20 |
| Contexts across all subjects | 10 | 40 |
| Representatives per context | 3 | 10 |
| Returned memories | 20 | 100 |
| Candidates, including graph targets | 100 | 500 |
| Examined outgoing edges | 24 | 100 |
| Preview characters per memory | 240 | 1000 |
| Whole result UTF-8 bytes | 32768 | 131072 |

All count bounds are positive integers; response_bytes has a 4096-byte minimum.
Returned edge descriptions are a subset of examined edges. Namespace filtering
also applies to graph targets; private cross-namespace content is not exposed.

## Observability and limitations

The response echoes clues/hints, enabled signals and applied budgets. It exposes
candidate/returned IDs, exact stored labels, individual field matches, relation
sources, ranking components, preview truncation and response-byte trimming.
An empty result means no eligible matches in this bounded search, not proof that
no relevant memory exists anywhere. There is no fabricated confidence value.

This is a measurable lexical baseline, not a final resolution formula. Different
wording works when words overlap stored content/tags/subjects/contexts, even if
none equal the context label. Synonyms with no lexical overlap, stemming,
multilingual semantic retrieval and canonical aliases are not implemented.

**Output bounds are not CPU/RAM guarantees for the existing indexes.** BM25 may
score many postings internally before taking top-k; native graph queries read
existing adjacency and may materialize a seed's full edge list before this
operation examines its bounded prefix. Index optimization is a separate measured
follow-up. Large matching subjects are never dumped into the response.

The shared BM25 shortlist includes all indexed payload fields. Disabling a field
removes its qualification/ranking contribution, but does not build a separate
field-specific index. Thus ablation is exact inside a fixed shortlist, not an
end-to-end independent retrieval experiment. Initial BM25 score ties inherit
index order; after shortlisting, rank ties use memory ID. A capped pool can omit
some contexts or graph targets; no exhaustive coverage or diversity guarantee.

History/evidence and memory content are inspectable by the existing tools using
returned IDs. This operation does not return full histories or judge correctness.
No frozen Contract 01 assumption was changed. No Contract 03 work is included.

## Validation and live run

2026-09-26: **590 tests passed**, including **22 new Contract 02 tests**.
Coverage includes rough clues, field ablations/hints, distinct subcontexts,
existing one-hop graph links, private namespace denial, unchanged store files,
no-result/unset behavior, 140 matching memories under small output budgets,
invalid inputs, current-version filtering, restart persistence, MCP/REST parity,
and the existing Contract 01 regression suite.

An actual HTTP test against normal `uvicorn embers.api:app` passed for both MCP
and REST orientation, no-result behavior and handoff to Contract 01 recall.
The user's deployed server was not modified or tested by this implementation run.

After updating/reinstalling the branch and restarting the normal server:

```sh
python examples/orientation_live_smoke.py --url http://127.0.0.1:9200
```

The script creates a unique test namespace, registers a test agent, exercises
orientation and prints the result with no credentials. Refresh the connected
agent's tool catalog to see `ember_orient`. No separate server or resolver setup
is required for this read-only operation.

| Track | Research contract | Implementation | User live validation |
|---|---|---|---|
| Contract 01 primary context | Closed per user | Preserved; regressions pass | Previously closed per user |
| Contract 02 orientation/resolution information | Accepted baseline | Implemented; local + HTTP checks pass | Pending |
| Autonomous context identity/merging | Excluded | Not added | Not applicable |
| Contract 03 / context-transfer learning | Outside this task | Not added | Not applicable |
