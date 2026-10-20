# Contract 01: primary-context plumbing

Status: implemented on the experimental branch; live-agent validation pending.
No canonical context resolution, similarity, scoring or transfer is introduced.
Existing Candidate 04 equations are unchanged.

## Fields

- `primary_context`: optional nonempty string or null on `remember` / `ember_write`
  / POST `/v1/memory/write`; stored at `record.data.primary_context`.
- `primary_context`: the same optional, separately supplied field on `recall` /
  `ember_recall` / POST `/v1/memory/recall`.
- `inspect_context`: optional boolean on recall. Use true to inspect query-only
  requests, including an explicitly unset context.
- `retrieval_context`: optional string or null on feedback (legacy or v2). The
  caller copies this from the recall response. It persists with the feedback.

Labels are opaque: Ember preserves spelling and whitespace, rejects blank labels
and non-string values, and never equates `LADC` with `LADC research`. Identity
resolution remains with the agent. Null/omission means unset. For legacy payload
compatibility, unset metadata need not be written as a new serialized key.
An SDK content object can already carry primary_context; conflicting explicit
values are rejected rather than silently overwriting either value.

## Observability and compatibility

A non-null recall context, or `inspect_context: true`, returns an envelope:

```json
{
  "query": "Ember",
  "primary_context": "LADC",
  "context_policy": "agent-supplied-pass-through-v1",
  "candidates": [{"id": "...", "primary_context": "Pairing Matrix"}],
  "results": [{"id": "...", "primary_context": "Pairing Matrix"}],
  "memories": []
}
```

`memories` uses the existing requested rendering. Candidates are the ordinary
retrieval merge before room filtering and top-k selection, not every stored
memory. Context is not appended to the query, used as a filter, or given an
invented ranking weight. Ordinary namespace permissions, room filters and
retrieval limits still apply. Cross-context eligibility is guaranteed; inclusion
of every cross-context memory is not. Same-subject memories are not merged or
copied. Primary context is excluded from automatic factual-conflict hints.

When context and inspection are omitted, the previous recall response shape is
preserved. Existing records need no migration. `ember_read` exposes stored
context in `data`. Feedback reads expose `retrieval_context` when set.

Feedback context is agent-reported metadata: this contract does not authenticate
that a past retrieval happened, bind it to a durable receipt, or authorize
learning. It never defaults to the target memory's primary context. Existing
v2 context_id/context and explicit resolver authorization still govern Candidate
04 learning; this new label does not replace them.

## Live acceptance

Update/reinstall the test branch and restart the server in the same Python
environment. Refresh the client's tool catalog. `ember_write` and `ember_recall`
must advertise `primary_context`; `ember_feedback` must advertise
`retrieval_context`. This contract works with the ordinary server; it does not
require a Candidate 04 resolver to be configured.

1. In an isolated namespace write two records with subject `Ember`, searchable
   content mentioning `Ember`, and contexts `LADC` and `Pairing Matrix`.
2. Read both IDs: each must retain its own context and the same subject.
3. Recall query `Ember`, primary_context `LADC`, top_k 10. Inspect the envelope:
   requested context is LADC; Pairing Matrix remains eligible and appears for
   this small fixture. There must be no duplicate stored records.
4. Give useful feedback on the Pairing Matrix result with retrieval_context
   copied from the response (`LADC`). Read feedback: it must retain LADC while
   the target memory retains Pairing Matrix.
5. Recall with primary_context null and inspect_context true: context is null.
   Submit feedback with retrieval_context null: reads must not invent a value.
6. Omit all new fields and confirm old write/recall clients still work.
7. Restart the same store and verify stored contexts and feedback context persist.

The agent should report request/response evidence with credentials redacted.

Run the automated live check after updating and restarting your server:

```sh
python examples/primary_context_live_smoke.py --url http://127.0.0.1:9200
```

A tunnel base URL also works (omit `/mcp`; the script adds route paths). The
script registers a test agent and writes only to a unique test namespace. It
prints no credentials. It leaves its test records in place for inspection.

## Production × Research update — 2026-09-25

| Track | Contract/research | Test-branch implementation | User live validation |
|---|---|---|---|
| 01 Primary-context plumbing | Accepted | Implemented; 14 new tests | Pending |
| 02 Canonical context resolution | Open | Not added | Pending |
| Context similarity/transfer | Open | Not added | Pending |
| Existing Candidate 04 learning | Experimental | Unchanged by Contract 01 | Previously blocked on deployed build |

Validation: 566 tests passed locally with the native backend. Contract 01 HTTP
MCP + REST smoke test also passed on an isolated local store. The user’s deployed
server has not been changed by this work.
