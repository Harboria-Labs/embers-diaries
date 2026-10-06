# Consolidated Rust research release — implementation report

Branch: `codex/ember-split-feedback-candidate04`.
Model: `ember-fur-activation-v1`.
Base commit: `a7f8f5340a9e4272de39b543d10179b7d2ac3999`.
The implementation commit is the commit introducing this report (`git log -1 --format=%H -- docs/consolidated-release-report.md`).

## What this means in plain English

The agreed feedback-to-activation chain is now implemented in Rust. Feedback first changes
what Ember knows about an experience. That produces usefulness U. U can strengthen or
weaken an existing query, and that query changes bounded activation. U cannot wake a memory
by itself, and none of this makes the memory true.

You start **one normal server**. It serves the new MCP tools, REST endpoints, Settings page
and live observer. The old Candidate 04 experiment is explicitly optional. No second server
is needed. Ordinary text recall remains available; use the new research recall tool to exercise
the consolidated equations instead of assuming ordinary recall tests them.

The implementation has local automated and browser evidence. Independent testing on the
researcher's deployed server remains a separate live-validation gate. This report does not
claim that a remote deployment was updated or its production data tested.

## Production × Research Map

| Track | Research/implementation status | Validation status |
| --- | --- | --- |
| Contract 01 context identity | Frozen behavior migrated to native validation | Existing regression suite passes |
| Contract 02 orientation | Existing lexical/one-hop policy migrated to Rust | Existing regression suite passes; no semantic resolver invented |
| FUR identities, conflicts, corrections, merge/split | Native reducer authoritative | Historical Python golden parity and regression coverage |
| U / N_eff | Native projection authoritative | Large duplicate/correction loops and façade/replay tests |
| Typed directional W | Native projection, visible | Direction isolation tested; retrieval coupling deliberately deferred |
| U→Q′→activation | Closed equations implemented natively | Random/grid/recovery and integration tests |
| Research Settings | Native validation, durable revisions, normal-server page | REST/MCP and real-browser checks |
| Observer | Existing SSE plus committed model fields | Local raw SSE, no healthy polling, restart/session/namespace checks |
| Independent live deployment | Ready to rebuild/restart this test branch | Pending researcher/connected-agent run |
| W→Pairing Matrix, semantic context transfer, SPRT | Outside this release | Not implemented or claimed |

## Exact files

### Rust

Added:

- `rust/ember_core/src/domain/mod.rs`: operation boundary, context invariants, retry identity, canonical JSON, explicit epistemic projection.
- `rust/ember_core/src/domain/fur.rs`: policy validation, target/experience identity, reductions, U/N_eff/W, decision transitions and replay.
- `rust/ember_core/src/domain/dynamics.rs`: configuration, recovery proof conditions, modulation, activation, stage, bounded state/admission.
- `rust/ember_core/src/domain/orientation.rs`: deterministic Contract 02 eligibility, signals, ties, one-hop grouping and limits.
- `rust/ember_core/tests/research_domain.rs`: seven native test families and seeded property loops.

Changed:

- `rust/ember_core/src/lib.rs`: PyO3 `domain_call`, durable-write context validation, canonical sorted hashing preserved.
- `rust/ember_core/Cargo.toml`, `Cargo.lock`: JSON insertion-order preservation for reducer parity and correctly rounded float round trips for exact retries. Historical record hash ordering remains canonical/sorted.

### Python façades, routing and packaging

Added:

- `embers/core/domain.py`: serialization-only native bridge; no Python numerical fallback.
- `embers/integration/consolidated.py`: source/auth checks, config/activation journal IO, external tokenization, context rendering.
- `embers/integration/research_schema.py`: human-readable field descriptions/default labels; native validation owns semantics.
- `embers/api/research_routes.py`: normal-server settings, validate and recall routes/page.

Changed:

- `embers/core/primary_context.py`: native context validation façade.
- `embers/cognitive/usefulness.py`: original Python numerical reducer removed; immutable journal adapter calls Rust.
- `embers/integration/memory_protocol.py`: native context-write check; legacy discovery version label.
- `embers/integration/orientation.py`: index/tokenizer/record IO façade around native policy.
- `embers/integration/server_memory.py`: FUR enabled normally; historical Candidate 04 registration opt-in.
- `embers/integration/context.py`: explicit truth labels, never confidence>.8 ⇒ verified.
- `embers/db.py`: explicit normal status projection; named legacy status reader retained.
- `embers/api/session_gate.py`: mounts research routes on the same app.
- `embers/mcp/tools.py`, `embers/mcp/server.py`: three new discoverable tools and dispatch.
- `embers/mcp/feedback_surface.py`: historical bias tools clearly labelled LEGACY.
- `pyproject.toml`: packages all API HTML assets, including Settings and observer.

