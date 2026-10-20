# Consolidated research model: ember-fur-activation-v1

Status: experimental implementation, not a claim of field validation or new mathematics.
This document specifies the closed research equations implemented by the Rust core.
Tests provide finite numerical evidence; the proof sketches below state their assumptions.
The next research task, W-driven pair retrieval, is deliberately excluded.

## Authority and execution boundary

| Responsibility | Authoritative source | Language |
| --- | --- | --- |
| Opaque context identity, context-write conflict, explicit epistemic projection | `rust/ember_core/src/domain/mod.rs` | Rust |
| Native durable write context validation, canonical record hashes, PyO3 boundary | `rust/ember_core/src/lib.rs` | Rust |
| Contract 02 eligibility, lexical ranking/ties, one-hop grouping and limits | `rust/ember_core/src/domain/orientation.rs` | Rust |
| Experience identity, grouping, consensus, correction, merge/split, replay, U, N_eff, directional W | `rust/ember_core/src/domain/fur.rs` | Rust |
| Configuration/recovery validation, query modulation, activation, stage, admission | `rust/ember_core/src/domain/dynamics.rs` | Rust |
| JSON bridge, no numerical fallback | `embers/core/domain.py` | Python |
| Immutable journal persistence, identity/source authorization, commit then notification | `embers/cognitive/usefulness.py` | Python |
| Configuration/activation journal IO, external tokenizer, source reads and rendering | `embers/integration/consolidated.py` | Python |
| Index/tokenization IO around native orientation | `embers/integration/orientation.py` | Python |
| API/MCP, authentication, SSE, bounded public projections | API/MCP/integration adapters | Python |
| Settings editor, graph, animation, inspection | `embers/api/*.html` | JavaScript/HTML/CSS |

The native domain receives explicit state, clock inputs and authenticated-actor facts. It does
not read clocks, credentials, files, network or UI state. Persistence/authentication remain
integration responsibilities. The admission protocol asks Python only for exact token counts
of the proposed rendered context; Rust decides whether those counts fit every limit.

## State and identity

A namespace journal contains immutable relevance reports and decisions. A report is not an
independent experience. The exact projection key is `(target, context)`:

* memory: one ID;
* pair: ordered IDs plus `explains`, `requires`, `warns_about`, or `alternative`;
* group: sorted IDs, with no individual numerical credit.

Context is opaque, including case and whitespace. Null is legal; blank strings are rejected.
The write path never infers, canonicalizes or duplicates context. Supplying a context that
conflicts with the same write's stored context is rejected. Feedback/recall context does not
rewrite a memory's primary home. All journal/session/namespace authorization still applies.

Experience matching preserves the previous FUR algorithm: explicit matching experience ID;
otherwise an authorized attested source/value identity (including retained aliases); otherwise
same actor, target, exact context and session within the configured creation-time window.
Structural grouping is a practical fallback, not proof of shared causal origin. Unattested
cross-agent reports can still become separate experiences. A split identity with several live
matches requires explicit resolution; it is not silently recombined.

An experience is accepted when its non-UNUSED reports agree. Conflicting types make it
unresolved; majority vote does not resolve it. An authorized correction replaces the effective
decision and recomputes projections. A subsequent substantive report reopens a manually
resolved experience; an UNUSED report does not undo that manual decision. Merge/split
conserves report membership exactly once, deactivates parent experiences and recomputes
children. It need not conserve numerical evidence mass: that mass depends on the corrected
experience structure. Historical reports and decisions remain in the journal.

## Equation 1: evidence-derived usefulness and pair weights

For an exact memory/context key, let E be its active accepted experiences excluding UNUSED.
For e in E, let s_e be its configured severity, and y_e=1 for CONTRIBUTED and 0 otherwise.

\[
P=\sum_{e\in E}s_e y_e,\qquad N_{\rm eff}=\sum_{e\in E}s_e,
\qquad
U=\epsilon_U+(u_{\max}-\epsilon_U)\frac{\kappa_Uu_0+P}{\kappa_U+N_{\rm eff}}.
\]

Unresolved experiences and UNUSED contribute zero; groups have no U or W projection.
`N_eff` is severity-weighted effective accepted evidence mass. It is neither a report count
nor an estimate of statistical independence. All quantities are dimensionless.

Defaults: epsilon_U=0, u_max=1, kappa_U=4, u0=.5; contributed=1, irrelevant=1,
misleading=2. Valid ranges: `0 <= epsilon_U < u_max <= 1`, priors in [0,1], strengths
and severities finite and positive, misleading severity greater than irrelevant severity.

For an ordered typed pair, PAIR_HELPED supplies positive mass and PAIR_IRRELEVANT negative mass:

