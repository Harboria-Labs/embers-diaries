# Redacted visual evidence

Frames in this folder are commit-safe: a sharp header strip where we could
keep one (counts, LIVE / RECONNECTING, expiry countdown, API health) and a
heavily blurred body so memory titles and text cannot be read. Access codes
never appear — the recorder hid code fields and stripped URL fragments, and
every committed frame is post-processed with a Gaussian blur on the body.

All timestamps in the watermark and in filenames are Africa/Lagos (WAT, UTC+1).

## What is in the repo, and what is not

**In the repo:** these redacted PNGs only.

**Not in the repo:** raw (unblurred) screenshots and Playwright videos. They
may contain memory text from Grok's `memories` namespace and live only on
this box, outside the repository:

- `/workspace/bench-media-private/raw-screenshots/`
- `/workspace/bench-media-private/raw-video/` (flushed when the long-running
  Grok recorder stops)
- `/workspace/bench-media-private/bench-pages/` and
  `/workspace/bench-media-private/sharp-capture/` (later captures)

## Two recorders, two timings — please read this

1. **Grok's pages, during the 1k / 2.5k / 5k load.** A headless Playwright
   recorder was started at 16:06:18 WAT, before writes, on the Research
   Observer and Namespace View links Samuel issued (~16:04 WAT, expected
   to expire ~17:04 WAT). Phase-boundary screenshots were taken on that
   long-lived session.

   After the first screenshot, a CSS-blur used to protect memory text was
   not fully cleared, so **mid-run raw frames (from 16:10 onwards) have a
   blurred chrome**. Counts and LIVE / RECONNECTING for those moments come
   from the recorder's page-health log (`/workspace/bench-media-private/logs/recorder.log`
   and `/tmp/bench-recorder-ctrl/status.json`), not from readable headers
   on those particular PNGs. The opening frames and a later fresh-load
   set do have sharp, readable headers (listed below).

2. **Our `bench-*` pages, after the 5k load, not during it.** We did not
   have a recorder on our own Observer / Namespace View while the 1k and
   5k writes were happening (codes issued at 16:45 during post-run were
   correctly never logged, and we did not re-run 5,000 writes). At
   16:56–17:00 WAT we issued new codes for our own agent and captured the
   already-written store. These frames are the post-5k state.

## Sharp-header frames (readable counts)

| File | What the header shows |
|------|------------------------|
| `20261113-160624-open-initial-namespace-redacted.png` | Grok Namespace View at **16:06:24**, before our load. **LIVE**, **97 visible memories**, namespace `memories`, Stream LIVE, API Healthy, “58 min left”, READ ONLY, page 1/5. |
| `20261113-160624-open-initial-observer-redacted.png` | Grok Research Observer at **16:06:24**. Stream LIVE, **0 visible memories**, “Agent not observed”, “58 min left”, API Healthy. |
| `20261113-165930-grok-namespace-redacted.png` | Same Grok grant, **fresh page load at 16:59:31**. **LIVE** again, still **97 visible memories**, “5 min left”, Stream LIVE, **API: Slow**. The long-lived recorder page (open since 16:06) was still **RECONNECTING** at this moment. |
| `20261113-165930-grok-observer-redacted.png` | Fresh Observer load at **16:59:30**. Stream LIVE, 0 visible memories, “5 min left”, API: Slow. |
| `20261113-165616-bench-observer-redacted.png` | Our agent’s Research Observer at **16:56:16**, after 5k writes. Stream LIVE, **0 visible memories**, “Agent not observed”, “No namespace observed”. The grant follows *future* activity; the writes were already done. |
| `20261113-165616-bench-scale-ns-redacted.png` | Our scale namespace at **16:56:16**. **100 visible memories**, page 1/5, **RECONNECTING**, API Healthy, namespace `bench-2026-11-13-20261113-161001-scale`. (5,000 records were written; the view paginates.) |
| `20261113-165930-bench-features-ns-redacted.png` | Our features namespace at **16:59:31**. **LIVE**, **47 visible memories**, 1 recorded relationship, page 1/2, Stream LIVE, API: Slow, namespace `bench-2026-11-13-20261113-161001-features`. |
| `20261113-170037-bench-scale-ns-redacted.png` | Scale namespace again at **17:00:37** after a longer settle. Still **RECONNECTING**, still **100 visible memories**, page 1/5, API Healthy. |

| `20261113-170300-pre-expiry-1703-grok-observer-redacted.png` | **17:03:01**, “**1 min left**”, Stream LIVE, 0 visible memories, API Healthy. |
| `20261113-170300-pre-expiry-1703-grok-namespace-redacted.png` | **17:03:01**, still **LIVE**, still **97 visible memories**. |
| `20261113-170542-expired-clear-grok-observer-redacted.png` | **17:05:46**, after expiry. **Stream · ACCESS EXPIRED** (red), 0 visible memories, “No namespace observed”. |
| `20261113-170542-expired-clear-grok-namespace-redacted.png` | **17:05:51**, **Stream · ACCESS EXPIRED**, 0 visible memories, “No namespace observed”. |

Expiry happened **between 17:03:34 and 17:03:50 WAT** (see REPORT.md and
`../grok/expiry-watch.json`). The 17:03:01 frames are the last sharp
pre-expiry evidence; the 17:05:46–17:05:51 frames confirm a fresh load
of the same grant no longer connects.

## Mid-run phase frames (Grok, CSS-blurred chrome)

These watermarks carry the WAT timestamp and the page name. The body is
blurred on purpose. The header chrome is also blurred (recorder bug,
above). They document that the recorder was sitting on both Grok pages at
each phase boundary:

16:10:02 load-start, 16:10:04 1k-write-start, 16:11:11 1k-writes-done,
16:11:38 1k-recall-done / 2.5k-write-start, 16:15:58 2.5k-writes-done,
16:18:27 2.5k-recall / 5k-write-start, 16:32:41 5k-writes-done,
16:41:04 5k-recall / load-complete, 16:41:18 post-run-start,
16:45:38–16:45:57 features, 16:46:22 post-run-done.

From the recorder’s page-health (not from these headers): Grok’s Namespace
View went from LIVE (16:06:24) to **RECONNECTING** during the 1k write
phase (16:10–16:11) and that long-lived page stayed RECONNECTING through
17:00. The Observer stream stayed up, with 0 visible memories (Grok was
idle; the grant follows future activity).
