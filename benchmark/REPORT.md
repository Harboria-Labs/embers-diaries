# Ember live benchmark — 13 November 2026

**Run id:** `run-20261113-161001`  
**When:** 16:10:01–16:46:23 WAT (Africa/Lagos, UTC+1), on the shared box against `http://localhost:9200` (Ember 0.2.0, git `f05ed04`, branch `codex/ember-split-feedback-candidate04`).  
**Who:** a newly registered benchmark agent (`agent-fcdcc04a-d413-4c57-b313-eb8f30d5f6c8`, name `bench-ember-2026-11-13`).  
**Where the data went:** only `bench-2026-11-13-20261113-161001-scale` and `bench-2026-11-13-20261113-161001-features`. The live agent is **Grok** (`grok-demo-agent`, `agent-bd98fca7-0c95-45df-a55f-063e41bc6831`). This run did **not** write to Grok’s `memories` namespace, did **not** restart uvicorn or ngrok, and did **not** touch Grok’s records.

Every figure below comes from an actual request, with raw logs in `benchmark/results/run-20261113-161001/`. If something failed or was not tested, it is said so.

---

## Executive summary

We wrote **5,000 synthetic memories** into a fresh `bench-*` namespace (0 write failures), and measured recall accuracy and latency at 1,000, 2,500 and 5,000. We also exercised conflicts, supersession, lobby, proposals, MCP, auth, error limits, a **simulated** Grok-style agent loop (explicitly not Grok), and a read-only integrity audit of our own namespaces. In parallel, a headless recorder sat on Grok’s Research Observer and Grok’s Namespace View of `memories`, using observation links Samuel issued around 16:04 WAT (due to expire around 17:04 WAT).

**Accuracy** on 195–715 labeled questions was stable as the store grew: **recall@1 ≈ 0.51**, **recall@5 ≈ 0.74–0.79**, **MRR ≈ 0.61**. Paraphrase questions (the realistic ones) were strong at @5/@10 (0.91 / 0.97 at 5k) but only about half correct at @1. Unique-token questions split cleanly: Ember’s **full-text search found 200/200 at rank 1**; Ember’s **recall path missed 140 of those same 200** (all “lab notebook TOK…” questions) even though the token was in the question. That is a real product finding, not a scoring bug — confirmed by a follow-up comparison at 16:49:50 WAT (`exact-search-vs-recall.json`).

**Latency and throughput got worse as the store grew**, while cheap `/health` stayed fast (~3 ms). A batch of 100 writes took **2.0 s at the start (49.7 writes/s)** and **42.7 s at 5,000 (2.3 writes/s)**. Recall p50 went from **147 ms at 1k** to **692 ms at 5k**.

**Impact on Grok:** Grok itself did not write during our load. The last WAL event for `agent-bd98fca7-…` is **16:03:47 WAT**, and that count (178) was unchanged through a 17:02 WAT re-check. The live uvicorn log showed **no HTTP 5xx**. What *did* change is Grok’s already-open Namespace View: it went from **LIVE** (97 visible memories, at 16:06:24 WAT — sharp-header frame in `media/`) to **RECONNECTING** during the 1k write phase, and that long-lived page was still reconnecting at 17:01. A *fresh* load of the same still-valid grant at **16:59:31 WAT** came back **LIVE**, still with 97 visible memories, “5 min left” on the countdown, and **API: Slow**. So the grant itself was fine; the long-lived SSE session dropped under load and did not recover on that page.

**Not ready to compare to Mem0, Zep, or Letta** — those adapters are empty stubs, and we did not invent numbers for them.

---

## What was tested, and how

