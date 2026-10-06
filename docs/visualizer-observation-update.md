# Ember visualizer / observer update

This work changes observation and presentation only. It does not add Human Identity or change Ember's mathematical calculations, memory content, contextual semantics, retrieval, truth, feedback, activation, or research configuration. Human Identity remains out of scope. Viewing access remains an expiring, scoped capability issued by an authorized agent.

## Transport and direct links

The pulled branch was `codex/ember-split-feedback-candidate04` at `cb892db049dcfdf0ad98afc168bfbf90164d01a3`. It already contained the preceding transport fix. Neither page contained a streaming-fetch body reader, a page-owned AbortController, or the old ten-second stream abort. This change does not replace that transport again.

Both `/visualizer` and `/observer` (also `/visualizer?mode=observer`) load **the same** `embers/api/observer_transport.js`, with a fingerprinted URL and Subresource Integrity. Its SHA-256 remains:

`b7f69d4f06405b066fee1e65e673100665d771131e13de58e7b03b1e2f0593c0`

The shared transport uses native EventSource. Its AbortController is limited to finite REST requests; it does not cancel a healthy streaming response. LIVE is established by the SSE handshake, not by snapshot success. The one-second expiry countdown changes text locally; it makes no network requests.

Both direct-link flows consume their fragment, exchange the capability for the existing HttpOnly scoped viewing cookie, remove the fragment through `history.replaceState`, and connect without manual entry:

- `/visualizer#view=<namespace-capability>`
- `/visualizer?mode=observer#observe=<observer-capability>`

Invalid/expired/revoked capabilities remain explicit errors. No identity layer, ownership fields, human-to-agent mapping, permanent grant, silent renewal, or new authorization role was introduced.

## Write visibility and journal isolation

The previous write path persisted memories but did not emit observable write receipts. New nodes therefore appeared only after a later recall or usefulness report. Both normal API and MCP writes now reach a shared writer instrumentation hook, including consolidation-created records.

A pending receipt is atomically written and fsynced **before** the source memory write. It contains the sealed record ID/hash, namespace, authenticated actor when available, a hashed session reference, and an observed request reference when available. It is not a successful-write event.

After the native writer commits the memory, the hook verifies the persisted record and appends a `memory_written` or `memory_consolidated` observation. Only then does it wake the existing SSE subscribers. Failed/uncommitted source writes produce no successful-write event.

**Important audit finding:** the existing FUR journal stores RAW records in memory namespaces, and consolidation scans namespace records. Adding new write observations there would create additional consolidation inputs. The final implementation therefore persists the new observer delivery journal separately, at `<store>-observation-journal.sqlite3`. It is not registered in memory indexes or passed to retrieval, consolidation, FUR, or activation.

The sidecar journal also mirrors already-committed FUR observations for a single replayable delivery sequence. Mirroring runs on the mutation side and at startup, never because a researcher opens a page. Source FUR events and source FUR revisions are unchanged.

- `revision` / `observation_revision`: monotonically increasing delivery revision within a namespace.
- `source_journal_revision`: original FUR revision, or `null` for a write-only receipt.
- `id`: stable source event ID; write receipts use `memory-write:<record-id>:<sealed-content-hash>`.
- `previous` / `seal`: observer journal integrity chain.
- `observation`: operation, affected memory IDs, primary context, session reference, source IDs, provenance, committed memory hash and creation timestamp.

Namespace stream cursor validation uses the **observation** journal. A regression test prevents comparing this cursor to FUR's independent revision. This comparison initially broke reconnect after writes; the test and route fix address that failure without changing FUR.

### Crash and retry behavior

| Interruption | Recovery |
| --- | --- |
| Before source commit | Pending receipt is removed at startup; no successful-write event. |
| After source commit, before observation commit | Startup verifies the sealed source and records the pending observation. |
| After observation commit, before receipt deletion | The unique event key prevents a second event; the remaining receipt is removed. |
| FUR event committed but mirror write failed | Source result remains successful; the next source mutation or startup repairs the mirror. Viewer reads do not repair it. |

SQLite transactions enforce unique `(namespace,event_key)` receipts. Readers use SQLite read-only connections. Journaling failures are logged rather than changing a successful source operation into a failed memory operation. If the filesystem prevents both preparing and recording an observation and the process subsequently crashes, no cross-file atomic guarantee can recover information that was never persisted. This limitation is explicit; the implementation does not claim a shared transaction with native memory storage.