\[
W_{ij,c}=\frac{\kappa_Ww_0+P_{ij,c}}{\kappa_W+N^{pair}_{\rm eff}}.
\]

Defaults kappa_W=4, w0=.5. W is dimensionless and in [0,1]. Different directions and
relation types are distinct keys. W is evidence, not the stored graph's weight or an old
Candidate 04 signed pair value. W does not expand retrieval in this release.

**Bound proof.** Each s_e>0 and y_e in {0,1}, hence 0<=P<=N_eff. With kappa>0 and prior
in [0,1], the numerator lies between zero and the denominator. The normalized fraction
lies in [0,1]; the affine U map lies in [epsilon_U,u_max]. Overflow is rejected.

**Duplicate proof.** Repeating an agreeing report within the same experience changes its
report list, not E, s_e or y_e. P, N_eff, U and W are unchanged. An identical request ID and
fingerprint returns its original committed event before any transition. A reused request ID
with different input is rejected. Unidentified repeats outside grouping rules are not covered
by the same-experience premise.

**Correction proof.** Derivation is a fold over the current active experience structure, not
an accumulated reinforcement counter. Replacing the decision changes that experience's
one effective contribution; the superseded contribution cannot remain as an extra bump.
Merge/split similarly recomputes from active children. Replay applies committed replacements
in journal order, preserving the same experience insertion order and floating-point sum order.

**Stability qualification.** U and W are bounded for any valid finite history. If accepted
mass tends to infinity and P/N_eff converges to p, the normalized fraction converges to p.
Arbitrary changing or adversarial feedback need not converge; boundedness alone is not
convergence. There is no invented theorem promising convergence for all feedback streams.

Examples: no evidence gives U=.5; one helpful experience gives 3/5=.6; one irrelevant
experience gives 2/5=.4; correcting the helpful experience to misleading gives 2/6=1/3.
Ten thousand agreeing reports for one identified experience still give .6, not 1.
One helpful A→B report gives W_AB=.6, while B→A remains at its prior .5.

Implementation: `fur.rs::{policy,target,apply,consensus,derive,reduce}`.
Tests: Rust duplicate/correction and structure suites; `tests/test_usefulness.py`;
`tests/test_consolidated.py`; historical fixture `tests/fixtures/fur_pre_rust_v1.json`.

## Equation 2: usefulness changes query pressure

\[
B=2U-1,\qquad m=1+\alpha B,\qquad Q'=Qm.
\]

U in [0,1], Q in [0,1], alpha in [0,1), with the stricter recovery constraint below.
B, m, Q, Q' are dimensionless. Q' may exceed 1; it must not be clipped to 1. Default
alpha=.25. B is derived, never independently learned or persisted as a bias counter.
The current direct-score endpoint supplies Q=d from each supplied direct score. The native
single-step function also accepts separate Q and d for deterministic research tests.

**Bounds and neutrality.** -1<=B<=1 implies 1-alpha<=m<=1+alpha, hence
(1-alpha)Q<=Q'<=(1+alpha)Q. At U=.5, B=0 and Q'=Q. At Q=0, Q'=0 for every U.
For fixed nonnegative Q, dQ'/dU=2alpha Q>=0.

Example Q=.8: U=.5 gives Q'=.8; U=.6 gives .84; U=0 gives .6; U=1 gives 1.
Usefulness cannot generate excitation from a zero query.

Implementation: `dynamics.rs::step`; prospective feedback impacts in `fur.rs::apply`.
Tests: 120,000 seeded random cases, 60,480 boundary grid cases, exact façade parity.

## Equations 3–5: bounded activation and its exact time step

Canonical state name: **activation** (A). **latent** is the derived complement L=1-A.
Historical H/heat/A refer to this same state, never independent numeric stores.

\[
u=\rho Q',\quad v=\rho[(1-d)+\delta],\quad k=u+v,
\]
\[
A^*=\epsilon_A+(1-2\epsilon_A)\frac{u}{u+v},
\]
\[
A_{next}=A^*+(A-A^*)e^{-k\Delta t},\qquad L_{next}=1-A_{next}.
\]

A in [epsilon_A,1-epsilon_A], 0<epsilon_A<.5, d in [0,1], rho>0, delta>0,
0<=elapsed<=max_elapsed. rho has units per explicit model-time unit; u and v are rates.
All other state quantities are dimensionless. The caller supplies elapsed; this version does
not silently interpret wall-clock seconds as model time. A retry never advances model time.
Defaults epsilon_A=.02, rho=1, delta=.05, max_elapsed=1,000,000 model units.