- **Dataset** (`benchmark/dataset/`, seed `20261113`): 5,000 fictional facts (people, organisations, conflicts, superseding headquarters, multi-hop river/city/country chains, near-duplicates, Unicode, inventory filler) and 715 labeled queries (paraphrase, exact token, distractor, conflict, supersession, multi-hop, near-duplicate, Unicode). 195 of those queries become valid by 1k, 400 by 2.5k, all 715 by 5k. Unique `TOK#####` codes are a ceiling test; paraphrase questions do not contain them.
- **Scale path:** 20 single writes (for single-write latency), then batches of 100, concurrency 1, against localhost only. Checkpoints at 1k / 2.5k / 5k: write the next slice, then score every already-valid query with `POST /v1/memory/recall` (`top_k=10`). `/health` was sampled between phases; we would have backed off if p95 rose above 200 ms or 5× baseline. It never did.
- **Correctness metric:** a question scores if the Ember id of a labeled relevant memory appears in the top 1 / 5 / 10 (recall@k) and at what rank (MRR). This is identity, not “did the answer text match.”
- **Other surfaces:** a second `bench-*-features` namespace was used after the 5k write for conflicts, lobby, proposals, feedback, MCP vs REST, errors, auth, a simulated agent loop, and a bench-only integrity audit.
- **Grok:** passive read of `ember_store/wal.jsonl` and `/tmp/uvicorn.log` (live process) plus `/tmp/embers-uvicorn.log` (earlier process today — the path originally named). Plus visual recording of the two pages Samuel issued. No writes, no feedback, no conflict resolution on `memories`.
- **Tunnel:** a separate 8-request `/health` sample against the public ngrok URL, not mixed into localhost numbers.

How to rerun is in `benchmark/README.md`. One command: `python -m benchmark.run --target ember-rest --scale 5000`.

---

## Accuracy as the store grew

Source: `summary-1000.json`, `summary-2500.json`, `summary-5000.json`. 0 recall HTTP errors at every checkpoint.

| Scale | Questions | recall@1 | recall@5 | recall@10 | MRR | recall p50 | p95 | p99 (ms) |
|------:|----------:|---------:|---------:|----------:|----:|-----------:|----:|---------:|
| 1,000 | 195 | 0.518 | 0.759 | 0.790 | 0.616 | 147 | 165 | 186 |
| 2,500 | 400 | 0.518 | 0.785 | 0.795 | 0.621 | 367 | 454 | 506 |
| 5,000 | 715 | 0.506 | 0.738 | 0.775 | 0.606 | 692 | 847 | 915 |

Headline accuracy barely moved. Latency did: recall p50 is about **4.7×** slower at 5k than at 1k.

### By question type at 5,000 (recall path)

| Type | n | @1 | @5 | @10 | MRR |
|------|--:|---:|---:|----:|----:|
| Paraphrase | 261 | 0.490 | 0.912 | 0.966 | 0.661 |
| Exact token (recall path) | 200 | 0.300 | 0.300 | 0.300 | 0.300 |
| Distractor | 59 | 0.237 | 0.847 | 0.966 | 0.537 |
| Conflict | 75 | 0.960 | 1.000 | 1.000 | 0.972 |
| Supersession (before MCP update) | 30 | 0.933 | 1.000 | 1.000 | 0.967 |
| Multi-hop (both hops as relevant) | 10 | 0.700 | 1.000 | 1.000 | 0.833 |
| Near-duplicate | 70 | 0.700 | 0.843 | 0.914 | 0.769 |
| Unicode | 10 | 0.400 | 0.600 | 0.600 | 0.500 |

**How to read this.** Paraphrase @5/@10 is the encouraging number: if an agent looks at five or ten memories, it almost always sees the right one. @1 is only about a coin flip. Distractor @1 got *worse* as the store grew (0.53 at 1k → 0.24 at 5k): more similar names in the pile. Conflict questions almost always retrieved at least one of the two contradictory records.

**Exact tokens, and an important split.** An earlier version of the dataset asked for a slightly wrong token on 140 “lab notebook” questions (a harness bug: the token in the question did not match the token in the memory). We **fixed the questions** to use the token that was actually written, and re-scored. The 1k exact-token score stayed **0**. A live probe at 16:49 WAT then showed why:

- `GET /v1/memory/search?q=TOK000010D7B` returned the right record at **rank 1**.
- `POST /v1/memory/recall` with “What does lab notebook TOK000010D7B refer to?” **did not** put that record in the top 10.

Across all 200 exact-token questions (`exact-search-vs-recall.json`):

| Path | @1 | @10 | p50 latency |
|------|---:|----:|------------:|
| Full-text **search** (token only) | **1.00** (200/200) | **1.00** | 65 ms |
| **Recall** (full question, including the token) | 0.30 | 0.30 | 673 ms |

The 140 misses are all the “lab notebook TOK…” people-discovery questions. The 60 filler “inventory item TOK…” questions hit @1 on both paths. So Ember *has* the record and can find it with search; the recall/rerank path can bury a unique exact token when the surrounding wording is generic. Search is also about **10× faster**. This is the strongest correctness finding in the run.

