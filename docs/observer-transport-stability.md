# Research Observer transport stability fix

Scope: observer/browser/SSE delivery only. Base commit `0560f7d`.
No changes to memory, FUR, U, N_eff, W, activation, model time, Contract 01/02,
retrieval policy, truth, or research configuration. No Rust code changes.

## Diagnosis and confidence boundary

The supplied `ember-test15-post-restart.md` reports a genuine deployed defect:
7 snapshots / 6 reconnects, later 9 / 8, with occasional successful pushes. It
also establishes that persisted model/configuration state remained correct.

The old observer did **not** use native EventSource. It used a streaming `fetch`,
a body reader and an AbortController. The browser scheduled an anonymous abort
10 seconds after the last received body chunk. A controlled reverse-proxy test
forwarded the handshake, then withheld subsequent heartbeat chunks while the
upstream application stayed open. The old browser called `AbortController.abort`
at approximately 10.15 seconds. Its captured stack points to the observer's
watchdog, followed by its unconditional finally-block abort. Snapshot count rose
from 1 to 2 and reconnect count from 0 to 1; it displayed LIVE again following the
next brief handshake. See `observer-transport-before.json` for the exact trace.

This establishes a **client-initiated cancellation path**, not a corrupt model.
It does not establish why bytes were delayed in the user's ngrok deployment.
Our initial public request returned HTTP 502 (an HTTP/2 proxy error). A second
HTTP/1.1 request reached ngrok and returned HTTP 404 with ERR_NGROK_3200 (offline).
No authenticated deployed SSE headers/server/ngrok logs could be collected. The production initiator remains unconfirmed; do not label ngrok as
proven faulty or claim the deployed system is already fixed.

Additional defects confirmed by inspection and tests:

1. Every stream failure unconditionally fetched a snapshot. This explains the
   near-lockstep snapshot/reconnect counters; it was not healthy streaming.
2. Every `ready` handshake reset the failure count. Repeated short-lived streams
   could therefore evade the intended fallback threshold.
3. Fallback snapshots shared the active connection's controller/deadline.
4. Authorization termination left the badge describing reconnection rather than
   an explicit access-expired terminal state.
5. Stream-generator closure was implicit in a wrapper `async for`; cancellation
   now explicitly closes the nested generator and unregisters its listener.

No duplicate-connection root cause or server-imposed ten-second timeout was
observed. No application gzip/buffering middleware was found. Existing SSE
formatting, durable journal replay, namespace-vector cursors and filtering were
already in place and have not been redesigned.

## Exact fix

`observer_transport.js` owns one native EventSource and one reconnect timer.
On error it closes EventSource before scheduling a replacement, disabling native
auto-retry so two retry systems cannot compete. Callbacks from replaced owners
are ignored. Filter/grant changes explicitly stop their previous owner.

- Initial load performs the existing bounded snapshot.
- Healthy delivery uses only the persistent SSE stream, with no snapshot timer.
- Reconnect supplies the last confirmed namespace-vector cursor in the URL.
  The server also retains support for `Last-Event-ID`. It replays from the same
  authorization scope; the UI deduplicates persisted namespace/revision IDs.
- Ordinary reconnect uses SSE replay, not a new snapshot. Already-rendered state
  is retained on transient failure and server restart.
- Connection/handshake deadline: 30 seconds. The old ten-second body deadline is
  removed. A named 45-second liveness deadline detects truly silent half-open
  transport; events or transport keepalives reset it.
- Failed attempts back off 1, then 2 seconds. After three failures, fallback
  polls at most once per five seconds after each completed bounded fetch and
  attempts SSE recovery every ten seconds. Poll requests never overlap.
- Before each SSE recovery attempt, fallback polling and any pending snapshot
  are cancelled so a late REST response cannot roll the stream cursor backwards.
  The badge reads RECONNECTING during that probe; a successful handshake stops
  polling completely. Failures reset only after received stream activity demonstrates at
  least 15 seconds of connection lifetime, not from an isolated `ready` frame.
- LIVE means a validated SSE handshake on the currently open EventSource.
  RECONNECTING means transport unavailable; POLLING FALLBACK means the bounded
  polling loop is enabled. Snapshot success never sets LIVE.