### Settings and observation

- Added `embers/api/research_settings.html`.
- Changed `embers/api/observer.html`, `embers/api/visualizer.html` to inspect committed native dynamics.
- Changed `embers/integration/research_observer.py`, `embers/integration/usefulness_service.py` to project native dynamics/report transitions and observed reactivation.
- Added `examples/consolidated_release_check.py` for normal-server browser/network validation.
- Added `docs/research-settings.png`, `docs/consolidated-observer.png`, `docs/consolidated-browser-proof.json` as actual fictional-test evidence.

### Tests and documentation

- Added `tests/test_consolidated.py` (eight tests), `tests/fixtures/fur_pre_rust_v1.json` (14 frozen original reducer cases).
- Changed `tests/test_server_memory.py` for current-default versus explicit-legacy startup.
- Changed `tests/test_promotion_engine.py` for explicit truth default and named legacy behavior.
- Added this report and `docs/consolidated-research-model.md` (equations, proofs, assumptions, domains, defaults, examples, limits, historical mapping).
- Updated `README.md` and `docs/feedback-candidate-04.md` to distinguish current and legacy paths.

Pre-existing local visualizer screenshot changes and the local store are not part of this release.

## Interfaces and using one server

Rebuild/install this branch's native extension using the existing Rust/maturin installation
workflow, then start your usual `python -m uvicorn embers.api:app` with your usual `EMBER_STORE`.
The required runtime tokenizer is installed with the package. It supplies token counts, not
learning mathematics. There is no new resolver server.

| Interface | Purpose |
| --- | --- |
| `GET /research/settings` | Settings editor shell |
| `GET /v1/research/settings/{namespace}` | Authorized config, policy, schema, model/config/journal revisions and derived guarantees |
| `POST /v1/research/validate/{namespace}` | Native dry-run validation; no writes |
| `POST /v1/research/settings/{namespace}` | Authorized config change, expected revision and reason |
| `POST /v1/research/recall/{namespace}` | Direct candidate scores + explicit elapsed → committed activation and bounded returned context |
| MCP `ember_research_settings` | Same config inspection |
| MCP `ember_research_configure` | Same authorized config commit |
| MCP `ember_research_recall` | Same consolidated recall |
| Existing FUR tools/routes | Same reports/corrections, now native decisions |
| Existing `ember_visualize` and observer SSE | Same read-only agent-following link and transport |

Existing agent/token or session authentication is reused. Namespace read/write permissions
are preserved. Only configured FUR decision authority can save policy/configuration. An
observer capability cannot configure, recall or submit feedback. Settings exposes `can_edit`.

Example new research recall arguments (substitute an actual saved memory ID):

```json
{
  "namespace": "research-run",
  "query_id": "unique-query-1",
  "context": "mathematics",
  "direct_scores": {"MEMORY_ID": 0.8},
  "elapsed": 1,
  "format": "structured"
}
```

Use the same query_id and input to retry safely. Changing the input under the same ID is
rejected. A genuinely new step requires a new query_id. Scores are explicit agent/integration
inputs; this release does not invent a new semantic scorer. The current endpoint sets Q=d.

To edit, read settings first, retain the complete returned `config` and `policy`, change the
intended field, and submit `expected_revision=journal_revision`, `request_id` and a reason.
The browser performs this workflow. Invalid alpha=.99 fails recovery validation with zero
writes. The native default alpha limit is approximately .7952586206896552.

## Canonical mathematics and configuration

All equations, variables, ranges and proof sketches are in the model document. The exact
implemented chain is:

- U = epsilon_U + (u_max−epsilon_U)(kappa_U u0 + P)/(kappa_U + N_eff).
- W = (kappa_W w0 + P_pair)/(kappa_W + N_eff_pair), ordered typed pairs only.
- B=2U−1; Q′=Q[1+alpha B].
- u=rho Q′; v=rho[(1−d)+delta].
- A*=epsilon_A+(1−2epsilon_A)u/(u+v).
- activation_next=A*+(activation−A*)exp[−(u+v)elapsed]; latent=1−activation.

Defaults U/W prior=.5, strength=4; severity +1/−1/−2; alpha=.25; floor=.02;
rho=1; delta=.05; threshold=.4; strong Q/d=.8. Default recovery equilibrium .69764706,
crossing bound .96790373 explicit model units. Rust computes the strict alpha limit from
these parameters. Configuration is not silently clipped. The full schema and budget table
are recorded in the model document and available through the API.

