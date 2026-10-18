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

| Track | Research status | Implementation/production status | Live validation status |
| --- | --- | --- | --- |
| Contract 01 context identity | Frozen behavior; prior regression coverage | Native validation in this release | No new live check in Track 7 |
| Contract 02 orientation | Existing lexical/one-hop policy; no semantic resolver claimed | Migrated to Rust in this release | No new live check in Track 7 |
| FUR identities, conflicts, corrections, merge/split | Historical Python parity and regression coverage | Native reducer authoritative in this release | No new live check in Track 7 |
| U / N_eff | Duplicate/correction loops and façade/replay tests | Native projection authoritative in this release | No new live check in Track 7 |
| Typed directional W | Direction isolation tested; retrieval coupling deferred | Native projection visible in this release | No new live check in Track 7 |
| U→Q′→activation | Random/grid/recovery and integration tests | Closed equations implemented natively in this release | No new live check in Track 7 |
| Research Settings | Prior REST/MCP and browser checks | Native validation, durable revisions and normal-server page | No new live check in Track 7 |
| Observer | Prior local SSE, restart/session/namespace checks | Existing SSE plus committed model fields | No new live check in Track 7 |
| Track 6: ELLA replay and projection | Phase 1–2 mechanism and isolated persisted correctness established; append cost grows with history. 2026-10-15 focused source design favors verified warm-state reuse; durable journal-head/change detection and historical-tamper contract remain open (`track7_atlas_design_20261015/04_INCREMENTAL_ELLA_DESIGN.md`). | Existing authenticated journal/reducer unchanged; no replay optimization implemented | Isolated store only; no live ELLA performance validation |
| Track 7: conflict lookup, index and integrity | Phase 3 benefit on healthy fixtures; Phase 4 correctness contract followed by Batch 1 source/isolated tests. Final source/applied epoch and recovery evidence in `track7_atlas_design_20261015/12_BATCH1_IMPLEMENTATION_EVIDENCE.md`; independent audit remains Batch 3. | Batch 1 implemented in local candidate source: certified MasterIndex, CONFLICT candidates and conflict-only lock/CAS. Installed production native binary and live service unchanged; audit/reporting not implemented. | Isolated stores only, including staged native and killed-process tests; no live validation |
| Track 7: bounded memory and capacity | Initial LADC/FUR/Pairing/ordinary-recall source map; no general capacity limit established | No Track 7 optimization implemented | No live capacity or stress validation |
| Track 7: Batch 2 promotion/status | Approved status/outcome policy and focused baseline/regression evidence in `13_BATCH2_IMPLEMENTATION_EVIDENCE.md`; independent-process and real power-loss proof remain open | Implemented on local candidate branch: mapped CONFLICT status gate, locked final recheck, proposal CAS and retry identity; 126 isolated tests passed. No genuine human override authority; Batches 3-4 pending. | No live validation or deployment |
| Track 7: Batch 3 independent audit | Strict physical/index and report contract validated in isolated stores; see `track7_atlas_design_20261015/14_BATCH3_IMPLEMENTATION_EVIDENCE.md` | Candidate source implements scoped audit, durable reports, protected REST/MCP run/status and local-operator-only index recovery with re-audit. Automatic scheduling and ordinary-agent repair remain unavailable; Batch 4 pending. | No live validation or deployment |
| Track 7: Batch 4 ELLA verified reuse | Cold/warm and cross-process equivalence plus 5/10/20/40-event isolated measurements; see `track7_atlas_design_20261015/15_BATCH4_IMPLEMENTATION_EVIDENCE.md` | Candidate source now reuses a bounded verified process projection and incrementally reduces new events; full sealed journal replay remains authoritative. Existing native ELLA semantics unchanged. | No live validation or deployment; real power-loss and mixed-version behavior unverified |
| Track 7: Phase 5 cross-feature validation | Selected isolated groups: 309 passed/2 installed-native skips, plus 46 passed; final four-case Phase 5 file passed, and the two skipped cases passed on a compatible staged native build. The 50/150/300-memory probe held physical reads at 1 recall, 5 conflict and 7 promotion; healthy conflict/promotion still fingerprint every in-memory MasterIndex row per call. Cold open, cross-process catch-up and explicit reconciliation scan physical source. See `track7_atlas_design_20261015/16_PHASE5_VALIDATION_EVIDENCE.md`. | Validation-only tests, probe and evidence; no runtime source or installed native change. Per-query metadata work was disclosed in the approved Batch 1 physical-I/O contract, so no violation or index correction was demonstrated. Capacity remains unbounded. | No live deployment/validation; real power loss, mixed-version writers and production-scale load unverified |
| **Track 7 current milestone — four batches plus Phase 5** | **Local implementation and isolated validation complete** on the test branch through `fe0a001`; Batch 1–4 and Phase 5 evidence above remains authoritative for exact coverage and limits. No general capacity bound or Phase 6 claim. | **Complete at the local candidate milestone only**; no installed native, production service or live-store deployment is certified. Earlier dated pending rows are historical snapshots. | **Unverified:** live-agent validation, production-scale performance, mixed-version compatibility and power-loss durability. No push or deployment at this closure. |
| Targeted API authorization | Existing token, session and namespace ACL mechanisms confirmed; narrow API correction only | Candidate source checks maintenance WRITE ACL for every requested namespace and token-plus-session active ownership; isolated API security 20 passed and focused regression 128 passed/2 skipped; MCP/scheduler audit policy unchanged | No live validation |
| Independent live deployment | This release's connected-agent validation remains a separate gate | Track 7 made no deployment change | HTTP `/health` returned 200 during disk incident; functional validation not performed |
| W→Pairing Matrix, semantic context transfer, SPRT | Outside this release | Not implemented or claimed here | Not validated here |

