# Live Lobby Observatory — completion checkpoint, 2026-10-19

Development branch: `codex/ember-split-feedback-candidate04`, inspected at `dcd898ebb7adf2842a7d69e2fd9b0cbf3b3a9115`. No other development branch was created. This is a narrow Observatory follow-up, not Track 7 validation or a system redesign.

## Source verification and changes

The current source already contains Lobby V2 provisional SQLite storage, shared-task authorization, attributed contributions, durable handoff intents/receipts, REST/MCP integration, the opt-in Codex Stop adapter, OS-notification-based SSE and a read-only Observatory panel. The prior 62-test and two-Codex-turn report remains historical evidence in `paper/lobby_v2_operational_evidence.md`; those runtime turns were not repeated here.

Confirmed defects corrected:

- `ember_visualize` did not accept/forward Lobby delegation although REST did. Its existing tool now accepts `lobby_scope_id` and `lobby_session_id`, using the same existing membership/session/ACL checks.
- Snapshot rows and cursor were separate autocommit SELECTs. A concurrent commit could advance the cursor past an item missing from the snapshot. The read-only connection now starts a single read transaction; a race regression proves the missing item remains replayable after the returned cursor.
- Observer SSE ignored `Last-Event-ID`. It now accepts the header as the cursor override and rejects invalid/negative values.
- The client advanced its cursor before rendering the snapshot, stopped on temporary fetch errors, and could accept obsolete async work after switching grants. It now confirms the cursor after rendering, serializes/coalesces event-triggered snapshots, uses bounded reconnect backoff and ignores stale-generation results. HTTP 401/403 and SSE denial end access instead of looping.
- Clock expiry could leave active content visible indefinitely during idle. A local deadline timer hides expired content, with no HTTP request or Lobby mutation.
- Closing an empty/already-withdrawn scope produced no event. The existing close transaction now records one `scope_closed` notification so the same SSE path updates the scope header. This is delivery instrumentation; it does not change close permissions or memory semantics.
- Evidence/contribution counts lacked explanation. The existing panel now exposes safe evidence summaries, availability, authors and contribution type; it never includes reference URLs, artifact bodies, execution context, raw session IDs or contribution notes. At most 16 recent contributor summaries per item are returned, with explicit truncation. The existing 1,000-item limit remains.

The panel retains its existing task/namespace/author/status/kind filters and renders affected cards by stable item ID. It shows membership as membership, not online presence. Reported uncertainty is explicitly not ELLA verification. Handoff receipt is explicitly not proof of final promotion. Earlier claim text, explicit supersession links and final promotion decisions are unavailable in this projection and are labelled as such rather than inferred. No new history or promotion system was introduced.

## Feature status

| Capability | Status |
| --- | --- |
| Explicit authorized task/namespace and members | Present; one delegated shared scope per capability |
| Findings, failures, questions, outcomes | Present, with scoped filters and empty state |
| Evidence availability/provenance | Safe metadata summaries; artifacts/URLs withheld |
| Uncertainty versus verification | Distinct explanatory labels; no inferred ELLA status |
| Corrections/withdrawal/expiry | Current version and tombstone; superseded text not exposed |
| Handoff | Pending/accepted receipt, durable target; final promotion not inferred |
| Live delivery | Existing SSE wakeups, ordered cursor replay, reset and reauthorization |
| Observer writes | None; no join, presence heartbeat, recall, feedback or promotion action |
| Browser visual validation / deployed endpoint | Not verified in this environment |

## Validation actually performed

- Native extension built from this checkout in a disposable Python environment. No Rust source changed.
- Focused command:
  `python -m pytest -q tests/test_lobby_observer.py tests/test_lobby_v2_batch1.py tests/test_lobby_v2_batch2.py tests/test_lobby_v2_batch3.py tests/test_lobby_v2_batch4.py tests/test_lobby_v2_multiworker.py tests/test_lobby_v2_http_stream.py tests/test_research_observer.py tests/test_research_dashboard.py`
- Client command: `node --test tests/js/lobby_observer.test.cjs` — **7 passed**. These use a small simulated DOM, timers and EventSource, not a browser. The three-minute idle case uses a virtual clock; it proves no scheduled periodic snapshot GET in this client, not three deployed minutes.
- `node --check embers/api/lobby_observer.js` and `git diff --check` passed.
- Real HTTP tests use the normal API app on an isolated loopback server. Arrival, removal and observer-header replay are asserted. Cross-process OS/WAL wakeup tests also passed. No production endpoint was contacted.
- Observation tests compare hashes before/after snapshots and stream/replay, and check capability rejection by mutation routes, missing delegation, member/capability revocation, inactive session, namespace mismatch and withheld provenance fields. No learning/retrieval routine was added to the observation paths.
- An existing Research Observer restart test used a Windows-only `indexes\\` prefix. It now normalizes the separator for its existing derived-index exclusion; the strict post-open observation hash assertion is unchanged.
- Browser tool attempted `http://127.0.0.1:9327/observer` and returned `ERR_CONNECTION_REFUSED`. This remote browser could not reach the isolated execution listener. No screenshot, visual layout, real-browser filter interaction, production reconnect or same-page production restart is claimed. The failed temporary browser fixture was stopped; its disposable credentials are not committed.