Corrections change future query dynamics without changing the last committed activation.
Old bias eta/mu and bias-in-opposition are absent from this path. Old journal formats are
not reinterpreted as new state. `candidate-04-kernels-v1` and `candidate-04-recall-v1` remain
explicit historical/replay code, only registered with `EMBER_ENABLE_LEGACY_CANDIDATE04=1`.
Ordinary query-text discovery remains `legacy-query-discovery-v1`, with legacy confidence/
access behavior, and is not advertised as the consolidated activation path. No C++ is added.

## Validation evidence

### Native tests

Seven native test functions passed, containing:

| Test family | Cases |
| --- | ---: |
| Random activation boundedness/conservation/monotonicity/neutrality/zero query | 120,000 |
| Random valid strong-cue recovery profiles | 24,000 |
| Cartesian boundary grid | 60,480 |
| Duplicate plus identical-request retry | 12,000 |
| Correction and recomputation | 12,000 |
| Conflict, UNUSED, group, direction, merge/split | Targeted scenarios |
| Repeated excitation/relaxation, invalid config, context/truth | Targeted scenarios |

These are seeded deterministic property loops, not proof-assistant verification. Both the
mathematical assumptions and finite numerical scope are documented; arbitrary changing
feedback/query sequences are bounded but not promised to converge.

### Python and persisted integration

Final full-suite result: **650 passed, 1 dependency deprecation warning, 49.63 seconds**.
The warning concerns Starlette’s test-client/httpx integration, not a failed Ember test.
The relevant focused collection contains 77 tests across Contract 01, Contract 02, FUR, consolidated integration and observer.
Eight consolidated tests cover exact native/façade parity, same-event retry, correction
continuity, reopen/replay, config CAS/retry/restart, budget enforcement, no access/W coupling,
truth separation, observer read-only projection, REST/MCP and original reducer parity.

The historical fixture was generated from the actual base-commit Python implementation,
whose SHA-256 is embedded in the fixture. Fourteen cases compare report/experience/policy/
transition outputs and full U/W projections exactly. The old executable reducer is not copied
into the runtime as a competing authority.

### Browser and raw transport

`docs/consolidated-browser-proof.json` records an actual headless browser against the
normal `embers.api:app` with a temporary fictional store:

- invalid config rejected without file changes;
- valid config committed at revision 1;
- Settings mobile width fit without horizontal scrolling;
- feedback revision 2 and activation revision 3 arrived over raw SSE;
- activation displayed after approximately **0.022 seconds** in the recorded run;
- browser dynamics exactly equal the committed native response;
- identical query retry returns the identical response;
- **zero periodic snapshot GETs over 6.1 seconds** while healthy;
- **zero observer-caused memory file changes**;
- zero JavaScript errors; credentials absent from captured SSE.

This latency is a local observation, not a production SLA. Screenshots are adjacent to this
report. The existing `examples/research_observer_check.py` also passed on this implementation:
same browser follows A→B→A across two sessions, no manual namespace input, 6.2 seconds with
zero healthy snapshot GETs, no cross-agent payload leakage, unchanged memory files, server
restart catch-up and capability revocation. That run observed first push in .0503 seconds.

The new event example is included verbatim in the raw SSE proof. It shows U=.6, N_eff=1,
Q=.8, alpha=.3 after the Settings test, Q′=.848, activation_before=.02,
equilibrium≈.761421, activation≈.514129, latent≈.485871 and ACTIVE/reactivated=true.
No UI equation reconstruction is used.

## Known limitations and deliberate deferrals

- Independent deployed-server validation is still required; no remote server is restarted by this release.
- Explicit candidate IDs/direct scores and explicit model time are required by the new path; ordinary text recall is not automatically replaced.
- No W-driven neighbor expansion, SPRT, context transfer/generalization, semantic duplicate inference or C++.
- Structural experience grouping cannot prove shared underlying outcomes across unidentified agents.
- Bounded activation cache is per context; eviction resets that cache entry to the floor later. Durable memory/FUR evidence remains. This is not a global byte bound on all journal/context storage.
- Token budgets depend on the selected external tokenizer matching the consuming agent; default is cl100k_base.
- Observer projections retain their existing 100-item/32-KiB bounds and explicitly omit oversized details. Raw journal remains authoritative.
- Floor changes after activation require an explicit migration, which is not implemented. Settings rejects them.
- Legacy confidence/access/promotion subsystems remain outside the new path. Explicit truth projection prevents this path from treating confidence or missing status as truth; it is not a complete truth-learning rewrite.
- Multi-platform floating-point bitwise equivalence is not claimed. Correct JSON round trips and persisted same-event retries are tested on the current build.