### Track 7 research consolidation — 2026-10-13

- **ELLA replay (mechanism measured; optimization open).** Each authenticated update replays existing journal history. Loads repeat record-integrity verification and cross native reducer boundaries; later events embed accumulated state and increase encoded history. In the isolated persisted 50-report fixture, the last-five append median was **1,929 ms**. A correction and reopen reproduced epistemic state at revision **51**. See `track7_research_20261013/01_EXECUTIVE_SUMMARY.md` and `track7_phase2_20261013/{01_FINDINGS,05_CORRECTNESS_CHECKS}.md`.
- **Conflict lookup (cost measured; replacement unapproved).** The current lookup reads namespace records before filtering for conflicts. At 200 unrelated records, the reference made 214 record reads versus 9 for a research-only record-type prefilter on a healthy fixture. Across **99** Phase 3 comparisons, **10** candidate mismatches occurred. Stale type metadata, unrelated-record corruption, absent graph edges and untagged conflicts defeat the tested replacements. See `track7_phase3_20261013/{01_FINDINGS,05_COUNTEREXAMPLES}.md` and `summary.json`.
- **Index/integrity contract (decision open).** A nonempty stale master index can survive restart; read-time integrity checks cover records actually accessed. Source review found no explicit namespace-wide audit contract assigned to conflict lookup, but its present scan can raise on unrelated corruption. Preserve that observable behavior until a correctness decision is made. Define index validation and full-scan fallback before considering a type-indexed lookup. See `embers/index/master.py`, `embers/storage/store.py` and `embers/db.py`; Phase 4 was recorded in the research transcript, with no separate report file.
- **Operational gate.** C: reached **0 bytes free**. One inactive 212,859,557-byte installer download was removed; about **219 MiB** remained immediately afterward, and local HTTP health returned **200**. The full cause of disk decline is unknown. Suspend further persisted stress testing until operational headroom is stable; a later documentation preflight observed about 114 MiB free.

The smallest next research decision is whether a conflict query must fail on corruption in an unrelated namespace record. That answer sets the equivalence contract for index validation, fallback and any separate audit. Replay optimization is a separate investigation and must preserve journal authority, correction history and recovery semantics. No optimization is approved or implemented by this map update.

**Research correction, 2026-10-15:** The preceding Phase 4 question and incidental-error preservation instruction record the earlier, unresolved research state. The frozen Track 7 design boundary now assigns namespace-wide integrity checks to independent maintenance; ordinary conflict queries verify records they use, not unrelated memories. This is a research contract change only. The certified index, independent audit and ELLA warm-state designs remain unimplemented and need the generation/recovery proof obligations documented in `track7_atlas_design_20261015/07_DECISION_RECORD.md`.

**Existing-mechanism reconciliation, 2026-10-15 (research only):** Current source confirms native WAL record recovery, atomic supersession sidecars, immutable sealed versions, memory CAS, read-only `audit_write`, Promotion Engine admission modes and Conflict Engine review/status paths. These must be reused. The earlier proposed durable index-generation marker and ELLA head pointer are design alternatives, **not established prerequisites**: existing store locking, physical-ID enumeration, journal replay and index rebuild need focused evaluation first. A strict independent physical/index audit and observable result remain unimplemented; promotion/conflict review and `audit_write` have different responsibilities. See `track7_atlas_design_20261015/08_EXISTING_MECHANISM_RECONCILIATION.md`. No implementation or live status changed.

