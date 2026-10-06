# Visualizer surfaces: shared transport and direct links

Base: pulled `dff6b7b` from `codex/ember-split-feedback-candidate04` before editing.
Scope: browser transport, view authorization entry, public shells and diagnostics.
No memory/math, FUR, U, N_eff, W, LADC, retrieval, truth or configuration changes.

## Confirmed deployed split

On 2026-10-01, public HTTP responses showed:

| Surface | Before behavior |
| --- | --- |
| `/visualizer` | Namespace HTML with inline streaming fetch, AbortController, ten-second body-abort watchdog, reconnect loop and snapshot recovery after failures |
| `/visualizer?mode=observer` | Observer HTML loading fixed native EventSource asset; includes `consumeObserverLink()` for `#observe=` |
| `/observer` | HTTP **404**, not a deployed HTML surface at the time of this check |
| `/v1/observer/transport.js` | Exact 9f94c5a transport asset, SHA-256 `6b6d262295d56d9f71546a6b79f6eeb0f0caded00fc242c5b9cd28cf1e4274e9` |

Deployed namespace HTML SHA-256:
`6249521cff7b0c2bbd0a2bd0957f6e55334632562491ccca7a4ae508daaffa27`.
Deployed observer-mode HTML SHA-256:
`31071678dba385ba8caed2c04d4630e461ed157c5af04d670a571c9dddb67e6e`.
The `/observer` 404 body hash is in the evidence, but is **not** an observer client hash.
See [public before headers and hashes](visualizer-surfaces-before-assets.json).

The namespace source contained exactly:

```javascript
idle=setTimeout(()=>active.abort(new Error('SSE connection timed out')),10000)
```

It also read the streaming response through `response.body.getReader()` and
performed snapshot recovery after stream failures. The literal browser error
`BodyStreamBuffer was aborted` is not application source text; the application
abort path can cause a browser-specific cancellation error. A controlled HTTP
proxy test reproduces the timeout by withholding body bytes after the handshake.

The current public observer-mode source already has fragment auto-entry. Thus the
reported manual-entry failure was **not independently reproduced against that
HTML**. The “Enter observatory” button belongs to the namespace shell. A stale
page or a lost `mode=observer` query remains a possible explanation, not a proven
one. The fix below removes ambiguity and tests every entry URL.

## One transport implementation

Both view shells now load the same fingerprinted resource:

`/v1/observer/transport.js?v=b7f69d4f06405b066fee1e65e673100665d771131e13de58e7b03b1e2f0593c0`

Source file: `embers/api/observer_transport.js`, class `EmberObserverTransport`.
Version: `shared-transport-v3`. Both pages use browser SRI and no-cache/no-store
headers. There is one EventSource owner, reconnect timer, liveness timer,
authorization-expiry handler and explicit fallback state machine. No second
transport implementation was added. The old namespace fetch/body-reader loop
was removed.

The scope configuration selects the authorized REST/SSE paths and validates the
handshake. Existing namespace messages use integer revisions and `patch` frames;
existing observer messages use namespace-vector cursors and `observer` frames.
Their existing render/apply callbacks remain separate because these are different
payload schemas. Snapshot pagination also follows each existing bounded API.
No learning or journal semantics were unified or modified.

Namespace SSE now includes a stream ID, namespace and grant lifetime in `ready`,
and named keepalives without model revisions. It explicitly closes its underlying
generator on cancellation. It uses no-transform/no-buffering/identity headers.
Neither keepalives nor these transport fields create Ember events.

## Link entry and authorization

- `/visualizer#view=<code>` exchanges the code and opens the namespace view without
  a click. Initial load and later hash navigation are supported.
- `/visualizer?mode=observer#observe=<code>` exchanges the code and opens Research
  Observer automatically. `/observer#observe=<code>` is now an equivalent alias.
- `/visualizer#observe=<code>` redirects to the observer alias while keeping the
  fragment out of HTTP requests. This handles a missing mode query explicitly.
- Codes are removed from the address bar before exchange, and cleared from the
  form on success. Invalid/expired/revoked codes show an explicit ACCESS EXPIRED
  status and explanation. Replacement links stop the previous connection; stale
  asynchronous exchange responses cannot override a newer attempt.
- Normal namespace view-code and observer capabilities retain their existing
  authorization rules. Observer and namespace cookies remain distinct.
- Existing advanced credential entry is preserved. Native EventSource cannot send
  custom headers, so `POST /v1/visualizer-access/browser-auth` validates the supplied
  agent/session credentials and carries them in an HttpOnly, SameSite cookie
  (Secure over HTTPS). Only visualizer read routes consult it. Every read rechecks
  authority and namespace/session scope. No stored grant or memory record is
  created by this exchange; write APIs do not accept this cookie. Credentials
  never enter SSE payloads or URLs.
