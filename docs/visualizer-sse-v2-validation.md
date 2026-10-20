# Visualizer V1 transport correction — SSE protocol v2

## Scope and status

This change touches observation transport, browser connection management,
diagnostics and transport tests. **Feedback formulas, U/W, deduplication, context,
truth, LADC and U→Q are unchanged.** No second server is introduced.

Implementation and isolated normal-server validation are complete. The reported
failure through the deployed tunnel remains **pending independent revalidation**;
local tests are not a claim that the deployed failure has been reproduced/fixed.

Read-only inspection of the deployed `/visualizer` returned HTTP 200 and exactly
matched the pre-change branch HTML. That HTML already included an SSE client and
no periodic `setInterval` poller. This rules out an obviously stale polling-only
HTML build, but does not identify why the independent run used REST recovery.
A subsequent unauthenticated SSE probe through the tunnel timed out after 10
seconds; no authenticated deployed stream was available to this implementation
session. No deployed memory or revision-48 evidence was changed.

## What changed

- Authenticated SSE now immediately emits an explicit `ready` handshake with
  `transport: SSE` and `protocol: ember-observation.v2`.
- `LIVE` requires receipt of that handshake through the response body; successful
  REST calls cannot set it. HTTP headers alone cannot set it either.
- SSE responses include `Cache-Control: no-store, no-transform`,
  `X-Accel-Buffering: no` and `X-Ember-Observation-Protocol: ember-observation.v2`.
- Ten-second connection/idle deadlines include the wait for response headers,
  so an intermediary holding the response open cannot leave a connection attempt
  stuck forever. EOF/abort clears `Connected` and changes the badge immediately.
- Each connection owns its abort controller and timeout. Obsolete requests cannot
  update the new connection's diagnostics. CRLF framing works across chunk splits.
- Healthy SSE makes no periodic snapshot requests. Initial load, failure recovery
  and explicit fallback retain bounded snapshots. After three failures the UI
  explicitly displays `POLLING FALLBACK`; successful SSE handshake restores LIVE.
- Authorization precedes subscriber registration and is rechecked before delivery.
- The existing durable journal append hook remains the publisher. It runs after
  the native durable write and state reduction. Transport notifications are wakeups;
  event IDs/revisions and state are read from the authoritative journal.
- Existing keyed patches, revision deduplication, stable graph node objects and
  affected-record highlights remain in use. Recovery events also highlight.

## Inspect the actual transport

Click the connection badge (`LIVE`, `RECONNECTING`, or `POLLING FALLBACK`). The
panel shows:

```
Transport: SSE
Connected: yes
Last pushed revision: 49
Last snapshot revision: 48
Last confirmed revision: 49
Reconnect count: 0
Snapshot GETs: 1
SSE connections: 1
```

Those numbers are illustrative; the panel displays the actual values. It also
shows the last transport error and pushed-event count. `window.emberTransport`
exposes the same fields and the last 100 delivery records (`SSE` or `SNAPSHOT`,
revision and event ID). Counters reset when a new view is opened. Receiving an
already-confirmed event does not reapply it; the received-event counter measures
wire delivery, not extra learning or independent outcomes.

## Transport evidence

The enhanced `examples/usefulness_sse_check.py` starts the **normal**
`uvicorn embers.api:app` with a fresh isolated store. It does not run against user
memories. It blocks the snapshot endpoint in the browser after initial load,
then commits new events externally through the API.

Checks include:

1. New revision arrives over SSE within 1.5 seconds while snapshot GETs are blocked.
2. No snapshot GET is attempted for a healthy window longer than five seconds.
3. Actual response is `text/event-stream` with the v2 protocol header.
4. SSE delivery revisions are ordered; duplicate application is a no-op.
5. Dropping the stream displays RECONNECTING; missed events apply once.
6. Restarting the actual server recovers persisted revisions.
7. Blocking SSE forces the explicit fallback badge; unblocking restores SSE.
8. An independent HTTP probe receives the same events without snapshot polling.
9. Existing namespace, authorization, revocation, credential-redaction and
   read-only tests remain passing.

A separate unit test sets journal recovery to **one hour** and still requires the
committed event within **one second**. Its publication hook asserts the record
already exists durably. This distinguishes commit wakeup from heartbeat-driven
journal discovery. Failed-write and observation-only file-hash tests remain in
place. Full test suite: **633 passed**, one dependency deprecation warning.

See `visualizer-sse-v2-browser-proof.jsonl` for the measured local browser/probe
results. Timing is a measurement from the isolated run, not a remote latency SLA.

## Independent deployed validation

Update/restart the normal server and refresh the viewer together. The browser and
server must both speak `ember-observation.v2`. Create a fresh authorized viewing
code with the existing `ember_visualizer_access` tool.

For a read-only independent observer, set `EMBER_VIEW_CODE` in your environment
and run:

```sh
python examples/visualizer_push_probe.py --url https://YOUR-EMBER-SERVER --seconds 60
```

The probe exchanges the viewing code, performs exactly one initial snapshot GET,
then reads only SSE. It prints receipt of the handshake, revisions/event IDs,
disconnections and its final GET count. Credentials are never printed. While it
runs, have the authorized agent commit a new observable event in its test namespace.
The probe's `PUSH OBSERVED` result means it received an event through the SSE body;
`NO NEW PUSH OBSERVED` is not a pass. Save its output beside the mutation request
and persisted journal response for independent comparison.

The browser Network panel should likewise show a continuously open
`/v1/visualizer-stream/...` response. While healthy, new revisions should advance
`Last pushed revision` without advancing `Last snapshot revision` or the snapshot
GET count. Preserve the diagnostics/error if fallback occurs through a proxy.

## Deployment boundary

Immediate in-process commit wakeups are verified on the normal single-process
server, including its HTTP MCP/API writers. Writes from other processes/workers
retain the existing one-second server-side journal reconciliation; that path is
SSE delivery to the browser but is not claimed to be an immediate cross-process
notification bus. Client-side snapshot polling is never used while SSE is healthy.