**Supersession.** REST `GET /v1/memory/read/{id}` does **not** return `content_hash`, which `PUT /v1/memory/{id}` requires, so the first attempt to update 30 headquarters facts failed (30/30, `no_content_hash_via_rest`). MCP `ember_read` *does* return the hash. We then applied all **30/30** updates via MCP. After the update, recall@1 of the (new) current id was **0.933**. History called on the *new* id returned length 1 and did not list the old id in that sample — we report that as measured, and did not have time to also call history on the old id.

---

## Write latency and throughput

Source: `summary-5000.json`, `_run.stdout`. 5,000 writes, **0 failures**.

**Single writes** (first 20 only, store almost empty):

- p50 **18.9 ms**, p95 **25.2 ms**, p99 **25.5 ms**, mean 19.6 ms, wall 0.39 s.

**Batches of 100** (50 batches, the whole climb from 20 to 5,000):

- Batch latency: min 2.01 s, p50 **22.3 s**, p95 **42.3 s**, p99 **42.7 s**.
- Throughput: first batch **49.7 writes/s**, last batch **2.3 writes/s**, overall p50 **4.0 writes/s**.

The slowdown is steady and large. It is the main scalability result. `/health` p95 was 3.2 ms at baseline, 7.3 ms just after the 1k writes, and **2.9 ms after 5k** — the API’s cheap path stayed fine; writes and recalls are what stretch.

Write wall-clock: 64 s to reach 1k (after the 20 singles), 257 s for the 1k→2.5k slice, 853 s for the 2.5k→5k slice. Scoring walls: 29 s, 150 s, 500 s.

---

## Other features (features namespace)

Source: `features.json`, `supersession-mcp.json`, `errors.json`, `auth.json`, `mcp_compare.json`, `agent_loop.json`. Namespace: `bench-2026-11-13-20261113-161001-features`.

**Worked (HTTP 200, as designed):**

- Conflicting pair written, mapped (`conflict_id` issued), listed in open conflicts (count 1), resolved — all 200, on our records only.
- Orient (187 ms), search (81 ms), query (count 5, 38 ms), lifecycle, graph, sessions.
- Lobby v1 publish + status; lobby v2 scope create + list.
- Propose (`proposal_id` issued) and list proposals.
- Report failure and list failures (list took **8.3 s** — unusually slow).
- MCP `ember_visualize` and `ember_visualizer_access`, and the REST equivalents — all 200, codes issued and **not** logged.
- Integrity audit **only** on the two bench namespaces: POST 200 in **7.9 s**, `run_id=defc3046-…`. A later GET reported `records_examined=11728`, `finding_counts` empty, 0 findings, `certified=false`, `status=stale`. We report those fields as the API returned them; we did not audit Grok’s namespace.

**Failed or incomplete (honest):**

- **Feedback** `useful` / `irrelevant` returned **400** (our body did not satisfy the contract; we did not guess and retry). A recall after the failed call did change order, but that must **not** be attributed to feedback.
- Lobby v2 item submit **400** (request shape).
- Epistemic state **422**, pair-relationship **400** (request shape).
- Simulated agent loop (orient → recall → write → feedback) ran in 754 ms; **feedback** was the step that failed, so `all_ok=false`. This is a **simulated Grok-style workflow, not Grok**.

**MCP vs REST** (20 writes + 20 recalls of fresh compare items, 51 MCP tools listed):

| Op | REST p50 / p95 (ms) | MCP p50 / p95 (ms) |
|----|--------------------:|-------------------:|
| Write | 207 / 233 | 212 / 245 |
| Recall | 253 / 282 | 253 / 264 |

The two surfaces are in the same band. MCP initialize was 2.6 ms.

**Auth and ACL:**

- No auth on write: **401**. Bad token: **401**. Wrong agent id: **401**.
- A second bench agent (`bench-ember-intruder-2026-11-13`) **could both recall and write** in our unregistered `bench-*` features namespace (both 200). Ember’s documented default is that unregistered namespaces are open. That is a measured result on *our* namespace, not a test against `memories`.
- We did not create a PRIVATE namespace (no REST for that) and we did **not** use Grok’s `memories` to test denial.

**Error handling and limits:**

