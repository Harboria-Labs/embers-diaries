# Lobby V2 operational follow-up — 2026-10-19

Scope: the existing test branch, isolated stores and temporary localhost HTTP API. No production binary, live store, deployed service, main branch or Track 7 implementation was changed.

## Cross-worker and HTTP delivery

The committed Lobby event journal remains in its separate provisional SQLite file. Each SSE subscriber installs an OS file-change listener before its initial journal read, then rereads the ordered journal only on a change to the SQLite WAL or a small post-commit signal file. The WAL notification also covers a writer exiting after commit but before signaling. A listener startup barrier closes the subscribe race. Event cursors, bounded history and explicit reset still come from SQLite; local in-process wake remains. `watchfiles==1.3.0` is pinned in API-capable dependency extras because the implementation uses its native notifier API. A notifier failure cannot silently leave the stream waiting: setup fails or the stream emits a reset. Only local shared-filesystem workers using this notifier were tested.

`tests/test_lobby_v2_multiworker.py`: a spawned reader process subscribed to an isolated store and received another process's committed finding, both with the post-commit signal and with that signal deliberately disabled; the latter used only the SQLite WAL change notification. `tests/test_lobby_v2_http_stream.py`: a real localhost HTTP SSE client received a committed finding, disconnected, and recovered the next finding with `Last-Event-ID`. The earlier Batch 2 tests still cover same-process push, revocation and missed-history reset. No production multi-worker deployment or network filesystem behavior was exercised.

## Live Lobby in the existing Observatory

An observer grant may explicitly include one Lobby scope and one active session belonging to the issuing member. The read-only Lobby routes recheck the observer grant, current shared-task membership, task/namespace match and namespace ACL. The projection opens the existing provisional SQLite file in read-only mode and neither calls recall nor writes observation, activity, presence, truth, usefulness, Pairing, LADC or promotion state. It exposes scope, members, bounded recent item statuses, correction version, uncertainty, evidence/contribution counts, timestamps and durable handoff status. It omits artifact bodies and evidence references. The Observatory page gains one panel with agent/task/namespace/status/kind filters, read-only item display, and Lobby SSE connection/reconnect/reset indications. Existing Research Observatory journal and research stream remain separate.

`tests/test_lobby_observer.py` checks no delegation, delegated snapshot, member revocation, capability revocation, no Lobby write authority, unchanged Lobby SQLite digest after viewing and that the panel appears only on the observer page. The localhost HTTP test also opened a delegated observer SSE stream, received both arrival and withdrawal, then observed the withdrawn status in the read-only projection. `node --check embers/api/lobby_observer.js` passed. Browser visual layout, deployed grant use and production CSS behavior were not tested.

## Real Codex runtime integration

`validation_artifacts/lobby_real_runtime_probe.py` ran a temporary localhost API/store and two real Codex CLI 0.156.1 turns with separate registered agent IDs and active sessions. Each had an invocation-scoped `Stop` command `python -m embers.lobby.codex_stop`; the hook source was reviewed for this isolated test and CLI hook trust was bypassed only for these invocations. The first turn emitted an explicit selected finding marker. Its integrated harness had executed `probe.py` and put the observed, redacted output `arithmetic probe passed` in the bounded outbox. The real Stop hook uploaded that output and submitted the finding; another authorized harness retrieved it with one evidence reference. The second real Codex turn consumed the authorized retrieved finding in its harness context, emitted an attributed outcome marker, and completed its Stop hook. The first agent's harness retrieved the second item while an HTTP SSE subscription received the second committed change. The final probe result was:

```json
{"manual_hook_exit":0,"manual_hook_valid_output":true,"codex_exit":0,"first_hook_completed":true,"marker_in_real_final":true,"reader_retrieved":true,"attached_evidence_count":1,"second_codex_exit":0,"second_hook_completed":true,"second_item_retrieved_by_first":true,"sse_changed":true,"sse_cursor":"15"}
```

This proves automatic publication through a real supported Codex Stop hook and authorized harness-mediated retrieval for two separate Codex runtimes in isolation. The first Codex runtime was initially asked to execute `probe.py` itself; automatic approval review rejected its tool command, so the harness ran the probe and supplied the actual observed output. An earlier Windows hook command using a quoted absolute executable path reported `hook: Stop Failed`; the documented `python -m` command completed. Neither the runtime's final text nor the test output alone proves the claim's broader interpretation; the claim and observed artifact remain distinct. The second contribution deliberately had no fabricated evidence.

Other MCP clients retain manual submission/retrieval. Claude, hosted ChatGPT and unsupported Codex hosts have no demonstrated completion hook here. No background model wakeup, autonomous action on SSE, durable promotion in this real-runtime probe or live service behavior is claimed.

## Focused checks and remaining limits

- `tests/test_research_observer.py tests/test_research_dashboard.py tests/test_lobby_v2_batch2.py tests/test_lobby_v2_multiworker.py`: 22 passed, 1 dependency warning.
- `tests/test_lobby_observer.py tests/test_lobby_v2_multiworker.py tests/test_lobby_v2_batch2.py`: 6 passed, 1 dependency warning before the added WAL-only variant; final multiworker group: 2 passed.
- `tests/test_lobby_v2_http_stream.py`: 1 passed.
- Existing four-batch focused group: 39 passed, 1 dependency warning before this follow-up.
- Final combined Lobby V2, multiworker, HTTP SSE, delegated Observatory and Research Observatory group: **62 passed, 1 dependency warning**. One first combined attempt had a test-only cached agent-registry fixture collision; the fixture was reset per isolated store and the full focused group passed on rerun.

The local API used a staged `watchfiles` install rather than changing the installed production interpreter. The native Ember binary and live store remained untouched. Production-scale event throughput, deployed multi-worker operation, browser visual behavior and hosted-agent hook support remain unverified.
