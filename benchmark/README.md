# Ember benchmark harness

A repeatable, evidence-backed benchmark of Ember (and, later, other memory
systems through the same tasks). Every published number comes from a real
run and is traceable to a raw log.

## One-command run

From the repository root, with a venv:

```bash
python -m venv .venv
.venv/bin/pip install -r benchmark/requirements.txt
# Playwright video needs:  .venv/bin/playwright install ffmpeg
# Chrome is used via Playwright's channel="chrome" (system google-chrome).

.venv/bin/python -m benchmark.dataset.generate --out-dir benchmark/dataset --scale 5000 --seed 20261113
.venv/bin/python -m benchmark.run --target ember-rest --scale 5000 --checkpoints 1000,2500,5000 \
    --base-url http://localhost:9200
```

Config defaults are in `benchmark/config.toml`. Results go to
`benchmark/results/run-<WAT-timestamp>/`.

After a scale run, optional post-phase (features, errors, auth, MCP compare,
simulated agent loop, bench-only integrity audit, exact-token rescore):

```bash
.venv/bin/python -m benchmark.tasks.post_run --run-dir benchmark/results/run-YYYYMMDD-HHMMSS
```

## Targets

| `--target` | Status |
|------------|--------|
| `ember-rest` | Implemented. Used for the 13 Nov 2026 live run. |
| `ember-mcp`  | Adapter implemented (`adapters/ember_mcp.py`); used in the post-run compare. The scale runner currently drives REST. |
| `mem0`, `zep`, `letta` | Stubs only. Selecting them exits with an error. **Do not invent results.** |

## Safety (live Ember)

- Register your own agent. Write **only** into `bench-*` namespaces.
- **Never** write, update, deprecate, resolve conflicts, or give feedback in
  `memories` (Grok’s live namespace) or any other non-bench namespace.
- Read-only listing of namespace *names* is allowed only to avoid collisions.
  Do not fetch records from non-bench namespaces.
- Cap concurrency (start at 1–4). Watch `/health`. Do not restart uvicorn or ngrok.
- Do not commit tokens, observation codes, or Grok’s memory text.
  Credentials go in `benchmark/.secrets/` (gitignored).

## Layout

```
benchmark/
  run.py                 # python -m benchmark.run
  config.toml
  REPORT.md              # latest written-up live run
  dataset/               # committed JSONL + ground truth (seed 20261113)
  adapters/              # ember_rest, ember_mcp, stubs
  tasks/post_run.py      # features / errors / MCP / Grok-style loop
  media/                 # recorder helpers (optional)
  results/run-*/         # raw JSONL, summaries, redacted screenshots
```

## Latest live run

See `REPORT.md` (run `run-20261113-161001`, 13 Nov 2026, 16:10–16:46 WAT,
5,000 writes on localhost:9200). Raw evidence is under
`results/run-20261113-161001/`.
