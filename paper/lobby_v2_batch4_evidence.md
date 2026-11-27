# Lobby V2 Batch 4 local adapter evidence — 2026-10-18

Status: common REST/MCP contract and an opt-in Codex Stop adapter are implemented and tested at isolated boundaries. **Actual Codex hook firing and a second live agent connection remain unverified.** This is a local adapter milestone, not completed real-agent validation or deployment.

`embers/mcp/lobby_v2_surface.py` exposes scope, manual submit/query/get/evidence/artifact, contribution and handoff through the same `working.py` operations used by REST. REST provides SSE. `embers/lobby/codex_stop.py` consumes the documented Codex `Stop` hook payload. It reads only `last_assistant_message`, never transcript or tool logs. Publication requires one explicit `<ember-lobby-v2>{...}</ember-lobby-v2>` JSON marker with `share: true`; `withhold: true` or no marker sends nothing. A selected redacted artifact file must be directly inside `.ember-lobby-outbox` in the hook `cwd`. The adapter observes its bytes, uploads a bounded temporary artifact with a stable turn key, and submits the claim with a stable turn key. Ember stores the artifact digest and gates artifact retrieval separately. The agent's interpretation remains `unverified` unless the agent explicitly supplies another uncertainty label; durable handoff maps this evidence to `REPORTED`, not claim proof.

An opt-in Codex hook can call `python -m embers.lobby.codex_stop` on `Stop`, with `EMBER_LOBBY_URL`, `EMBER_LOBBY_AGENT_ID`, `EMBER_LOBBY_TOKEN`, `EMBER_LOBBY_SESSION_ID`, and `EMBER_LOBBY_SCOPE_ID` in its environment. The hook command must be configured and trusted by the Codex host; this repo does not install or enable it. Codex's [hook documentation](https://developers.openai.com/de-DE/docs/hooks) documents `Stop` and its payload. Other MCP clients can submit/query manually; no automatic Claude or ChatGPT capture, long-lived MCP push, or model wakeup is claimed.

Example selected completion in the final assistant message (the JSON marker is visible to the user):

```text
<ember-lobby-v2>{"share":true,"kind":"finding","claim":"three parser tests passed","uncertainty":"unverified","artifact_file":"result.txt","redacted":true}</ember-lobby-v2>
```

`result.txt` must contain a redacted, shareable result and live directly in `.ember-lobby-outbox` under the hook working directory. The hook stamps its own observation time and digest. A host without a supported Stop hook uses manual `ember_lobby_v2` submit/query or the matching REST endpoints.

The isolated hook-payload test drove a selected finding plus actual redacted file bytes through the adapter, observed a committed Lobby SSE signal, then retrieved the finding and artifact as another authorized session. Separate tests covered REST artifact authorization and manual MCP submission. This exercises the adapter function with a simulated Codex payload and isolated service transport; it does **not** prove a Codex runtime invoked the hook. No actual Codex client was configured, no production server was contacted, and no live store was touched.

Focused command: `diaries\Scripts\python.exe -B -m pytest tests/test_lobby_v2_batch4.py tests/test_lobby_v2_batch3.py tests/test_lobby_v2_batch2.py tests/test_lobby_v2_batch1.py tests/test_lobby.py tests/test_lobby_api.py tests/test_mcp_http_transport.py -q -p no:cacheprovider --basetemp .test_lobby_v2_final_20261018` — **39 passed, 1 dependency deprecation warning**. This pass tests committed handoff intent after lost acknowledgment, retained promoted artifacts across closure and expiry, artifact capacity, expiry removal signals, and hashed session references in durable V2 handoffs. Internal session-account links retain the durable ID.

Open completion gate: configure the hook in an isolated real Codex harness, capture an actual `Stop` event with a selected/redacted result, and verify a second authorized agent's real SSE connection and retrieval. Cross-worker live push remains separate and unverified.