**Derivation.** The bounded flow is
`dA/dt = u(1-epsilon_A-A) - v(A-epsilon_A) = k(A*-A)`.
For fixed inputs over the step, solving this first-order linear equation yields the exact
exponential transition. Rust evaluates `A + (A*-A)*(-expm1(-k*elapsed))` for small-step
accuracy. A final interval clamp only absorbs floating-point roundoff after input validation;
invalid configurations are rejected, never silently clamped into validity.

**Invariant interval and conservation.** Since u>=0, v>0, 0<=u/k<1, A* lies in the
closed safe interval. The step is a convex combination of A and A*, because exp(-k dt)
lies in [0,1]. Therefore A_next stays in that interval. Defining L=1-A establishes A+L=1
without a second independent state transition. Finite-machine tests use explicit tolerances
where necessary; these real-number proofs are not a machine-checked floating-point proof.

**Constant-input convergence.** |A_next-A*|=exp(-k dt)|A-A*|. With positive cumulative
time and fixed k>0 this tends to zero. Arbitrarily changing queries need not converge to a
single equilibrium. Each step remains bounded.

**Zero-query relaxation.** If Q=0 then u=0 independently of U and A*=epsilon_A. Thus
A_next=epsilon_A+(A-epsilon_A)exp(-v dt). Activation cannot rise. If elapsed time tends
to infinity it relaxes to the floor because v>=rho delta>0. In particular usefulness alone
cannot reactivate a latent memory. Reading/rendering does not call this transition.

**Monotone usefulness.** Fix Q,d,configuration,initial A and elapsed. Raising U raises u
and leaves v unchanged. At any A in the invariant interval, increasing u increases the vector
field by delta_u(1-epsilon_A-A)>=0. Scalar comparison (or the nonnegative forcing in the
sensitivity equation) gives A_high(t)>=A_low(t). The opposition term has no usefulness input.

**Correction continuity.** FUR decisions do not overwrite `activation_state`. They recompute
U, which affects the next query's u. The next step begins at the last committed activation,
not a retroactively modified historical A. Configuration changes also preserve existing A;
floor changes after any activation state exists are rejected pending an explicit migration.

Example from floor .02, Q=d=.8, elapsed=1: U=.5 gives activation≈.495474; U=.6 gives
≈.511078; U=0 gives ≈.408011. With U=.6, u=.84, v=.25, A*≈.759817.
These are approximations for explanation; persisted values are native f64 values.

Implementation: `dynamics.rs::{step,batch}`. Tests: random/grid/recovery/cycles Rust suites;
restart, retry, correction continuity, no access reinforcement in `test_consolidated.py`.

## Equation 6: strong-cue recovery and lifecycle floor compatibility

ACTIVE means activation>=T; LATENT means activation<T. Require epsilon_A<T<1-epsilon_A.
A latent record is not deleted or excluded from direct candidate discovery by this threshold.
The consolidated path does not call historical reflection/confidence decay to determine A.

For sustained Q>=Q_min>0 and d>=d_min with fixed configuration, the worst usefulness is
U=0, giving u_min=rho Q_min(1-alpha) and v_max=rho[(1-d_min)+delta]. Define

\[
r=\frac{T-\epsilon_A}{1-2\epsilon_A},\qquad
\alpha_{limit}=1-\frac{r}{1-r}\frac{(1-d_{min})+\delta}{Q_{min}}.
\]

Require alpha<alpha_limit, strictly. This comes from solving A*_worst>T:
`u_min/(u_min+v_max)>r`, then `u_min>r v_max/(1-r)`. rho cancels from the equilibrium
condition; it controls reaction speed, not reachability. If the bound admits no alpha>=0,
the declared recovery profile is invalid.

For initial A0<T, constant worst-case inputs cross T at

\[
t_{cross}(A_0)=\frac{1}{u_{min}+v_{max}}
\log\frac{A^*_{worst}-A_0}{A^*_{worst}-T}.
\]

Starting at the floor maximizes this time over allowed latent states. Comparison with the
worst vector field extends the bound to sustained stronger Q,d and any U in [0,1]. At the
strict boundary A*=T, finite-time crossing from below is not guaranteed; this is why equality
is rejected. Near the boundary crossing time grows large. The UI displays this limitation.

Default T=.4, Q_min=.8, d_min=.8 yields alpha_limit≈.7952586206896552. At alpha=.25,
worst equilibrium≈.6976470588235294 and crossing time from floor≈.9679037317274952 model
units. Rust computes these numbers from config, never from a hard-coded .79526 bound.
Validation rejects nonfinite inputs, invalid ranges, overflowing rates and nonfinite guarantees.
Recovery may require several queries/time steps; max_elapsed is a step bound, not a claim
that every valid profile crosses within one step.