A retry that refers to the same committed record creates no duplicate observation. If an existing memory API creates a genuinely new record on a repeated request, this work does not change that API's write/idempotency semantics: each actual committed record has its own receipt.

## Authorization and provenance

Authentication binds instrumentation to the acting agent and hashes bearer session IDs. HTTP request-local and MCP call-local contexts are reset between requests. Direct/internal writes without an authenticated actor are identified as system activity, not guessed to belong to an agent.

Observer filtering remains server-side: observed actor, allowed namespaces, actual namespace ACLs, session scope, grant expiry and revocation all apply. Namespace views do not acquire agent-wide visibility. Provenance includes only authorized same-namespace durable source memories, bounded to 32. It never copies consolidation `source_data` or arbitrary writer metadata into provenance.

The existing expiry limits are preserved: ordinary namespace grants allow 60–3,600 seconds; agent observer grants allow 60–86,400 seconds. The UI displays expiry and remaining time. Existing explicit issue/revoke/replacement flows and restart persistence are retained. Seven-day owner-specific access is not introduced; no Human Identity dependency is being implemented.

## Visual changes

- Shared charcoal/neutral theme with restrained pale-yellow accents and a complete light option.
- Screen-sized workspace; long inspectors/timelines scroll inside their panels.
- “Memory groups” explains grouping by stored subject/context, with namespace boundaries kept separate.
- Memory inspector separates content, origin, recorded creation event, sources/consolidation, U, evidence mass, actual H, truth, and typed directional relationships. Missing information is labeled as missing.
- Bubbles are now individually selectable memories inside subject/context containers. The previous small dots were decorative and could not be selected.
- Network and bubble views expose real directed edges; bounded two-hop/30-node inspection traces only recorded relationships. No synthetic graph links are created.
- Observer graph spacing uses the available panel width; relationship labels emphasize the selected node rather than overlapping every label.
- Heatmap supports row paging, exact-context values, tooltips and a visible scale. Missing H is different from zero and never derived from U.
- 3D retains visual-layout semantics and adds separate Fit View / Reset Camera / return-to-2D controls. Reduced-motion settings suppress event animation and default layout motion.
- Genuine events update affected state in place and briefly highlight it. No animation is used as proof of neural activity.

## Final local results

| Check | Namespace View | Research Observer |
| --- | --- | --- |
| Direct capability URL | PASS; auto-entry and fragment removed | PASS; auto-entry and fragment removed |
| Healthy idle duration | 180 seconds | 180 seconds |
| Persistent SSE streams | 1 | 1 |
| Healthy reconnects | 0 | 0 |
| Initial snapshot GETs | 1 | 1 |
| Extra healthy snapshot GETs | 0 | 0 |
| Fallback activations | 0 | 0 |
| BodyStreamBuffer abort / JavaScript errors | 0 | 0 |
| Disconnect/replay and same-page restart | PASS | PASS |

Both routes loaded the identical fingerprinted transport URL. The first API write appeared in both views in **0.035 seconds** end-to-end, without a recall or feedback call. Browser-visible activity stayed healthy during the subsequent writes, consolidation, and pair reports. The same observer followed two actual sessions and two namespaces; the namespace view received no second-namespace payload. Foreign-agent events were absent from raw SSE capture.

Memory-store **and observer sidecar file hashes were unchanged** during observer-only idle/inspection. The source FUR revision remained zero after a write-only operation in the focused regression test. Source feedback then advanced FUR to r1 while observer delivery advanced independently to o2.

- Full regression suite: **670 passed**, one upstream Starlette/httpx deprecation warning.
- Final focused write/cursor suite: **11 passed**, including the added reconnect-cursor regression.
- Presentation matrix: 0, 1, 12 and 100 nodes × network, bubbles, heatmap and 3D = **16 passed**. These layout fixtures are synthetic and do not establish real heat measurements.
- A final short browser run verified the revised observer graph spacing, screenshots, and recording. Its one-second idle is supplemental visual QA, not the 180-second transport evidence.
- Packaging: release wheel built successfully; both HTML templates, shared JS, CSS, build metadata, and observation modules match source bytes exactly. See `visualizer-observation-evidence/packaging.json`.

Earlier failed checks were fixed rather than labeled passes: the original new-write prototype polluted the source journal and was replaced before publication; the first sidecar reconnect attempt compared against FUR revisions and returned HTTP 400, corrected by the observation-cursor validation test. Only the final sidecar proof is published here.

