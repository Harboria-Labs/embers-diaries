# Explicit feedback and Candidate 04: implementation progress

Status: experimental, report-only API integration plus an in-process canonical relevance journal. No automatic learning in live recall or deployment.
Base reviewed: c63f7338bb52ecb1a3f24d5a07a019cfc5a0bc19.

## Included

- Version 2 feedback explicitly distinguishes relevance from correctness through
  the existing SDK, REST and MCP feedback interfaces.
- Agent identity comes from existing authentication, not a body attribution.
- Durable target identity must agree with the feedback target.
- Context and underlying outcome IDs are recorded, without inventing them for
  legacy reports. The target record ID identifies the immutable memory version.
- Mixed-channel fields, ambiguous outcomes, non-finite values and unsupported
  v2 fields are rejected. Unknown context details may remain absent.
- Historical version 1 serialization retains its original fields.
- Pure numerical kernels implement clipped learning, signed pair state to
  nonnegative weight, and exact active/latent transfer.
- Regression tests cover schema validation, legacy compatibility, SDK/REST/MCP
  storage, channel separation and mathematical bounds/recovery.

## Agent example

Submit to the existing POST /v1/memory/{memory_id}/feedback route, or pass these
fields with memory_id and authenticated session credentials to ember_feedback:

```json
{
  "schema_version": 2,
  "channel": "relevance",
  "outcome": "useful",
  "outcome_id": "run-204-result",
  "context_id": "training-debug",
  "context": {
    "task": "diagnose training failure",
    "goal": "finish training",
    "constraints": {"ram_gib": 8},
    "environment": {"build": "recorded-build"}
  },
  "signal": 0.5,
  "supporting_refs": ["run-204-log"]
}
```

This stores a report. It does not change activation, confidence or pair weights.
Explicit irrelevance can carry zero or negative signal; appearing without use
does not warrant a feedback event. Correctness uses correct, incorrect,
confirmed or contradicted, requires supporting_refs, and forbids signal.
References and agent claims are not automatically verified evidence.

Version 2 currently supports a single-memory report only. Pair/group attribution,
revision commands, idempotency keys and dependency fields are rejected rather
than silently accepted with missing semantics. Transport retries can still append
multiple reports. These reports MUST NOT be treated as independent learning.

## Mathematics included

In embers.cognitive.feedback_dynamics:

- bounded_update(x, s, eta) = clip(x + eta*s, -1, 1).
- pair_weight(z) = max(0, z), retaining z separately for reversibility.
- preview_memory_bias leaves bias unchanged for correctness and rejects legacy
  reports. It is a pure preview, not an authorized learning/replay pipeline.
- activation_step uses u=rho*q*(1+mu*b),
  v=rho*((1-d)*(1-mu*b)+delta), and equilibrium
  epsilon+(1-2*epsilon)*u/(u+v). It integrates the exact exponential over an
  explicit interval and returns (A, 1-A).

No production defaults are supplied. Time is in declared model units, not
implicitly wall-clock seconds. Inputs require 0<epsilon<0.5, 0<=mu<1,
delta>0, rho>0, 0<=d<=q<=1 and -1<=b<=1. Invalid or overflowing rates fail.

The clipped update stays in [-1,1]. Nonnegative u and positive v put the
equilibrium inside the activation floors; the exact update is a convex
combination of current state and equilibrium. Truth is absent from these
functions. This is a code-level separation property, not a proof that a future
caller cannot incorrectly route events. Arbitrary changing feedback is bounded,
but is not claimed to converge to a fixed value.

## Required before automatic learning or production use

1. Connect the in-process canonical journal to authenticated, namespace-scoped
   durable resolution commands. Preserve raw reports, resolve pending disputes,
   validate target versions and bind context identity before accepting decisions.
2. Durable projection generations, recovery and visible lag. Never learn by
   simply iterating over raw feedback_for results.
3. Integrate kernels into versioned experimental recall with exposure neutrality,
   lifecycle/floor checks and explicit truth/provenance rendering. Legacy recall
   and its access-based behavior are unchanged by this slice.
4. Implement total-store reservation/admission in the existing Rust-backed
   storage path, covering all writers and peak temporary usage. This slice
   makes no new disk/RAM quota guarantee.
