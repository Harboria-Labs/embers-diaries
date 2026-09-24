# Candidate 04 experimental implementation

This opt-in implementation connects durable relevance resolution, directional
pairings, active/latent transfer, and bounded context rendering. Legacy recall
remains available. Use the experimental launcher for the integrated path.

## Start a live test

From a checkout of branch `codex/ember-split-feedback-candidate04`, with Python
3.10+ and a current stable Rust toolchain installed:

```sh
python -m pip install -e '.[experimental,dev]' httpx
python -m embers.experimental_server --encoding cl100k_base
```

`cl100k_base` is an example tokenizer. Choose the encoding for your test model.
The tokenizer may download its vocabulary on first use. The server listens on
`127.0.0.1:9200`, with MCP at `/mcp` and REST under `/v1`. It creates a separate
`ember-experimental-store` with a 256 MiB managed logical-byte limit and saves
private resolver credentials in `ember-experimental-credentials.json` outside
that store. Reuse these paths on restart. Do not commit or publish credentials.
The server never prints the token. On Windows, restrict the credential file's
ACL to your account; the POSIX creation mode is not a Windows ACL guarantee.

In another terminal:

```sh
python examples/candidate_live_smoke.py
```

This writes a test memory, calls MCP and REST recall, reports usefulness,
resolves that outcome and checks retry protection. It prints PASS on success.
To connect remotely through your existing tunnel, point it at port 9200 and
use its HTTPS URL ending in `/mcp`. Keep the resolver credentials private.

The configured namespace is `memories`; context ID is `live-test`; its exact
context descriptor is `{"task":"live-test"}`. This is a test context, not a
universal context inferred from wording. Applications configure other contexts
with `db.relevance_journal(...)` and `CandidateRecall(...)` at trusted startup.
HTTP callers cannot configure resolver authority or token counters.

## Agent workflow

1. The agent finds candidate IDs and supplies direct scores in [0,1]. Ember
   does not claim to implement a trained semantic judgment model.
2. `ember_candidate_recall` (or POST
   `/v1/relevance/{namespace}/{context_id}/recall`) takes `query_id`,
   `direct_scores`, `elapsed` and optional `format` (structured/text/messages).
   Time is explicit model time, not implicitly seconds. A repeated query ID
   returns its original receipt; changed input with the same ID is rejected.
3. Submit schema-version-2 `ember_feedback` with channel `relevance`, outcome
   `useful`, `irrelevant` or `misleading`, outcome/context IDs and a signed
   signal. Mere appearance calls for no feedback. Correctness has its own
   channel, supporting references and no relevance signal.
4. An authorized resolver calls `ember_resolve_relevance` with `request_id`,
   `expected_revision` and a decision containing outcome ID, status, credits,
   report IDs and a nonempty reason. Credits target immutable memory versions
   or directional typed pairs: explains/requires/warns_about/alternative.
   Total absolute credit per outcome is at most one. Unresolved group credit
   remains pending rather than being spread arbitrarily.
5. Corrections create immutable revisions and replace the outcome's effective
   contribution. Replay preserves original outcome order and suspends stale
   dependent decisions. Read the projection with `ember_relevance_state`.

Multiple reports about one outcome are not independent reinforcement. Resolving
outcomes requires trusted attribution; inventing different outcome IDs is not
mathematically detectable by this implementation.

## Equations and proof boundary

For accepted, effective outcome credit s, learning is
`x' = clip(x + eta*s, -1, 1)`, separately for contextual memory bias b and typed
pair state z. Retrieval uses `w = max(0,z)`. Corrections replay from zero, so
clipping does not leave irreversible old reinforcement.

Direct cues d are augmented only one hop:
`q = d + beta*(1-d)*min(1, sum(normalized positive incoming pair cues))`.
Outgoing normalization uses `max(1,sum(outgoing weights))`. Seed and traversed
edge counts are capped. Consequently `0 <= d <= q <= 1`; there is no recursive
pair amplification.

LADC uses `u=rho*q*(1+mu*b)`,
`v=rho*((1-d)*(1-mu*b)+delta)`,
`E=epsilon+(1-2*epsilon)*u/(u+v)`,
`A'=E+(A-E)*exp(-(u+v)*elapsed)`, and `L'=1-A'`.

* Clipping proves b,z remain in [-1,1] by induction.
* For positive rho/delta and mu<1, u>=0 and v>0. E is inside the
  active/latent floors, and the exponential update is a convex combination
  of A and E. This proves boundedness and capacity conservation.
* With no cue, E=epsilon, so passive decay cannot rise. Fixed cues converge
  exponentially to E. Arbitrarily changing feedback need not converge.
* Policy validation requires the activation threshold below the worst-case
  equilibrium for a declared strong direct cue. Latent inspection precedes
  the threshold, so a low activation does not exclude discovery.
* The relevance projection and activation functions do not write epistemic
  state. For fixed correctness inputs, replacing any relevance history leaves
  truth state unchanged: the relevance-to-truth derivative is zero wherever
  a derivative is meaningful. This is a structural statement, not a claim
  that future agent behavior/evidence selection is causally independent.

These are analytical arguments and executable invariant tests, not
machine-checked formal proofs or a claim of globally novel mathematics.
SPRT calibration and automatic truth promotion remain deferred. Correctness
reports alone do not establish truth. Recall includes existing truth labels;
useful but incorrect memories can appear as warnings without promotion.

## Attention and storage limits

The configured tokenizer counts the exact returned `context` string, including
its labels/wrappers. Total, per-memory and per-neighborhood caps are enforced.
Oversize entries are skipped. Transport envelopes and other prompt content are
outside this budget. Choose matching model tokenization before interpreting
these counts as model tokens.

One latent candidate is inspected per query before normal threshold filtering.
This is not universal discovery: the agent must nominate a memory or reach it
through a learned pair. Changing candidate sets do not have a global fairness
guarantee. The transient activation map has a capacity; removed cache entries
restart from the floor. Durable memory and learned feedback are retained.

The existing Rust backend enforces `storage.max_total_bytes` across files under
the managed store root, including WAL, metadata, indexes, annotations and
replacement-file peak space. Pending WAL recovery space is reserved. Admission
is serialized across cooperating writers. The cap survives reopen and cannot
be silently changed. `db.storage_usage()` reports logical usage.

This is not a quota for RAM, filesystem allocated blocks, external logs or
external backups. Symlinks inside managed roots are refused. Out-of-band writes
that bypass Ember are outside the guarantee. Derived-index callbacks can fail
after the source record is durable; this emits a warning and requires index
repair. There is no whole-database rollback guarantee for derived indexes.

History scans, replay and quota usage scans are currently linear. Edge traversal
is bounded, but the whole request is not constant-cost. History is not deleted
automatically: a full store refuses writes. Bounded replay checkpoints,
compaction policy, adversarial outcome identity and long-running workload
calibration remain research/engineering follow-ups.

## Validation

Tests exercise the real Rust backend on isolated temporary stores: restart,
lost acknowledgements, corrections, namespace scope, channel separation,
activation bounds, typed pair direction, context budgets, quota admission,
replacement peaks and subprocess crash recovery. The smoke script exercises
actual HTTP transport. This does not substitute for your agent's live workload.

Local validation on 2026-09-24: **552 tests passed** with the rebuilt native
engine (Python 3.12, Rust 1.98.1). The actual HTTP MCP + REST smoke test passed.
No production/user memory store was used.
