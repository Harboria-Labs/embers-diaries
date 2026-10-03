# Research Observer deployment version audit — 2026-09-30

## Outcome: BLOCKED — deployed identity unavailable

None of the three requested conclusive classifications is established by the
available evidence. It would be inaccurate to label this **DEPLOYMENT VERSION
MISMATCH CONFIRMED**, **DEPLOYED CLIENT MATCHES 9f94c5a; TRANSPORT FAILURE
CONFIRMED**, or **BOTH** while every public route returns an offline-tunnel page.
The reported old watchdog is a strong version-mismatch lead, not a substituted
asset capture. No new transport redesign was performed.

The normal server and ngrok host are not accessible through this workspace.
There is no host process/venv inventory, service manager, ngrok request log or
origin transport log available here. No deployed restart or installation has
been performed. Repository publication does not update a running remote process.

## Exact version evidence

Expected client commit: `9f94c5a01d4d0ca843cfdb3a322df5968671935f`.

| Artifact | SHA-256 |
| --- | --- |
| Local baseline `embers/api/observer_transport.js` (7,987 bytes) | `6b6d262295d56d9f71546a6b79f6eeb0f0caded00fc242c5b9cd28cf1e4274e9` |
| Fresh baseline wheel JavaScript | `6b6d262295d56d9f71546a6b79f6eeb0f0caded00fc242c5b9cd28cf1e4274e9` |
| Local baseline `embers/api/observer.html` | `e4f84643a5ce5a58779f5233a793a25161df1b2da17322b0b56f650a4dc9293a` |
| Fresh baseline wheel HTML | `e4f84643a5ce5a58779f5233a793a25161df1b2da17322b0b56f650a4dc9293a` |
| Deployed JavaScript | **Unavailable — response is an ngrok error page, not JavaScript** |
| Deployed HTML / package version / git commit | **Unavailable** |

The baseline HTML references exactly `/v1/observer/transport.js`.
Canonical observer page: `/visualizer?mode=observer`. Commit 9f94c5a does not
register an `/observer` HTML route. The probe checks both paths to avoid silently
comparing a different view. No loaded deployed asset URL can be established
without obtaining the actual observer HTML.

Public asset requested:
`https://weasel-master-currently.ngrok-free.app/v1/observer/transport.js`.
Fresh no-cache checks also covered `/observer`, `/visualizer?mode=observer`, and
`/v1/observer/build`. All returned ngrok HTTP 404 / `ERR_NGROK_3200` in the final
probe. Exact UTC timestamps, response headers and error-body hashes are in
[observer-version-deployed-probe.json](observer-version-deployed-probe.json).
Error-body hashes are explicitly **not** deployed JavaScript hashes.

Representative response headers:

```
HTTP/1.1 404 Not Found
Connection: close
Content-Type: text/html
Ngrok-Error-Code: ERR_NGROK_3200
Referrer-Policy: no-referrer
```

Earlier reported HTTP 502 and the current offline-tunnel response must remain
separate from SSE correctness. These responses do not show why the tunnel went
offline: process exit, host/network failure, restart, changed tunnel credentials,
or deployment configuration cannot be distinguished without host logs.

## The two different ten-second timers

Old observer HTML at 0560f7d contains:

```javascript
timer=setTimeout(()=>active.abort(),10000)
```

That timer surrounds the streaming-fetch body reader. It is absent from the
9f94c5a observer HTML and transport JavaScript.

9f94c5a JavaScript still contains:

```javascript
const timer=setTimeout(()=>c.abort('REST timeout'),10000);
```

This belongs to the bounded REST JSON helper, not EventSource. Searching only for
`10000` or `AbortController` is insufficient to prove an old transport client.
The native SSE connection uses `new EventSource(...)`; the stream-body watchdog
from the previous HTML is removed. No timer or reconnection logic changed in
this audit; the transport JavaScript remains byte-for-byte 9f94c5a.

## Packaging, cache and process checks

- A fresh release wheel was built from the unmodified 9f94c5a checkout before
  adding diagnostics. Its HTML and JavaScript exactly match the commit.
  `pyproject.toml` includes both `embers/api/*.html` and `embers/api/*.js` in
  wheels and source distributions. Missing assets are **not reproduced** in
  that wheel. See [baseline package evidence](observer-version-baseline-package.json).
- Package version is `0.2.0`; that version alone cannot distinguish these commits.
- Baseline HTML and JS routes already specify `Cache-Control: no-store`.
  This does not prove the deployed process served those headers or that an
  already-open page loaded new code.
- No service-worker registration appears in the repository's observer code.
  The researcher's browser profile/cache/service-worker state is unavailable;
  it has not been claimed clean.