- Explicit new grant links are consumed even on same-document hash navigation,
  so replacing an expired grant in the same tab does not leave the old observer active.
- Expired/revoked/missing authorization ends in ACCESS EXPIRED. All stream,
  retry, polling and expiry timers stop. No grant is renewed or extended.
- Invalid cursor/journal recovery ends in RECOVERY REQUIRED, with no automatic
  broad replay or new grant.

`GET /v1/observer/status` checks the same capability without fetching the journal.
It allows an EventSource error (which hides HTTP status from JavaScript) to be
separated from an authorization failure. It returns authorized identity, expiry,
remaining seconds and scope only. It is not a memory snapshot or polling feed.
It runs on error/expiry checks, never every five seconds while healthy.

The existing server heartbeat comments remain. The route additionally emits a
`keepalive` transport frame so native EventSource can expose liveness to the UI.
It contains only the non-secret stream ID, has **no journal event ID**, and does
not call any model/memory mutation. No false journal revisions are invented.

## Protocol and diagnostics

Origin response headers:

```
Content-Type: text/event-stream; charset=utf-8
Cache-Control: no-store, no-cache, no-transform
X-Accel-Buffering: no
Content-Encoding: identity
X-Ember-Observation-Protocol: ember-agent-observer.v1
X-Ember-Stream-Id: <random non-secret connection ID>
```

No fixed Content-Length is set. The ASGI server supplies HTTP transfer framing;
no HTTP/1-only Connection header is injected into HTTP/2. Identity encoding and
no-transform/no-buffering headers explicitly request immediate delivery, but
cannot force an arbitrary intermediary to obey them.

Persisted observer delivery remains:

```
id: <replayable namespace-vector cursor>
event: observer
data: {"events":[{"event_id":"usefulness-...-000000000003","revision":3,...}],"cursor":"...",...}

```

The SSE ID is an opaque checkpoint, not a new global revision. Each namespace's
revision is monotonic; separate namespace revisions must not be compared as if
they belonged to one merged journal. Individual committed event IDs remain stable.

The UI exposes state, stream ID, last event ID, reconnect/snapshot/fallback counts,
last disconnect reason, last REST and SSE status, and grant expiry. The full last
event ID is expandable. `window.emberObserverTransport` exposes the same diagnostic
object for black-box validation. No tokens or credentials are included.

The `embers.observer.transport` logger records stream open/close with the stream
ID, duration, chunk count and reason (`http_disconnect`, `asgi_cancelled`,
`response_closed`, `socket_write_failed`, `authorization_ended`, or recovery
failure). DEBUG additionally records generator yields and byte counts. These are
application-side observations, not claims that a browser received a chunk. Pair
with browser network receipt times and the same response stream ID to locate a
future deployed failure. Logging stays outside the memory/learning journal.

## Changed files

- `embers/api/observer_transport.js`: single-owner EventSource state machine.
- `embers/api/observer.html`: uses that owner; adds truthful visible diagnostics.
- `embers/api/observer_routes.py`: JS asset/status endpoint, SSE headers, stream
  IDs, cancellation diagnostics, explicit generator closure, transport keepalive.
- `embers/integration/research_observer.py`: handshake adds stream ID and grant
  lifetime; journal traversal, authorization/filtering and event projection unchanged.
- `embers/api/usefulness_routes.py`: permits same-origin script loading in CSP.
- `pyproject.toml`: includes the observer JS asset in distributions.
- `tests/test_research_observer.py`: status/no-renewal, headers, cancellation cleanup,
  assets; existing authorization/session/namespace tests retained.
- `examples/observer_transport_check.py`: actual browser → fault-injecting HTTP
  proxy → normal Uvicorn/ASGI test, including the original-client reproduction.
- `examples/research_observer_check.py`, `examples/consolidated_release_check.py`:
  network capture updated from fetch interception to native EventSource CDP capture.

Existing unrelated screenshot/store changes are excluded from the commit.

## Validation

Full Python regression suite: **653 passed**, one dependency deprecation warning.
The existing observer browser test also passed: follows namespaces and sessions,
zero healthy snapshot polling, raw cross-agent isolation, same-page restart,
revocation, read-only hashes and desktop/mobile screen fit.