- Empty query: 400. Missing content: 400. Malformed JSON: 422. Batch of 101: **400**, message `batch exceeds max_write_batch (100); received 101` — correct.
- Nonexistent read: 404. Nonexistent feedback: 400.
- Unicode write: 200. A 200k-character write: **200**. A **~2 MB** raw body write: **200** and an id was returned. The live config has `max_record_bytes = 0` (unlimited). If a limit was expected, it is not enforced here.

---

## Grok’s real usage (passive, read-only)

Sources: `grok/snapshot-*.json`, built from `/workspace/embers-diaries/ember_store/wal.jsonl` and the two uvicorn logs. **No memory contents, no tokens, no IPs** are copied here.

**WAL, Grok only** (`agent-bd98fca7-0c95-45df-a55f-063e41bc6831`, registered name `grok-demo-agent`, provider xai):

- **178** WAL write events, unchanged from the 16:10:01 WAT “before load” snapshot through 16:46:23 WAT “post-run end”.
- Namespaces: almost all in `memories` (the live snapshot groups 178 events; a pre-run inspection saw 176 in `memories` and 1 in `_ember_agents`).
- Record types (pre-run inspection of the same agent): document, session, and a few raw. Creation reasons we can name without quoting content: “session memory write logged”, “ELLA stress corpus”, “agent registration”, “session started”, “demo memory for live recall”.
- First event: **13:36:14 WAT** (12:36:14 UTC). Last event: **16:03:47 WAT**. Grok was active today, including a last write shortly before the observation links were issued, and then **idle for the entire benchmark**.

**Uvicorn logs.** The live process (pid 52604, started 15:20 WAT) writes to **`/tmp/uvicorn.log`**, not `/tmp/embers-uvicorn.log`. The named file is the **earlier** process (shut down 15:01 WAT) and is still useful for Grok’s morning/midday MCP traffic:

- `/tmp/embers-uvicorn.log` (earlier): **305** `POST /mcp`, statuses 200 (269), 202 (100), 404 (9), 400 (1), 405 (1). **No timestamps and no per-request latency** in these logs, and no agent id, so we cannot attribute each `/mcp` to Grok with certainty — but this was before our harness existed, and Grok is the agent that was using MCP.
- `/tmp/uvicorn.log` (live, during our run): `POST /mcp` stayed at **13** all through the 5k scale (our scale path is REST). It rose to **118** by post-run end, which is **our** MCP compare / visualize / supersession work, not Grok. **Zero HTTP 5xx** in every snapshot. 401/400/422 ticks at post-run are our auth and error tests.

**Live impact while we loaded the store:** Grok’s own write rate did not change (it was already zero; still 178 WAL writes, last at 16:03:47 WAT, at a 17:02 re-check). Grok’s already-open **Namespace View** did: LIVE with 97 visible memories at 16:06:24 WAT, then **RECONNECTING** from the 1k write phase (16:10–16:11). That long-lived page (the recorder started at 16:06:18) stayed RECONNECTING through 17:01, with the page body shrinking (bodyLen ~9940 down to ~2010). A fresh navigation to the same grant at 16:59:31 WAT — still unexpired, countdown “5 min left” — connected **LIVE** again with the same 97 memories; the API badge on that fresh page said **Slow** (uvicorn was still around 56% CPU and ~1.9 GB RSS after the 5k load). The Research Observer stayed up on both the long-lived and fresh pages (Stream LIVE, “0 visible memories / Agent not observed” — consistent with a grant that follows *future* Grok activity, and Grok being idle). `/health` on the API stayed green and fast (~2.5 ms at 17:01). We did not see Grok errors in the WAL or 5xx in the log, because Grok was not making requests.

**What would be needed to benchmark Grok directly:** Grok’s own consented tool-call log, or a durable MCP access log that attributes each call to `agent-bd98fca7-…` with per-request latency. Uvicorn’s default access log has neither agent ids nor durations.

---

## Visual evidence

A headless Playwright + Chrome recorder was started at **16:06:18 WAT**, before the load, on both pages Samuel issued (localhost, not ngrok), with `span_sessions` as issued. Codes were never printed, never written into this report, and never left in saved URLs (`#observe=<redacted>`, `#view=<redacted>`). Code fields were hidden on the page; committed frames have a **heavily blurred body** so memory text cannot be read.

Phase-boundary and later frames (WAT times in the filenames): `benchmark/results/run-20261113-161001/media/`, with captions in that folder’s README.

### Grok’s pages (recorded during 1k / 5k)

