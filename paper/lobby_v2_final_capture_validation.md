# Final focused Lobby harness evidence validation — 2026-10-19

Verdict: **genuine automatic tool-result capture was not achieved in this environment**.
This is not full end-to-end success. Candidate 04 was pulled to
`b3159d052f3354d250f5bfdf7c48b0bc90a265d0` before inspection. No runtime code,
mathematics, memory behavior, deployment or Track 7 work changed.

## Source findings

- `embers/lobby/codex_stop.py`: `process_stop` accepts a final-message publication
  marker and a selected, explicitly redacted outbox basename. It uploads the file
  through existing REST artifact/item routes. Non-Stop and recursive hooks are
  ignored. It does not consume completed tool-call results or open transcripts.
- `embers/lobby/working.py`: existing authorized provisional storage, artifact
  digest/reference, retry identities and evidence retrieval are reused.
- `embers/mcp/lobby_v2_surface.py`: existing agent-selected submission/retrieval,
  not an automatic tool-result collector. REST submission is the Stop adapter path.
- `embers/lobby/realtime_v2.py`, existing API observer routes and
  `embers/api/lobby_observer.js`: persisted events, scoped SSE, bounded read-only
  projection and recovery remain existing mechanisms.
- `validation_artifacts/lobby_real_runtime_probe.py`: the historical harness runs
  the arithmetic subprocess and prepares its outbox before invoking Codex. Its
  prompt explicitly says no tool call is needed or permitted. The historical two
  real CLI Stop turns prove selected publication, not capture of agent tool work.
  The prior operational report remains historical evidence, not a new execution.

The adapter's digest verifies uploaded bytes, not their execution provenance.
Its returned artifact timestamp is upload/recording time, not proof of execution
start/end. The `redacted` assertion is not an automatic redaction algorithm.
Command, exit status, tool-call identity and source revision are not independently
harvested by this adapter. Do not interpret `execution_context` as such proof.

## Environment boundary

No Codex CLI was found on PATH (`command -v codex` returned no path), and the
available remote agent tools expose execution results to this agent but no
supported lifecycle subscription delivering those results into this Stop adapter.
No real Codex CLI session was run. No private runtime internals or host credentials
were accessed. Manually copying this task's shell output into an outbox would not
satisfy automatic capture, so that substitution was deliberately not performed.

To close the remaining gate, a supported agent harness must supply completed-tool
receipts (actual tool/call identifier, command, exit status, permitted output,
execution timestamp and revision) and associate selected receipt references with
the agent's completed finding. Only selected safe evidence should reach the
existing outbox/adapter. Run that actual integration in an isolated store with a
second authorized session and a connected SSE observer. No new Lobby, reasoning
capture or permanent promotion is required; no unverified hook API is prescribed.

## Executed validation and evidence grades

All stores were disposable test stores, not the user's private Ember store.

Command (Python executable: `../lobby-venv/bin/python`):

```sh
python -m pytest tests/test_lobby_v2_batch1.py tests/test_lobby_v2_batch2.py tests/test_lobby_v2_batch3.py tests/test_lobby_v2_batch4.py tests/test_lobby_v2_http_stream.py tests/test_lobby_v2_multiworker.py tests/test_lobby_observer.py tests/test_lobby_rest_mcp_parity.py -q
```

An intermediate run had 41 passed/1 failed because the new denial assertion
expected WorkingError instead of the existing PermissionError. Corrected the test
expectation; no authorization code changed.

Final result: **42 passed, 0 failed, 0 skipped**, one FastAPI/Starlette dependency
deprecation warning. The observer test also runs the existing Node client suite.

| Check | Evidence and limitation |
|---|---|
| Selected finding/evidence linkage | Simulated Stop event and explicitly synthetic artifact in `test_lobby_v2_batch4.py`; real isolated storage. Claim and evidence remain separate; default uncertainty is unverified. Not real agent execution. |
| Peer retrieval and denial | Authorized second session retrieves exact bytes/digest; outsider finding read rejected; existing REST test proves artifact rejection before grant and success after grant. |
| Retry behavior | Same receipt identity and unchanged event cursor after repeat; changed artifact with same identity rejected without advancing cursor. |
| Selectivity | Non-Stop/recursive hooks do not publish; no marker/withholding does not publish; unrelated outbox file excluded. Missing selected artifact yields unavailable evidence, not fabricated tool evidence. |
| SSE | Adapter fixture receives `working_memory_changed` with its receipt ID; real isolated HTTP tests cover observer/direct streaming, cursor replay and read-only hashes; cross-process wake tested separately in the selected suite. |
| Observatory | Existing projection authorization, namespace/session rechecks, revocation and read-only tests pass. Client state-machine tests execute. No browser or deployed visual check in this task. |
| Automatic runtime capture | **Unsupported/unverified here**; no real tool execution was automatically linked to a real completed finding. No real resulting finding ID is claimed. |

Changes are narrowly scoped test assertions and documentation. The fixture text
`3 passed in isolated pytest` is synthetic test data, not an execution receipt.
The actual pytest execution above validates the adapter contract only; its output
was not automatically captured and published through a supported agent harness.

## Scope closure

Agent-selected publication, isolated permissions/retries and SSE are supported by
executed tests. Automatic evidence capture remains a separate unproven integration
gate. Existing historical runtime results are not upgraded to that proof. No
runtime defect requiring a code fix was established. No Track 7 validation or
final documentation overhaul was performed.
