# Pairing V1 ordinary-recall capacity fix

`top_k` limits direct memories. The selected pair is now admitted against the separate existing `search.max_results` hard cap, rather than against `top_k`. At most one pair is added; direct results remain untouched. Existing text/message token and character checks are unchanged. Failed pair admission does not substitute another candidate.

Files changed:
- `embers/integration/memory_protocol.py`: one admission-condition change and explanatory comment.
- `tests/test_pairing_v1.py`: obsolete top_k=1 assertion replaced; eight additional regression cases.
- This report.

Validation:
- Focused Pairing + consolidated research suite: **50 passed**.
- Full suite: **716 passed**, 37.90 seconds.
- One external Starlette TestClient deprecation warning.
- An initial new message test compared unrounded time-decayed confidence across calls and failed; it was corrected to compare unchanged direct content and IDs. No production confidence behavior was changed.

Regression coverage:
- top_k=1: A direct plus B paired (structured, raw, text, messages).
- top_k=2: A,C direct plus B paired (structured/raw).
- Full separate max_results cap: direct preserved, pair null (all four formats).
- Exhausted text/message budget: direct preserved, pair null; selected strongest edge attempted once, no lower-W substitution.
- Existing research-recall capacity, correction, retry, context, feedback and read-only pair-target tests remain passing.

No Rust, FUR/W mathematics, LADC, context, primary selection, ranking, feedback or serialization changes. Research recall implementation is unchanged. No live validation, deployment or server startup performed. Unrelated workspace changes preserved.
