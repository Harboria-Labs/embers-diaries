# Ember implementation atlas

Source snapshot: local `codex/ember-split-feedback-candidate04` at `f183346`, inspected 2026-10-15. This is a source-first audit, not a deployment or live-server certification. HEAD does not capture the pre-existing dirty and untracked files; see the [source-version record](MAINTENANCE_CONTRACT.md#source-version-tracking). No persisted benchmark, server request, native rebuild, or broad test suite was run.

## Implemented map

`REST / MCP / Python SDK` → `MemoryProtocol` or direct `EmberDB` → `WriteEngine` / `ReadEngine` → immutable `PhysicalStore` and WAL. Write callbacks update master, timeline, text, vector, and graph indexes. Rust owns record codec/hash, transactions/CAS, graph operations, and research-domain decisions. Python owns orchestration, authentication, storage IO, and projections at public boundaries. See [component map](COMPONENT_MAP.md), [paths](EXECUTION_PATHS.md), and [inventory](SOURCE_INVENTORY.md).

Research recall is a separate path: FUR journal state → native activation batch and admission (LADC) → selected direct records → native Pairing selection from outgoing graph edges and FUR W → bounded response with ELLA metadata. Ordinary recall still searches text/vector indexes and uses decay/context rendering; it can optionally add one pair. ELLA has its own append-only RAW journal and native reducer; it is neither memory-version history nor usefulness state.

The main Track 7 finding is that substantial machinery already exists: immutable memory versions and agent history, index metadata, WAL recovery, integrity checks, FUR incremental same-object reduction, bounded research admission, and a durable observer journal. ELLA's `load()` nevertheless scans and replays its whole epistemic journal per call. Existing memory history cannot replace that replay because it contains different events. The conflict record is authoritative for semantic-conflict status; a graph `CONTRADICTS` edge only records a relationship.

## Reading order

1. [Source inventory](SOURCE_INVENTORY.md) accounts for production source, Rust, UI assets, and tests.
2. [Execution paths](EXECUTION_PATHS.md) traces calls, reads, writes, returns, and failures.
3. [Memory history](MEMORY_HISTORY_AND_LINEAGE.md) separates version, causal, conflict, and epistemic histories.
4. [Track 7 cross-check](TRACK7_CROSSCHECK.md) tests prior research assumptions against current code.
5. [Feature status](FEATURE_STATUS.md), [gaps](GAPS_AND_OVERLAPS.md), and [evidence](VALIDATION_REFERENCES.md) state practical limits.
6. [Coverage register](COVERAGE_REGISTER.md) states what was actually inspected. Follow the [maintenance contract](MAINTENANCE_CONTRACT.md) on every subsequent Ember task.

Existing descriptions consulted and cross-checked: `docs/consolidated-research-model.md`, `docs/ella-v1-checkpoint.md`, `docs/pairing-matrix-v1.md`, `docs/research-observer-mode.md`, `docs/primary-context-contract-01.md`, `docs/context-orientation-contract-02.md`, `features update`, and `track7_research_20261013/`, `track7_phase2_20261013/`, `track7_phase3_20261013/` reports. Their claimed past test runs are documented intention or historical evidence, not execution in this audit.
# 2026-10-15 focused Track 7 design follow-up

The documentation-only `track7_atlas_design_20261015/` research notes extend this Atlas for conflict retrieval, independent integrity auditing and incremental ELLA state. They propose **unimplemented** integrations and retain explicit contract questions. See [coverage](COVERAGE_REGISTER.md) for review depth, [maintenance contract](MAINTENANCE_CONTRACT.md) for source-version history, and the canonical Production × Research Map for separate research/production/live status. No source, test, store or runtime change is implied by this Atlas update.

The later [existing-mechanism reconciliation](../../track7_atlas_design_20261015/08_EXISTING_MECHANISM_RECONCILIATION.md) narrows those proposals using the current source and `features update`. In particular, a new durable ELLA head or MasterIndex generation marker is **not yet proven necessary**; existing WAL, lock, physical-ID enumeration, sidecars, rebuild, CAS, promotion/review and write-audit mechanisms must be reused or evaluated first. Earlier design notes remain as historical alternatives, not current implementation claims.

## Track 7 upgrade contract for review — 2026-10-15

The [ordered roadmap](../../track7_atlas_design_20261015/09_COMPLETE_UPGRADE_ROADMAP.md), [regression matrix](../../track7_atlas_design_20261015/10_REGRESSION_AND_RECOVERY_MATRIX.md), and [reuse/policy record](../../track7_atlas_design_20261015/11_REUSE_AND_POLICY_DECISIONS.md) cover seven planned upgrades in four gated batches. They are **research artifacts, not implemented behavior or approval to edit source**. Batch 1 certifies MasterIndex candidates before conflict retrieval/concurrency; Batch 2 aligns promotion with an approved current-status policy; Batch 3 adds independent audit reporting; Batch 4 reuses verified ELLA state and runs cross-feature validation. See the coverage register and source-version log for what was actually inspected.

## Batch 1 local implementation — 2026-10-16

Batch 1 is now implemented in the local working tree and validated with isolated stores. See [implementation evidence](../../track7_atlas_design_20261015/12_BATCH1_IMPLEMENTATION_EVIDENCE.md) for the source-change protocol, conflict path, tests, exact I/O counts, rollback and limits. No live runtime or installed native binary was changed. The 2026-10-15 statements above remain the planning snapshot; the source-version log and evidence page establish current applicability. Batches 2–4 remain unimplemented.

## Targeted API authorization correction (2026-10-16)

POST /v1/maintenance now validates an explicit nonempty namespace list and checks the authenticated agent's existing WRITE ACL for every requested namespace before reflect/consolidate. Default memories applies only when the field is absent. Token authentication with a session header now requires an existing, active session owned by that agent before observation binding. Session-only authentication is unchanged. This does not establish a new integrity-audit permission model or change scheduled/MCP maintenance policy.

## Batch 2 local implementation — 2026-10-17

Promotion now evaluates current mapped CONFLICT status and recorded resolution outcome, with unmapped graph relationships held for investigation. The shared store lock spans routing and admission; direct promotion rechecks conflict state and uses proposal-head CAS. Conflict-related Human overrides remain blocked because no verified human authority exists. See [Batch 2 evidence](../../track7_atlas_design_20261015/13_BATCH2_IMPLEMENTATION_EVIDENCE.md). Isolated validation passed; this is not live deployment. Batches 3 and 4 remain pending.
## Batch 3 implementation note — 2026-10-17

The current test branch adds independent, scoped physical/index audit reporting in existing maintenance code, protected REST/MCP access, and local-operator-only derived-index recovery with re-audit. This supplements older research-only statements above; it does not change normal recall, conflict lookup, ELLA, or observer source. See [Batch 3 evidence](../../track7_atlas_design_20261015/14_BATCH3_IMPLEMENTATION_EVIDENCE.md), [coverage](COVERAGE_REGISTER.md), and the [source-version entry](MAINTENANCE_CONTRACT.md). Batch 4 and live validation remain open.

## Batch 4 local implementation — 2026-10-17

Normal ELLA reads and updates now reuse a bounded, verified process projection after first authoritative replay. The sealed event journal, native reducer and full `load()` replay remain authoritative. Source epoch, journal head, store identity and Batch 3 audit marker govern reuse; committed new events are reduced incrementally. See [Batch 4 evidence](../../track7_atlas_design_20261015/15_BATCH4_IMPLEMENTATION_EVIDENCE.md). Local isolated validation is separate from live-agent and production validation.

## Phase 5 local validation — 2026-10-17

The selected cross-feature and native/recovery test groups passed in isolated stores; the two installed-native source-epoch skips passed separately with a compatible staged build. A disposable 50/150/300-memory probe measured fixed targeted physical-read counts, growing per-query in-memory metadata fingerprint CPU work, and source scans at cold/catch-up/explicit reconciliation. This is consistent with the approved Batch 1 physical-I/O contract, so no runtime index change was made. See [Phase 5 evidence](../../track7_atlas_design_20261015/16_PHASE5_VALIDATION_EVIDENCE.md) for exact results and limits. No live deployment or validation was performed.