**Complete Track 7 upgrade contract, 2026-10-15 (research only):** Seven upgrades are ordered in four gated batches: MasterIndex trust, conflict retrieval/concurrency; promotion status alignment; independent audit/review; ELLA disposable verified-state reuse and cross-feature validation. The source-backed touchpoints, rollback and completion gates are in `track7_atlas_design_20261015/09_COMPLETE_UPGRADE_ROADMAP.md`; exact isolated regression and recovery requirements are in `10_REGRESSION_AND_RECOVERY_MATRIX.md`; unresolved policy choices are in `11_REUSE_AND_POLICY_DECISIONS.md`. Existing WAL, history, CAS, conflict/promotion engines and native ELLA reducer remain the foundation. This is **planning status only**: no Track 7 implementation, test execution, build, benchmark, store change or live validation was performed for this contract. The implementation/production and live-validation columns above remain unchanged.

**Batch 1 local implementation, 2026-10-16:** The existing MasterIndex now uses a pre-change source epoch and post-publication indexed epoch; startup/behind reconciliation is source-backed, while healthy conflict queries use existing CONFLICT type candidates without unrelated physical reads. Conflict mapping/status use shared lock and existing CAS. Source, tests and native Rust were modified locally; the installed binary, service, live store and ngrok were not. Baseline 97 passed; post-change isolated groups passed 115 plus 1 expected skip on the installed-extension compatibility path and 116 with a staged native build; final focused groups passed 73 plus 1 expected skip and 74 respectively. See `track7_atlas_design_20261015/12_BATCH1_IMPLEMENTATION_EVIDENCE.md` for fault/restart evidence and limits. Batches 2–4 and live validation remain open.

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


**Targeted API authorization correction, 2026-10-16:** The candidate source now checks existing namespace WRITE ACL for every requested POST /v1/maintenance namespace before any reflect/consolidate work; explicit malformed lists return HTTP 400. Token-plus-session authentication now validates active ownership before observation binding. Existing session-only authentication and unregistered-namespace compatibility remain. Isolated pre-edit baseline: 46 passed; post-edit API security: 18 passed. Further post-edit Batch 1/MCP reruns stopped when disk free space hit zero. Batch 1 implementation evidence remains isolated only; Batches 2-4 are pending. No live deployment, production native replacement or integrity-audit permission model is claimed.


**Resumed isolated validation, 2026-10-16:** With disk headroom restored, the focused security/ACL/session/conflict/index/Batch 1 group passed 128 tests with 2 staged-native-only skips on the installed extension. The final API security cases passed 20 tests; MCP HTTP and lobby REST/MCP parity passed 14. PUBLIC, INTERNAL and PRIVATE maintenance ACL behavior and token/session ownership are directly tested. Live and production status is unchanged; Batches 2-4 remain pending.


**Test-branch consolidation, 2026-10-16:** Batch 1 and the targeted API authorization correction, Atlas, tests and curated research evidence were committed locally on codex/ember-split-feedback-candidate04 as 44e52c554de79fa2bbddb1fe43ca65866225d87a. The commit was not pushed or deployed. Batches 2-4 remain pending, and isolated validation is not live production validation.

**Batch 2 local candidate implementation, 2026-10-17:** Current mapped CONFLICT status and recorded outcome now govern Promotion Engine admission; graph-only relationships hold for investigation. Route and admission share the existing store lock, direct promotion rechecks conflicts, proposal terminal decisions use CAS, and deterministic promoted-memory identity supports tested interruption/retry. Legacy explicit HUMAN promotion of non-conflict proposals remains an authenticated-writer operation; no verified human role exists and conflict-related overrides stay blocked. Baseline 66 passed; final isolated focused group 126 passed/0 failed/0 skipped with unchanged installed native SHA-256 452CC6D246C96311DDFE590F38947B6D386D39D4CF38F9650319E3833C6C19A5. Evidence and limits: track7_atlas_design_20261015/13_BATCH2_IMPLEMENTATION_EVIDENCE.md. Batch 3 audit, Batch 4 ELLA state reuse, Ember Control Center, live validation and deployment remain pending.

**Batch 2 post-commit isolated supplement, 2026-10-17:** Focused FUR/LADC tests passed 29/29. A spawned-process losing-resolution versus submit case passed, and the full Batch 2 test file passed 23/23. The first local Batch 2 commit is b52a16e654db7d921c09f28b743a91118c955bde; this follow-up evidence is separate. No source/native/live system changed after that commit, and Batches 3-4 remain pending.