The detailed before/after and fault-injection results are recorded in the
adjacent JSON evidence files. Tests use temporary stores and fictional memories;
separate authenticated agent clients create test events. Observation-only phases
compare all memory-store file hashes, covering journals and metadata. Capability
files remain outside that store; expiry/revocation testing does not change model
state. Tests do not silently reuse the user's deployed Test 15 records.

Reproduce:

```
python -m pytest -q
python examples/observer_transport_check.py --idle-seconds 180 --proof /tmp/observer-after.json
python examples/observer_transport_check.py --flap-only --proof /tmp/observer-flap.json
python examples/research_observer_check.py --proof /tmp/observer-regression.json
```

For the original-client reproduction, save `embers/api/observer.html` from base
commit `0560f7d` and pass its path via `--before-html`. The harness withholds
heartbeat chunks after the handshake and records browser AbortController calls.

## Deployment boundary

Publish this branch, update the normal Ember server package (including the new JS
asset), and load the updated observer page once. Existing already-open old pages
cannot acquire new JavaScript without loading it. After that, subsequent normal
server restarts must recover in the same open page without refresh.

This local fix is **not yet a verified deployed ngrok fix**. A final deployed run
must capture the stream ID, origin log reason, browser disconnect time and proxy
logs if flapping persists. Do not confuse the passing local fault-injection tests
with access to the user's reverse-proxy diagnostics.

## Recorded results

| Check | Result |
| --- | --- |
| Original client with delayed chunks | Client watchdog aborted at 10.151 seconds; snapshots rose from 1 to 2 and reconnects from 0 to 1 |
| Healthy idle, 180 seconds | One actual SSE request; same stream ID throughout; reconnects 0, snapshots 1, fallback 0 |
| Burst | 30 events across two authorized namespaces; ordered within each journal, no duplicates |
| Socket interruption | RECONNECTING then LIVE; three missed events replayed exactly once; snapshots remained 1 |
| Delayed heartbeat | 12.5 seconds without the original premature abort |
| Normal server restart | Same browser page recovered; missed event replayed; snapshots remained 1 |
| SSE unavailable, REST available | Explicit fallback after three failures; bounded five-second polling; polling stopped on SSE recovery |
| Rapid handshake/disconnect | Failure count persisted across short handshakes and reached fallback |
| Silent half-open connection | Explicit 45-second liveness diagnostic, then recovery |
| Revocation and real grant expiry | ACCESS EXPIRED; no further retry growth; no automatic authorization extension |
| Isolation | No unauthorized agent/namespace payloads in raw browser SSE |
| Observation-only activity | Memory-store hashes unchanged |
| Browser errors | None in successful final acceptance runs |

The final long-run stream ID was `a08aacb7244c43d895580be8579f167b`.
Interruption replay included
`usefulness-776501a63e0e5383bc3029fc98382c3f-000000000007`,
`...000000000008`, and `...000000000009`. Revision 6 belonged to an
excluded agent, so its absence is intentional authorization filtering. Restart
replay included `usefulness-2aa87a45731a5b04a2c52cc2ab8cc21c-000000000026`.
Revisions are ordered per namespace journal; the observer resume cursor is a
vector of journal positions, not a fabricated global model revision.

After all deliberately injected failures, the long run had six reconnects, five
snapshots, one fallback entry and seven stream attempts. These are fault-test
counters, not healthy-stream counters. A supplementary run after the final
fallback/snapshot race guard repeated the fault scenarios successfully; it had
two fallback entries because failed recovery probes explicitly pause and then
resume fallback. Its idle duration was zero; the separate long run supplies the
180-second idle evidence.

Evidence files:

- [Original cancellation reproduction](observer-transport-before.json)
- [Three-minute run, raw SSE headers and browser events](observer-transport-after.json)
- [Latest recovery/fallback/expiry regression](observer-transport-faults.json)
- [Rapid flaps and half-open detection](observer-transport-flap.json)

The public endpoint check returned an HTTP/2 proxy error, then an HTTP/1.1
ngrok 404 with `Ngrok-Error-Code: ERR_NGROK_3200`. Consequently, the exact
deployed proxy delay/abort source remains unverified. The evidence proves the
client cancellation and reconnect defects and the controlled transport fix,
not an authenticated end-to-end pass through the unavailable deployment.
