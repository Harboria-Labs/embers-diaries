# Automatic public status indicators

The observer and namespace visualizer headers automatically show API availability with a colored dot and text. A separate stream indicator follows the existing SSE connection badge; REST health cannot produce a LIVE stream verdict. The full `/status` page requires no button press.

- Green: successful public response (and status=ok for health), under 2 seconds.
- Yellow: response at least 2 seconds, non-ok health status, or HTTP blocked/partial response.
- Red: HTTP 5xx, failed network request, or 8-second timeout.
- Gray: no observation yet. Missing memory/model information does not affect health.

Public status checks run immediately, then 15 seconds after the previous cycle completes. No overlapping cycles. Hidden tabs pause; visible tabs resume. Page exit cancels only health requests. The SSE transport is unchanged.

Full-page coverage: `/health`, `/v1/observer/build`, `/observer`, `/visualizer`, `/research/settings` (HTML only), `/status`, `/v1/observer/transport.js`. Header probes only `/health`. All checks omit credentials, reject redirects, and bypass caches. No private namespace, session, token, grant, memory, retrieval, feedback, configuration API or model endpoint is probed. The page explains these exclusions: this is public availability, not a claim that every protected operation or model is healthy. API shutdown can be detected by an already-open page; an offline server cannot serve a new status page.

Implementation: shared `status_monitor.js` included in both existing rendered viewers and status page; removed the old one-shot dashboard health request and manual status button. No new API route, configuration system, memory behavior or transport implementation.

Validation: offline Chromium checks automatic repeated GETs, red failure, green recovery, yellow partial/blocked/slow states, and independent SSE badge changes. No browser errors. Dashboard tests verify served pages/public probes preserve persisted store hashes and shared SSE source hash remains unchanged. Three focused tests passed. No deployed/live validation or server startup.

Reproduce browser checks with `PYTHONPATH=. python examples/status_monitor_check.py`; set EMBER_TEST_BROWSER if using a custom browser executable. No server is started: all browser requests are intercepted.