Evidence:

- [Raw transport proof and counters](visualizer-observation-evidence/transport-proof.json)
- [Visual QA proof](visualizer-observation-evidence/visual-proof.json)
- [Unit-test output](visualizer-observation-evidence/unit-tests.txt)
- [12-second interaction recording](visualizer-observation-evidence/interaction.mp4)
- [Previous tracked 3D view](visualizer-observation-evidence/before-orbit.jpg)
- [Network, dark](visualizer-observation-evidence/network-dark.jpg) · [light](visualizer-observation-evidence/network-light.jpg)
- [Bubbles](visualizer-observation-evidence/bubbles-dark.jpg) · [heatmap](visualizer-observation-evidence/heatmap-dark.jpg) · [3D](visualizer-observation-evidence/orbit-dark.jpg)
- [Research Observer, light](visualizer-observation-evidence/observer-light.jpg) · [mobile](visualizer-observation-evidence/mobile.jpg) · [100-node layout](visualizer-observation-evidence/bounded-100.jpg)

No new identity concept or authorization dependency was introduced. Public deployment validation is pending; no local test is represented as a deployed ngrok result.

## Interfaces

No new server, transport implementation, API authorization role, or MCP tool is needed. Existing `/v1/visualizer/*`, `/v1/visualizer-stream/*`, `/v1/observer/events`, `/v1/observer/stream`, grant APIs, `ember_visualize`, and namespace viewing tools expose the improved observation automatically through the normal server.

The usefulness/state interface keeps its source FUR revision. Only visualizer observation snapshots opt into the separate delivery journal. Existing source math and native core files are unchanged.

## Validation

Results and evidence are recorded in `visualizer-observation-evidence/` after final validation. Browser checks use a real Chromium browser, the normal `uvicorn embers.api:app` server, an isolated temporary store, and a controllable local reverse proxy. They are **local tests, not ngrok/deployed validation**.

The browser test covers direct links, fragment removal, independent 180-second idle runs on both views, raw SSE payloads, immediate writes without recall/feedback, authenticated MCP/consolidation writes, source paths, same-agent session/namespace transitions, disconnect/replay, same-page server restart, foreign-agent payload isolation, themes, selection, screen fit, and synthetic bounded layout fixtures. Synthetic visual fixtures are explicitly separated from real delivery evidence.

Focused backend tests cover failed writes, receipt replay, duplicate writes, startup recovery, source FUR mirror recovery, unchanged FUR revisions/math, exclusion from memory indexes/consolidation, capability bounds, and read-only access including the observer sidecar itself. The normal regression suite also exercises expiry/revocation, unauthorized access, observer session scope and write-API rejection.

## Files changed

| Area | Files |
| --- | --- |
| Presentation | `embers/api/visualizer.html`, `embers/api/observer.html`, `embers/api/visualizer_theme.css`, `embers/api/observer_build.py`, `pyproject.toml` |
| Source-write instrumentation | `embers/engine/writer.py`, `embers/integration/write_observation.py`, `embers/api/session_gate.py`, `embers/mcp/server.py`, `embers/mcp/session_auth.py` |
| Observer delivery/projections | `embers/integration/observation_journal.py`, `embers/integration/observation_stream.py`, `embers/integration/research_observer.py`, `embers/integration/usefulness_service.py`, `embers/api/usefulness_routes.py` |
| Tests/evidence | `tests/test_write_observation.py`, `tests/test_consolidated.py` (identify snapshot node by ID), `examples/visualizer_write_check.py`, this report and evidence |

The unrelated pre-existing `docs/observatory-orbit.png` modification and `ember_store/` are preserved and excluded from the commit.

## Deployment

Pull the existing test branch, install/update that checkout/package, and restart the **same normal Ember server** using the existing store and settings. No experimental second server is required. The wheel includes the shared theme CSS and the existing transport asset. Preserve `<store>-write-observations/`, `<store>-observation-journal.sqlite3`, and the existing observer-access sidecar with the store backup.

After restart, request an ordinary scoped viewing link and validate through the real proxy/ngrok path. Check the visible build marker and shared transport hash. Local evidence does not establish deployed tunnel availability or public SSE stability.

The observer delivery journal is append-only and is not covered by native memory-store quotas. No automatic deletion/retention policy or new research configuration is introduced in this change. Deployment operators must budget audit storage; retention policy is outside this scope.