5. Add bounded neighbor expansion and token planning; verify permissions,
   authentication/session boundaries and real-engine integration end to end.
6. Calibrate correctness evidence separately. No SPRT promotion is enabled.

## Validation

Tests were written and reviewed, but NOT executed in the authoring session:
the code execution workspace was unavailable. No Rust build or live Ember test
was performed. This change must remain draft until validation is available.

In a checkout with Python >=3.10 and the Rust toolchain:

```sh
python -m pip install -e ".[dev,api]"
python -m pytest tests/test_split_feedback.py tests/test_feedback_lifecycle.py tests/test_feedback_replay.py
python -m pytest
cargo test --manifest-path rust/ember_core/Cargo.toml
```

Use an isolated store for live acceptance testing. Do not merge based on the
presence of tests alone.

## Second slice: canonical relevance journal

Implemented in embers.cognitive.feedback_replay. This is an explicitly invoked,
in-process component, not a new public endpoint or a persistent transaction
manager. Existing feedback endpoints still store raw reports only.

- A RelevanceDecision assigns at most one unit of total absolute credit across
  memory versions and typed directed pairs. Pending groups/disagreements and
  retracted outcomes have no credit.
- Only configured resolver identities may submit decisions. The embedding
  caller must authenticate those identities; a string allowlist is not a
  substitute for transport authentication or namespace/target permission checks.
- One outcome ID has one effective head. Separate agent reports do not produce
  separate contributions unless the resolver incorrectly assigns different
  underlying outcome IDs; Ember cannot infer real-world identity by itself.
- Reusing a committed request ID with identical content returns its original
  receipt, including after correction. Changed content with that request fails.
  Identical current decisions are no-ops. No-op requests do not create receipts;
  after a later correction they may return a revision conflict on retry.
- Corrections require the current expected revision. An in-process lock serializes
  competing corrections. This does not provide inter-process consistency.
- Corrections retain immutable revision history and replace the effective
  contribution at the original outcome position. Replay starts from zero,
  applying the bounded kernels in that stable order.
- Dependencies reference exact earlier outcome revisions in the same scoped
  journal. Cycles and forward references are rejected. A changed parent makes
  dependent learning inactive until explicitly revalidated, transitively.
  Independent outcomes continue to contribute.
- project() returns detached memory biases, signed pair states, source generation,
  rates, model version and effective/inactive outcomes. It cannot write truth.
- from_history() validates and rebuilds typed in-memory history under supplied
  configuration. It is not serialization, disk recovery or model migration.
  Use the same recorded rates, scope and resolver policy for identical replay.

Example (the agent/resolver supplies the decision):

```python
from embers.cognitive.feedback_replay import (
    Credit, RelevanceDecision, RelevanceJournal,
)

journal = RelevanceJournal(
    namespace="memories", context_id="training-debug",
    authorized_resolvers=frozenset({"authenticated-resolver"}),
    memory_rate=0.25, pair_rate=0.25,  # experimental, not production defaults
)
decision = RelevanceDecision(
    outcome_id="run-204-result", status="accepted",
    credits=(Credit("memory-A-v1", 1.0),),
    report_ids=("stored-feedback-record-id",),
    reason="Memory A identified the observed failure cause",
)
journal.resolve(
    decision, actor="authenticated-resolver",
    request_id="resolution-request-1", expected_revision=0,
)
assert journal.project().memory_bias["memory-A-v1"] == 0.25
```

### Why replay matters

Five positive updates of 0.25 saturate at 1. If the first outcome is corrected
to negative, replay produces -0.25+0.25+0.25+0.25+0.25 = 0.75.
Simply subtracting the old update from the final clipped value gives the wrong
answer for many such histories.

### Remaining limits

History, outcome heads and request receipts currently grow in RAM; replay is
linear in effective outcomes and runs under a process-local lock. Do not use
this implementation as the production bounded-storage service. Durable atomic
commands, byte admission, checkpoints, recovery and live API/recall wiring are
the next integration work.

The tests added for this slice cover retries, conflicting request reuse,
saturated-history corrections, transitive dependency invalidation, revalidation,
pending group credit, typed pairs, context isolation, concurrent corrections,
history rebuild and invalid data. They remain unexecuted in the authoring
session because the execution workspace is unavailable.