Initial test attempts exposed a new test's incorrect MCP class name and the pre-existing path-separator assumption; both were corrected. No failures were hidden by changing Ember behavior. **Final: 45 Python tests passed, zero failures/skips, one Starlette/httpx deprecation warning (3.74 seconds). This includes a Python wrapper executing the seven JavaScript cases; those seven also passed standalone.**

## SSE behavior and limits

One Lobby EventSource while that panel is open. Initial bounded snapshot, then SSE invalidations cause coalesced bounded snapshot reads. This is event-driven invalidation, **not** a periodic polling loop and not full item bodies in SSE. Cursor advancement follows the successful read/render. Reconnection performs bounded catch-up before reopening SSE; reset does likewise. Leaving the Lobby panel closes its stream. Existing Research Observer transport remains separate and unchanged. Header diagnostics show Lobby status, cursor, snapshot/reconnect/notification counts.

Shared task/session/namespace authorization is checked server-side before the snapshot/stream and on delivery/heartbeat. Capability expiry/revocation remains enforced, without silent renewal. Server heartbeats only recheck access; the observer read callback uses SQLite read-only mode and never calls pruning or presence updates. External evidence availability means a reference was recorded; Ember does not fetch or validate the remote URL. Expiry display uses server snapshot time plus local elapsed time.

Browser screen-fit/theme behavior, sustained deployed idle traffic, network proxies, same-page server restart and high-volume production load remain local/operator validation gates. A complete immutable correction history or final promotion projection would require a separately reviewed surface; current UI labels absence honestly.

## Source-verified startup and access

Use the existing checkout, Python environment, configuration and normal server. Do not start a second server if one is already serving this store.

```bash
git switch codex/ember-split-feedback-candidate04
git pull --ff-only
python -m pip install -e '.[api]'
python -m embers.api_cli --config /path/to/existing/ember.toml
```

The TOML path is a placeholder for the operator's real config. `ember-api` is the equivalent installed command. `--host` and `--port` override configured `api.host`/`api.port`; the source default port is **9200**. Existing environment overrides include `EMBER_CONFIG`, `EMBER_STORE`, `EMBER_API_HOST` and `EMBER_API_PORT`. Use an isolated store for testing. Normal startup may load the `cl100k_base` tokenizer vocabulary; install/cache it normally if the environment lacks it. No legacy Candidate 04 flag is needed for Lobby. `watchfiles==1.3.0` is installed by the API extra for existing cross-worker delivery.

Backend: `embers.api:app`, Lobby routes in `embers/api/lobby_v2.py`, observer routes in `embers/api/observer_routes.py`. Frontend: existing `/observer` or `/visualizer?mode=observer`, rendered by `observer_build.py` with `lobby_observer.js`. No separate frontend dev server/build is required.

An authenticated **current member** asks the existing MCP tool:

```json
{
  "name": "ember_visualize",
  "arguments": {
    "lobby_scope_id": "<existing shared task scope>",
    "lobby_session_id": "<active owned session matching task/namespace>",
    "server_url": "<actual reachable server origin>",
    "ttl_seconds": 3600
  }
}
```

Use the existing MCP authentication/session mechanism; do not put agent tokens or bearer sessions into the human's URL. REST equivalent: authenticated `POST /v1/observer-access` with the two Lobby fields. Current expiry is 60–86,400 seconds, default one hour; renewal is explicit. The capability remains tied to a valid matching Lobby session and membership even when ordinary research observation spans sessions.

Return the tool's actual `visualization_url`, which has `/visualizer?mode=observer#observe=<capability>`. Opening it consumes/removes the fragment and exchanges it for the existing HTTP-only observation cookie. Click **Live Lobby** in the header. A normal observer link without explicit Lobby delegation intentionally does not show that button. If using the default loopback configuration the expected origin is `http://127.0.0.1:9200`; this is an example, not a verified URL on the user's machine.

### Handoff to the local operator

Pull Candidate 04, preserve local changes, use the existing configured environment/store, find the actual listening port from configuration/startup logs, and verify `/health` plus `/observer`. Obtain a Lobby-enabled capability through an authorized member's active session, and return the **actual reachable visualization URL**. Open it, click Live Lobby, publish a separate isolated agent finding, correct/withdraw it and verify updates plus filters. Leave it idle, inspect Lobby counters, and check reconnect/replay after a controlled restart. Never return the agent token or raw session ID. If scope/session authorization is unavailable, report that instead of inventing a link.