| When (WAT) | Source | What was observed |
|------------|--------|-------------------|
| 16:06:24 | Sharp-header frame, first open | Observer: Stream LIVE, 0 visible memories, “58 min left”. Namespace: **LIVE**, **97 visible memories**, `memories`, Stream LIVE, API Healthy. See `20261113-160624-open-initial-*-redacted.png`. |
| 16:10:02–16:11:11 | Recorder page-health (1k writes) | Namespace went **RECONNECTING**. Mid-run committed PNGs from this session have CSS-blurred chrome (a blur used to hide memory text was not fully cleared after the first shot); the status is from the recorder log, not from a readable header. |
| 16:11–16:46 | Recorder page-health at 2.5k / 5k / post-run boundaries | That long-lived Namespace page stayed RECONNECTING (bodyLen fell from ~9940 to ~2010). Observer stayed up, 0 memories (Grok idle). |
| 16:46:17 | Recorder status | `expired=false` on both pages. |
| 16:59:30–16:59:31 | Sharp-header **fresh load** of the same grant | Still unexpired: countdown **“5 min left”** (consistent with ~17:04–17:05 expiry). Namespace **LIVE** again, still **97 visible memories**, Stream LIVE, **API: Slow**. Observer Stream LIVE, 0 memories, API: Slow. The long-lived recorder page was *still* RECONNECTING at this moment. See `20261113-165930-grok-*-redacted.png`. |
| 17:03:01 | Sharp-header, already-open fresh pages | Still valid: Observer countdown **“1 min left”**, Stream LIVE, 0 memories. Namespace **LIVE**, **97 visible memories**. See `20261113-170300-pre-expiry-1703-*-redacted.png`. (A naive `/expir/` regex also fired here — that was a false positive; the pages were live.) |
| 17:03:34 | Watcher poll | Still LIVE, namespace bodyLen 9941. |
| **17:03:50** | Watcher poll | **Expiry.** Namespace connection text became **ACCESS EXPIRED**, bodyLen dropped 9941 → 1629. Observer also flipped. This is between 17:03:34 and 17:03:50 WAT, in line with “1 min left” at 17:03:01 and the expected ~17:04 expiry. |
| 17:04:48–17:05:51 | Sharp-header fresh navigation | Both pages **Stream · ACCESS EXPIRED**, 0 visible memories, “No namespace observed”. See `20261113-170542-expired-clear-*-redacted.png`. The original recorder page, still open since 16:06, had not yet surfaced the expiry badge (it stayed RECONNECTING / `expired=false` through at least 17:04:51). |

The grant itself survived the load; what failed was the already-open SSE session on the Namespace View, which did not recover without a fresh navigation.

### Our `bench-*` pages (after 5k, not during write growth)

We did **not** record our own Observer / Namespace View while the 1k and 5k writes were in flight. Codes for those pages were issued at 16:45 during post-run and were correctly never written down; we did not re-run 5,000 writes just to film growth. At 16:56–17:00 WAT we issued new codes for our own agent and captured the store as it then stood:

| When (WAT) | Frame | What the header shows |
|------------|-------|------------------------|
| 16:56:16 | `20261113-165616-bench-observer-redacted.png` | Our Research Observer, Stream LIVE, **0 visible memories**, “Agent not observed”, “No namespace observed”. The grant follows *future* activity; the 5k writes were already finished. |
| 16:56:16 | `20261113-165616-bench-scale-ns-redacted.png` | Scale namespace, **100 visible memories**, page 1/5, **RECONNECTING**, API Healthy. 5,000 records were written (see `raw.jsonl`); the view paginates. |
| 16:59:31 | `20261113-165930-bench-features-ns-redacted.png` | Features namespace, **LIVE**, **47 visible memories**, 1 recorded relationship, page 1/2, Stream LIVE, API: Slow. |
| 17:00:37 | `20261113-170037-bench-scale-ns-redacted.png` | Scale namespace after a longer settle: still **RECONNECTING**, still 100 visible, page 1/5, API Healthy. |

**Raw video and unblurred screenshots are only on this box**, not in the repo, because they may show memory text from `memories`:

- `/workspace/bench-media-private/raw-screenshots/`
- `/workspace/bench-media-private/raw-video/` (flushed when the long-running recorder is stopped)
- `/workspace/bench-media-private/bench-pages/` and `/workspace/bench-media-private/sharp-capture/`