- An old local wheel artifact containing only the native-extension package was
  also found. It was not used for the fresh build and has no proven connection
  to the deployed installation. Do not treat its existence as deployment proof.
- The remote test branch was at 9f94c5a before this audit. That says nothing
  about the checkout, interpreter, installed distribution or process on the host.

## Added identity diagnostics (observer only)

These make the next deployed comparison concrete; they are not a deployment fix
claim and do not alter stream lifetimes, reconnection or Ember behavior.

1. Visible marker on both the access panel and research view:
   `Observer client: 9f94c5a / transport-v2 · Server build: sha256:<fingerprint>`.
2. Public, read-only `GET /v1/observer/build` returns the full observer-source
   fingerprint, package version, expected/actual transport hash and exact asset
   URL. It never opens a memory store or reads a grant.
3. The server build is a deterministic hash of the observer delivery/rendering
   source files. It is **not** falsely labeled as a git commit or whole-engine
   fingerprint. `server_git_commit` is explicitly null. It works in installed
   wheels without a `.git` directory.
4. HTML references `/v1/observer/transport.js?v=<full SHA-256>` with browser SRI
   `integrity="sha256-..."`. Wrong fingerprint requests return HTTP 409; altered
   cached bytes fail browser integrity verification. The unversioned asset route
   remains compatible. Responses carry no-store/no-cache/no-transform headers
   and explicit build/hash/version headers.
5. `window.emberObserverTransport` diagnostics include client version, asset hash,
   URL and server build. No credentials, filesystem paths or agent data appear
   in public build metadata.
6. Assets and their identity are captured once at process import. Copying new
   files under a still-running process cannot silently swap its client assets;
   restart the normal server after installing an update.
7. `examples/observer_version_check.py` performs credential-free public HTTP
   checks and records exact evidence. It refuses to call an error page a JS
   asset. It does not open streams or generate Ember events.

Files changed: `embers/api/observer_build.py`, `observer_routes.py`,
`usefulness_routes.py`, `observer.html`, `tests/test_research_observer.py`,
`examples/research_observer_check.py`, `examples/observer_version_check.py`, and
these audit/evidence documents. `observer_transport.js` is unchanged. No Rust,
model, memory, learning, configuration or retrieval files changed.

## Local validation

- Observer tests: **13 passed**, including fingerprint/SRI identity, mismatch
  rejection, public build inspection without any memory access, and immutable
  process asset capture. One dependency deprecation warning.
- Updated wheel built and installed into a separate target directory. From
  outside the repository, its build endpoint and asset route return the exact
  baseline JS hash and package version 0.2.0. See
  [installed-package evidence](observer-version-installed-package.json).
- Local browser validation **passed**: visible build marker, fingerprinted JS
  loading with SRI, diagnostics identity, same-page restart/catch-up, raw isolation,
  read-only state and desktop/mobile fit. The first local attempt hit the harness
  startup deadline; after checking the tokenizer cache, the rerun passed without
  application changes. Evidence is recorded separately in
  [observer-version-browser-proof.json](observer-version-browser-proof.json).
  Local browser success is not the requested deployed three-minute pass.

## Remaining deployment gate

Restore the normal server/tunnel and update the **actual interpreter/package
used by that service**, then restart that one normal server. On the host, inspect
its service command and run the following using that exact Python executable:

```bash
python -c 'import sys, embers.api, importlib.metadata as m; print(sys.executable); print(embers.api.__file__); print(m.version("embers-diaries"))'
```

Compare that location with the checkout/venv being updated; a git pull in another
directory cannot update an already-running installed package. Rebuild/install
from the intended test-branch revision instead of reusing an old wheel. Retain
host service and ngrok lifecycle logs around the reported offline interval.
Do not share bearer tokens, observer codes or unredacted credential-bearing URLs.

Then run:

```bash
python examples/observer_version_check.py --base-url https://weasel-master-currently.ngrok-free.app --proof deployed-version.json
```

Load the updated observer page once with a fresh authorized grant. Check the
loaded asset URL/hash and client/server build markers. Only after that version
gate passes, keep the same page open for at least three minutes and require one
stream, zero reconnects, one initial snapshot, zero fallback, continuous LIVE.
This required deployed test is **NOT RUN / BLOCKED**, not passed.

If it still flaps, correlate the response stream ID with browser EventSource
open/error timestamps, `embers.observer.transport` open/yield/close records and
ngrok request/connection logs. Use timestamped DEBUG server logging for each
yield; existing close reasons distinguish ASGI cancellation, HTTP disconnect,
socket failure and authorization termination. No such deployed stream ID or
origin/proxy trace was obtainable while the tunnel was offline.