Threshold compatibility is established for this new version's own activation floor and
admission path. It is not a proof that legacy reflection thresholds can be substituted into
this model. No legacy-confidence-to-activation conversion is performed.

Implementation: `dynamics.rs::validate_config`. Tests: 24,000 randomized strong-cue cases,
alpha-boundary grid, invalid-config tests, Settings API/browser validation.

## Truth decoupling

The relevance state is R=(reports,experiences,U,N_eff,W,activation); epistemic/source state
is E. A relevance operation is `(R,E) -> (F(R,feedback),E)`. Python persists a new RAW
journal record; it does not write the source memory. Native relevance/activation functions
have no correctness updater. By induction, any sequence of relevance operations leaves E
unchanged. Thus a continuous relevance parameter, where differentiation is meaningful,
has partial derivative dE/df_rel=0; for categorical feedback the exact equality E'=E is the
stronger appropriate statement.

Explicit projection `explicit-epistemic-v1` reads `_status`, then `verify_status`, explicit
verification annotations, with an open conflict overriding to contested. Missing/unknown
status is unverified, not VERIFIED. Confidence, U, Q, access count and activation are not
inputs to this projection. Ordinary coarse memory status maps unknown to provisional.
This is a display/reader boundary, not a new truth-learning model. Historical promotion and
verification workflows remain; no SPRT is invented. Correctness evidence does not enter
FUR unless separately submitted as an explicit relevance report.

Tests: native context/truth test, explicit-status test, source hashes around FUR operations,
observer read-only file-hash comparisons and legacy promotion regressions.

## Deterministic admission, discovery and bounds

The new endpoint accepts a bounded map of memory IDs to direct scores in [0,1]. It does not
invent query embeddings or claim exhaustive semantic discovery. Contract 02 and existing
search adapters remain available to find candidate IDs. Core scores candidates by Q'×activation,
then ID. A rotating previously latent candidate with positive Q is considered before active
candidates; active candidates follow rank order. Reservation is an inspection opportunity,
not permission to exceed the token budget. Oversize records may be omitted. No W or graph
weight is consulted. Each direct candidate is its own neighborhood in this release.

The tokenizer is `tiktoken`, selected by `EMBER_TOKEN_ENCODING` (default `cl100k_base`).
Exact counts include the rendered wrapper, metadata and separators. Rust checks per-memory,
per-neighborhood, total token and result-count limits. This does not claim an arbitrary
agent/model tokenizer match. Select and test the intended encoder when deploying.

Activation is retained per exact context. Each explicit step relaxes retained nonqueried
memories with zero Q; state is bounded by recent query sequence, then ID. Evicted activation
cache entries restart at the floor when seen again; their durable source memory and FUR
experience evidence are not deleted. The guarantee applies to retained/currently stepped
state, not an unbounded history cache. Namespace journals and the number of contexts are
not globally byte-capped by these admission settings.

Contract 02 preserves its lexical baseline: Python full-text index supplies a capped shortlist;
Rust ranks by descending distinct clue matches, then hint matches, then matching fields,
then ID. It expands bounded outgoing typed stored relationships by one hop, groups by stored
subject/context (including unset), applies record/group/preview/response-byte limits, and
returns individual signals. It does not canonicalize, semantically merge or learn contexts.
Changing enabled signals does not make the upstream full-text shortlist exhaustive.

## Configuration schema

Model/time labels are fixed structural metadata. All editable fields are experimental.
Unknown fields are rejected; saving requires decision authority, reason, unique request ID
and expected journal revision. Settings reads/validation do not commit events. Config changes
commit atomically; both usefulness-only configure and research configure increment config
revision. Replay retains the committed config. Settings may become stale during agent work;
a stale revision is rejected and the editor asks the researcher to reload.