Links Samuel issued were created ~16:04 WAT and expected to expire ~17:04 WAT. They were still valid at 16:59:31 (“5 min left”) and at 17:03:01 (“1 min left”). They **expired between 17:03:34 and 17:03:50 WAT**: at 17:03:50 a still-open fresh page’s Namespace connection text became **ACCESS EXPIRED** (bodyLen 9941 → 1629). A new navigation at 17:04:48–17:05:51 confirmed **Stream · ACCESS EXPIRED** on both pages. Details and the poll log are in `grok/expiry-watch.json`. The long-running recorder was then stopped so its private video could flush; that video is not in the repo.

---

## Tunnel sample (separate, not mixed in)

`tunnel_sample.json` — eight `GET /health` requests to `https://igloo-flaring-subpar.ngrok-free.dev` at 16:46 WAT: **8/8 ok**, p50 **99.5 ms**, p95 **166 ms** (localhost `/health` p50 is about 2.5–3 ms). The tunnel was usable in this short sample; we did not put load through it.

---

## Limits and caveats

- **Synthetic data**, one box, one live shared server. Results are Ember as deployed today on this host, not a published law and not a comparison with other products.
- **Single-write latency** is only from the first 20 writes. We did not repeat singles at 5k.
- **Exact-token @1 of 0.30 on recall** must be read with the search comparison above. An earlier harness bug (wrong token in 140 questions) was fixed before the re-score; the remaining miss is Ember’s recall path, not that bug.
- **Supersession “current wins”** is after a post-run MCP update, not during the original 5k scoring.
- **Feedback** was not successfully applied (400), so we have no evidence that useful/irrelevant changes recall.
- **Grok was not directly measured as an agent.** The agent-loop is labeled simulated. Grok was idle during our load; the visual impact is on the observation surfaces.
- **list_namespaces** returned 200 but our parser counted 0 registered names (unregistered namespaces may simply not appear). Evidence that we did not touch `memories` is the unchanged Grok WAL count, not that listing.
- Uvicorn logs have **no per-request latency** and **no agent id**.
- Tick-to-hour and LADC questions from earlier AMMM work are out of scope here.
- Mem0 / Zep / Letta were **not run**.

---

## Evidence map

| File under `benchmark/results/run-20261113-161001/` | What it is |
|-----------------------------------------------------|------------|
| `raw.jsonl` (674 KB) | Per-request log for the scale run (op, latency_ms, status, ids). No tokens. |
| `raw-post.jsonl` (155 KB) | Post-run features, errors, MCP, rescore. |
| `summary-1000.json`, `summary-2500.json`, `summary-5000.json`, `summary.json` | Checkpoint and final aggregates. |
| `uid_to_id.json` | Dataset uid → Ember id. |
| `exact-search-vs-recall.json` | The 200-question search vs recall comparison. |
| `rescore-exact-token-fixed.json` | Re-score after fixing question tokens. |
| `supersession-mcp.json` | 30/30 MCP updates + recall after. |
| `features.json`, `errors.json`, `auth.json`, `mcp_compare.json`, `agent_loop.json`, `tunnel_sample.json` | Other surfaces. |
| `grok/snapshot-*.json` | Passive WAL + log aggregates (no contents), including 17:02 pre-expiry and 17:07 after-expiry re-checks (Grok still 178 writes, last 16:03:47 WAT). |
| `grok/expiry-watch.json` | Health-only poll around the 17:04 expiry window (no codes, no memory text). |
| `health-baseline.json`, `health-post.json` | `/health` probes. |
| `media/` | Redacted screenshots: Grok pages during 1k/5k (sharp opening + 16:59 fresh load; mid-run phase frames) and our `bench-*` pages after 5k. Captions in `media/README.md`. |
| `../../dataset/` | memories.jsonl, queries.jsonl, ground_truth.json. |

---

## Bottom line

Ember accepted 5,000 bench writes with no errors and held paraphrase retrieval at about **0.91 @5** as the store grew, which is usable for an agent that looks at a short list. Two things will matter as Ember is used harder: **write and recall get substantially slower by 5k**, and **recall can miss a unique token that search finds in one hit**. Grok, the live agent, was idle during the load (last memory write 16:03:47 WAT) and produced no errors we can see; Grok’s Namespace View, however, spent the load in **RECONNECTING**. Nothing in this run is a comparison with Mem0, Zep, or Letta.
