# ELLA V1 takeover checkpoint — implementation candidate, not deployed

Base test branch: `codex/ember-split-feedback-candidate04` at
`5a1d6b8b76a69589876c9c9057a5b3f7b0b2c8c3`.

Takeover branch: `codex/ella-v1-takeover`.

ELLA V1 research architecture is frozen. This document records implementation
status only. Do not treat it as permission to deploy.

## Frozen boundaries preserved

- usefulness / FUR / LADC / Pairing are not epistemic truth
- promotion is admission, not verification
- proposal confidence never becomes ELLA score
- source_type, origin confidence and assessor authority never set evidence strength
- assessments are claim-version × evidence-record specific
- detectable hard dependence counts once; hidden dependence remains a known limitation
- threshold crossings require a distinct second assessor
- opposite-polarity confirmation remains unresolved and cannot become a voting chain
- corrections, withdrawal, dependency changes and carry-forward are recomputed
- semantic conflict overlays public DISPUTED without changing ELLA score
- MemoryRoom behavior is unchanged by ELLA
- legacy verification annotations and legacy _status/verify_status are not canonical truth
- full ELLA metadata is outside the capacity-counted recall block

## Capacity boundary

The original draft inserted the full ELLA projection into each rendered recall
row. That made evidence growth increase the token size of the row and could evict
an otherwise identical direct or paired memory.

The accepted integration now keeps the capacity-counted row epistemically neutral:

```
truth_status = "see_epistemic_metadata"
truth_projection = {
  "source": "epistemic_metadata",
  "projection_version": "ella-v1"
}
```

Canonical state is returned separately under the ELLA metadata/API/observer
surface. Token budgets and Pairing capacity checks were not increased or weakened.

## Implemented

- native Rust ELLA policy, assessment, dependency grouping, unit reduction,
  S+/S-/S projection, verdict and conflict-overlay projection
- native Rust journal transition validation on both commit and replay
- separate append-only epistemic journal with CAS/idempotency
- SUPPORTS / OPPOSES and WEAK / MEDIUM / STRONG structured assessment
- exact claim-version targeting
- threshold-crossing confirmation
- confirmation disagreement and explicit resolution
- withdrawal, revision, evidence invalidation and lineage correction
- merge/split dependency changes with full recomputation
- audited evidence carry-forward along later versions only
- deterministic projection ordering
- evidence origin / origin-confidence / event / request / derived-from lineage fields
  with legacy hash compatibility
- promotion confidence separated from verification
- REST and MCP epistemic state/feedback surfaces
- observer/SSE epistemic delivery
- canonical public PROVISIONAL / VERIFIED / DISFAVORED plus DISPUTED overlay
- legacy MemoryStatus compatibility now preserves DISFAVORED instead of silently
  collapsing it to PROVISIONAL

## Validation

The latest functional candidate run passed:

- 90 focused feedback/replay regression tests
- 766 full Python tests
- 16 native Rust research-domain tests
- Rust type/build check
- isolated real-server ELLA acceptance

The live acceptance covered:

- initial PROVISIONAL state
- threshold confirmation to VERIFIED
- exact retry idempotency
- usefulness/epistemic firewall
- REST/MCP parity
- semantic-conflict overlay with unchanged ELLA score
- .99 proposal confidence still promoting as PROVISIONAL
- correction over an already-open SSE stream
- read-only observation
- restart persistence

The takeover audit also fixed two confirmation-lifecycle edge cases found after
the first green run: a disagreement can no longer be orphaned by revising or
withdrawing its active parent, and withdrawing an accepted confirmation restores
the original threshold-crossing assessment to pending confirmation.

The final semantic audit additionally closed three integration gaps:

- current-format/new unsealed evidence submissions record an explicit origin
  identity (`unknown` is legal); pre-sealed hash-version-1 evidence remains
  byte-for-byte compatible and is projected by ELLA as UNKNOWN provenance
  without changing its historical content hash
- model-facing and public REST/MCP memory reads no longer expose legacy
  `_status` / `verify_status` markers as competing truth; they expose bounded
  canonical ELLA metadata instead, while legacy verification annotations remain
  clearly labelled non-authoritative audit history
- ordinary memory visibility is fail-open with respect to optional ELLA summary
  projection: a projection failure reports `epistemic_projection_unavailable`
  but cannot hide the underlying memory; dedicated epistemic endpoints still
  surface the projection error directly

The functional branch passed again after these fixes. The remaining deployment
block is explicit merge/live-environment approval; nothing has been deployed.

## Known limitations retained intentionally

- hidden/unlinked common-source evidence can still be mistaken for independent evidence
- a distinct assessor ID is not a proof of cognitive/model independence
- semantic polarity/strength can be wrong or malicious; Ember bounds and audits
  the judgment but does not understand arbitrary science
- W/M/S numeric magnitudes and ELLA thresholds are configurable candidate values,
  not calibrated posterior probabilities or Wald-SPRT guarantees
- exact soft-dependence heuristics remain policy/calibration work
- evidence-dispute threshold remains configurable policy
- priors are deferred in V1
- lineage coverage is diagnostic, not a probability of safety

## Separate non-blockers

ROOM ISOLATION remains a pre-existing separate track: general recall without an
explicit room may return PERSONAL records. ELLA does not change that behavior.

RESEARCH TRACK 7 remains future work: bounded memory / attention / capacity policy
is broader than the existing token/result/storage caps.