- `GET /v1/visualizer-status/{namespace}` checks current authorization and lifetime
  without a memory snapshot, supporting terminal expiry and reconnect diagnosis.

## Final local served hashes

| Surface/asset | SHA-256 |
| --- | --- |
| `/visualizer` | `ddc4545f646f448a6b8704b4b150d1532a73394e7d635e7adea732d2888867b3` |
| `/visualizer?mode=observer` | `fc38198d371d46ea35bb5a13e68596cf26a6e1e090d0065de07665a8c8500e17` |
| `/observer` | `fc38198d371d46ea35bb5a13e68596cf26a6e1e090d0065de07665a8c8500e17` |
| `/v1/observer/transport.js` | `b7f69d4f06405b066fee1e65e673100665d771131e13de58e7b03b1e2f0593c0` |

These are local after-change response hashes, not a claim that the running ngrok
server has already installed the new commit. HTML contains the server build
fingerprint and package metadata, so HTML hashes can vary with installation
metadata. The normalized JavaScript hash is the stable transport comparison.
The two observer URLs return byte-identical HTML. Namespace HTML differs because
it renders a namespace graph, while its **transport asset is identical**.
See [after headers and hashes](visualizer-surfaces-after-assets.json).

## Validation and deployment boundary

The full Python suite passed **660 tests**, with one dependency deprecation
warning. After final input/error hardening, the focused transport/access suite
passed **34 tests**. Browser evidence is recorded in
[direct-link and three-minute proof](visualizer-surfaces-proof.json).
An additional observer browser regression verifies agent-following across two
namespaces/sessions, same-page restart/catch-up, revocation, raw cross-agent
isolation, read-only state and desktop/mobile fit.

Tests run against a real normal Uvicorn server with a temporary store and a
controlled HTTP proxy. Agent-created fixtures/grants are prepared before the
observation-only file-hash baseline. Deliberate agent feedback is separate from
observer activity. The fixture proxy was corrected to forward repeated Set-Cookie
headers separately; combining them had prevented the browser receiving its view
cookie. The application CSP was retained; browser test polling uses direct CDP
evaluation rather than requiring unsafe-eval.

No remote process-management access is available here. Publishing this branch
will not replace the user's running server. After updating/restarting the same
normal server, load each updated view once and repeat the deployed idle check.
The current public before capture proves the namespace's old path; local after
validation must not be described as a post-deployment ngrok pass.

## Changed files

- `embers/api/observer_transport.js`: shared configurable EventSource owner.
- `embers/api/visualizer.html`: removes legacy transport, uses shared owner,
  direct-link routing and explicit entry errors.
- `embers/api/observer.html`: robust replacement-link/error handling.
- `embers/api/observer_build.py`: shared asset identity and both shell templates.
- `embers/api/usefulness_routes.py`: read-only browser auth/status, namespace
  keepalives and cleanup, shared rendering, observer alias.
- `tests/test_visualizer_access.py`, `tests/test_research_observer.py`: scope,
  no-mutation, asset identity, alias and version expectations.
- `examples/visualizer_surfaces_check.py`: real-browser independent surface test.
- `examples/observer_transport_check.py`: proxy covers namespace SSE and preserves
  repeated cookie headers.
- `examples/research_observer_check.py`: accepts the new shared version marker.

Reproduce:

```bash
python -m pytest -q
python examples/visualizer_surfaces_check.py --proof /tmp/surfaces.json
python examples/research_observer_check.py --proof /tmp/observer.json
```

## Recorded before/after counters

Old namespace client, delayed-body reproduction: reconnects **1**,
snapshot GETs **2** after 12.5 seconds. It entered the
legacy timeout/recovery path. This is a controlled reproduction, not a new
measurement of Grok's browser.

After **180 seconds**, independently per view:

| Surface | SSE requests | Reconnects | Snapshot GETs | Fallbacks | Status |
| --- | --- | --- | --- | --- | --- |
| namespace | 1 | 0 | 1 | 0 | LIVE |
| observer-mode | 1 | 0 | 1 | 0 | LIVE |
| observer-alias | 1 | 0 | 1 | 0 | LIVE |

All links entered without manual code entry and cleared their fragments. Invalid
links displayed ACCESS EXPIRED. Query-stripped observer links reached the alias.
All three views received the subsequent committed event without increasing
snapshot/reconnect counts. JavaScript errors: **0**. Observation-only memory file
hashes: **unchanged**.

The three-minute run and additional observer regression record their actual
server fingerprints. Subsequent server-only malformed-cookie/status error
guards were covered by the final focused tests; the shared JavaScript hash is
identical in the browser proof and final asset report.