| Field | Default | Valid domain / effect |
| --- | --- | --- |
| policy.u0 / policy.w0 | .5 / .5 | [0,1]; reproject evidence |
| policy.kappa_u / policy.kappa_w | 4 / 4 | finite >0; reproject evidence |
| policy.epsilon / policy.u_max | 0 / 1 | 0<=epsilon<u_max<=1; retained FUR schema, not UI controls |
| policy.severity.contributed | 1 | >0; reproject |
| policy.severity.irrelevant | 1 | >0; reproject |
| policy.severity.misleading | 2 | >irrelevant; reproject |
| policy.cluster.session_window | 1800 seconds | [0,86400]; future grouping |
| policy.cluster.time_window | 60 seconds | [0,86400]; future grouping; zero disables |
| alpha | .25 | [0,computed recovery limit), also <1 |
| epsilon_a | .02 | (0,.5); frozen once activation exists |
| rho | 1 | finite >0 per model unit |
| delta | .05 | finite >0; recovery validation |
| threshold | .4 | floor<T<worst strong-cue equilibrium |
| q_min / d_min | .8 / .8 | (0,1] / [0,1]; recovery validation |
| max_elapsed | 1000000 | finite >0 model units |
| max_seeds | 32 | integer 1..512 |
| max_state_memories | 2048 | integer 1..100000; >=max_seeds; per context |
| max_results | 10 | integer 1..100 |
| token_budget | 2048 | integer 1..1000000 |
| memory_token_cap | 512 | integer 1..1000000; <=token_budget |
| neighborhood_token_cap | 1024 | integer 1..1000000; <=token_budget |

Settings metadata lives in `research_schema.py`; defaults/validation/calculations live in Rust.
The UI formats server numbers for readability and offers raw values in tooltips. It does not
reimplement equations. Floor migration is deliberately unsupported; no hidden restart reset.

## Versioning, historical mapping and rejected alternatives

| Historical path/name | New treatment |
| --- | --- |
| Candidate 04 learned memory_bias | Superseded by derived B=2U−1 on the new path |
| Bias update eta / clipped signed update | Absent from the consolidated model |
| mu / bias in opposition | Removed; alpha modulates Q only |
| A, H, heat / L | `activation` / derived `latent`; H retained only as observer alias |
| Candidate 04 signed pair state | Explicit legacy experiment, not W |
| `cognitive/feedback_dynamics.py`, `cognitive/feedback_replay.py`, `cognitive/feedback_durable.py`, `integration/candidate_recall.py` | Retained historical/replay implementation; server opt-in `EMBER_ENABLE_LEGACY_CANDIDATE04=1` |
| Legacy namespace query recall and confidence decay | Retained compatibility discovery; inspectable response labels `legacy-query-discovery-v1`; not this activation model |
| Missing-status-is-verified | Available only through explicitly named `legacy_memory_status`; normal reader uses explicit projection |
| Renderer confidence>.8 ⇒ VERIFIED | Removed from normal context formatting |
| FUR journal `ember.usefulness-evidence.v1` | Readable unchanged; original replacement events replay natively; new events identify `ember-fur-activation-v1` |

No old bias/activation/signed-edge journal is silently converted into FUR or new activation.
The normal server starts without requiring the old Candidate 04 context/tokenizer journal.
The new tool is `ember_research_recall`; ordinary text recall is not secretly rerouted.

Rejected alternatives remain rejected: direct Q×U suppresses neutral priors and can erase
query pressure at U=0; a second N_eff confidence gate double-counts evidence shrinkage;
tanh adds an unapproved nonlinear model; bias in v creates an additional usefulness pathway.
No C++, SPRT, context generalization or W→pair retrieval is included.

## Observation guarantees and limits

Durable RAW commit precedes subscriber notification. The existing authenticated SSE
transports committed revisions, not an independent state database. Both namespace and
agent-observer views remain. Observer access grants no mutation authority. UI reads do not
invoke recall, advance model time or call FUR. Actual activation fields are emitted only when
observed; U is never relabeled as heat. Feedback modulation is labelled prospective, based on
the last recorded Q, and does not claim a retroactive activation change.

A dynamics row contains Q, direct, U, N_eff, B, alpha, multiplier, Q_prime, u, v,
activation_before, equilibrium, activation, latent, threshold, stage, reactivated, elapsed,
context, memory ID and model version. Report events include report/experience IDs, resolution,
report count, duplicate indicator and effective-mass-change flag. Transition projections carry
U/W and N_eff before/after plus native modulation when applicable. Source session credentials
remain hashed references; credentials are not streamed.

Existing observer bounds still apply: up to 100 projected memories/dynamics/changes per event
and a 32 KiB projected-event budget; oversized details are explicitly omitted with event
identity retained. This release does not claim every large candidate set fits one visible event.

## Reproducible validation

```
cargo test --release --manifest-path rust/ember_core/Cargo.toml --test research_domain
python -m pytest -q
python examples/consolidated_release_check.py
python examples/research_observer_check.py --proof /tmp/observer-proof.json
```

Build/install the local native extension before Python tests (for example `maturin develop
--release`). The standalone Rust domain test imports the pure module and does not require a
Python interpreter inside its mathematical loop. Seeded property loops are reproducible;
there is no formal proof assistant or shrink-based property framework in this release.
See `consolidated-release-report.md` for recorded outcomes and exact file changes.
