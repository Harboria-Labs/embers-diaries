# Ember Lobby V2: architecture and implementation contract

Status (2026-10-18): **the ten principles and five decisions below are approved.** Implementation and batch validation are tracked separately. The V1 board contract remains in [lobby_design.md](lobby_design.md); the source comparison is in [lobby_v2_gap_matrix.md](lobby_v2_gap_matrix.md).

## Approved implementation decisions

1. Authorized shared-task membership governs visibility. Within that scope, supported harnesses may publish qualifying completed findings and evidence automatically, subject to structural privacy controls, redaction and agent withholding.
2. Provisional working memories survive ordinary server restarts but expire through defined retention or task closure. They remain separate from permanent `.ember` memory.
3. Evidence retains provenance, uncertainty, execution context and accessible references; evidence retrieval has its own authorization check.
4. Authors may correct or withdraw their findings. Other authorized agents may add attributed evidence or contradictions, never rewrite another agent's contribution.
5. One Ember-facing submit/retrieve/event contract serves harness-specific adapters. Implement and claim only hooks verified in each harness.

## Frozen principles

1. Agents select meaningful completed findings, experiences, unresolved questions, and outcomes. Capture of an operation is separate from publication.
2. A harness attaches observable supporting evidence when available. Missing hooks yield declared missing evidence, never invented receipts.
3. Submission joins the selected finding, evidence references, and provenance.
4. Ember holds the submission as provisional, scoped working memory.
5. Relevant authorized agents can receive server-pushed memory-change signals without polling.
6. Agents fetch underlying memories through contextual Ember retrieval.
7. No agent-to-agent messaging or orchestrator is required.
8. Durable promotion remains separate and uses proposals, evidence, conflicts, and admission policy.
9. Unverified findings and failed approaches may be useful without becoming true.
10. Private work, credentials, and private reasoning are never automatically shared.

## Source reconciliation and boundaries

The current `LobbyStore` is a process-local board with explicit open, typed publish, corroborate, snapshot, TTL, and close. REST and MCP share it through `mcp/lobby_surface.py`. `Session` supplies agent, task and namespace provenance. `NamespaceManager` supplies read/write ACLs, but unregistered namespaces are open by default. V2 must require an explicit shared-task scope and authorization at every read/write/stream operation; a namespace string or free-text privacy guess cannot grant visibility.

Use the existing `Evidence` shape and proposal/promotion engines for durable handoff. Current `lobby_surface._promote` supplies post and corroboration evidence, but its `DIRECTLY_OBSERVED` label describes the post, not independent proof of its claim. V2 must preserve that distinction. Current close marks a board closed before writing unpromoted failures; the handoff and retry boundary needs an idempotent design before implementation.

Use the existing observation SSE code as a *transport pattern*: authenticated stream, `Last-Event-ID`, committed wakeup, journal-backed catch-up, heartbeats, reset, and disconnect cleanup. Do not use the Observatory's journal, grants, event feed or snapshots as a Lobby data source. The Observatory stays read only with zero causal effect. Ordinary `MemoryProtocol.recall` ranks durable records and records access; FUR/Pairing/ELLA are durable-memory mechanisms. Provisional Lobby ranking must be a separate, bounded selection over authorized working items, with no access write, feedback event, truth update, confidence increase, or automatic promotion.

## Proposed capture and submission contract

At a meaningful completion boundary, the agent or its harness selects a concise outcome and explicitly opts it into an active shared task. Examples: a reproducible failure, completed test finding, discovery with a source, or unresolved question. Tool completion alone is not publication. The harness may attach an artifact reference, tool-call identifier, exit status, test result, timestamp, and digest only when observed and permitted to share. An agent can also submit an agent-declared claim with `evidence_status: unavailable` and a reason such as `harness_hook_unavailable`. Ember validates identity, active session, scope, size, provenance and references, then creates a provisional item. It never upgrades an agent declaration into an observed fact.

Proposed minimum submission: `client_submission_id` (idempotency key scoped to authenticated agent/session), `kind` (`finding|failure|question|outcome`), `claim`, `uncertainty` (`unverified|observed|contradicted|resolved`, with explanation), optional `approach`, `evidence_refs[]`, `evidence_status` (`attached|unavailable|withheld`), `task`, `namespace`, `room` (`project|task`), and explicit `share=true`. Server supplies `item_id`, source `agent_id` and `session_id`, `created_at`, `updated_at`, `expires_at`, `version`, and lifecycle state. Client-supplied identity or timestamp is advisory only. `observed` means the referenced observation exists, not that the claim is true.

An evidence reference has `evidence_id`, `source`, `source_type`, `reference`, `observed_at`, `agent_id/session_id` provenance where available, `content_hash` when the bytes are accessible, `availability` (`accessible|external|withheld|unavailable`), and a short redacted description. The claim and its support are separate fields. Reuse sealed `Evidence` only when constructing a durable proposal and when its actual source and visibility are verified. A URL, local path or digest is not evidence of another agent's ability to access the artifact.

## Proposed contextual retrieval and event contract

`GET working memories` / equivalent MCP tool accepts the authenticated session, task/context clues, namespace, room and a bounded `limit`. Ember first requires active session ownership, explicit shared-scope membership and namespace read permission. It filters active, unexpired items before ranking by task/room match, supplied context terms, freshness and kind; it returns stable order with item IDs, uncertainty, provenance and evidence availability. The first cut can use lexical matching and current task metadata. It must not claim semantic ranking until measured. Repeated deliveries, reads, corroborations or citation counts cannot turn uncertainty into truth. Durable ordinary recall remains unchanged; an agent adapter may request both and must label the provisional results.

`GET /v1/lobby/stream` is a proposed SSE surface. Event names: `ready`, `working_memory_changed`, `working_memory_removed`, `reset`, `denied`, plus heartbeat comments. A change frame carries an opaque item ID, namespace/task scope, event type, version, server time and cursor; it omits claim text, raw evidence, session IDs and secrets. The receiver calls contextual retrieval and decides whether to act. Events are hints, never commands or proof. A dedicated per-scope monotonic cursor identifies an ordered retained event log. `Last-Event-ID` or explicit `after` resumes from the last *processed* event; duplicate frames are safe. Page catch-up is bounded. A cursor ahead of, older than, or incompatible with the retained log yields `reset` and an explicit authorized snapshot/refetch path. No silent skip. Recheck auth at connect and during the stream; revocation ends delivery.

Proposed bounded delivery uses coalesced per-scope wakeups and journal reads rather than an unbounded per-client payload queue. Slow readers may be reset to refetch. Across workers/processes, a shared event source or bounded heartbeat catch-up is required; process-local wakeups alone do not meet the cross-process contract. Whether the retained log survives restart is an open storage choice below. A connection is allowed to disconnect without changing working-memory state.

## Privacy and authorization rules

- Publication is default deny. A registered agent, active owned session, explicit `share=true`, explicitly shared task/room, and namespace write access are required. The intended readers must have explicit shared-scope membership and namespace read access. An unregistered namespace's permissive ACL is insufficient by itself.
- Task names and caller-supplied namespace overrides do not confer membership. Recheck membership and ACL on retrieval, event feed and artifact dereference, including after a permission change. Session IDs are bearer-like and must not appear in event payloads or cross-agent results.
- Never auto-publish full tool transcripts, prompts, private reasoning, credentials, private rooms, personal preferences, or unrestricted local paths. A harness shares only an allowlisted, redacted artifact reference or declares evidence withheld/unavailable. Reject a private scope structurally; do not guess privacy from prose.
- The author may correct/withdraw its provisional item; a designated authorized shared-task reviewer may flag or supersede according to a separately approved role policy. Receivers see status and provenance, not private evidence bytes. Namespace ACL and artifact ACL are both enforced.

## Lifecycle, failures and durable handoff

Proposed lifecycle: `active -> corrected|superseded|withdrawn|expired`, with a stable `item_id` and increasing version; corrections create a visible successor/version and signal receivers. Expiry/close removes the working item from retrieval and emits a removal hint when possible. Failed approaches remain retrievable while active, clearly marked failed/unverified. Retention duration, scope closure policy and restart guarantee are open choices. Expiry must not erase a successfully accepted durable handoff.

Publication retries use the same submission key and return the same item/version. Promotion is explicit: freeze the selected working version and verified evidence references, create or find one durable proposal/failure via a stable handoff key, then record its durable ID. A retry returns that ID. If the durable write succeeds but the Lobby acknowledgment fails, reconciliation finds the durable ID before retrying. Close/expiry must wait for or retain a pending handoff receipt. Proposal admission, conflicts, independent evidence and permanent memory state remain under the existing engines; corroboration is attributed testimony, not automatic truth. Delivery is at least once, so consumers deduplicate by event cursor and item/version.

## Agent-facing adapters and sequence

Minimum adapter contract: establish an Ember identity/session; expose a deliberate `submit_working_memory` operation with evidence availability; offer `query_working_memory` and `get_working_memory`; optionally run an authenticated SSE listener that passes change IDs into the host's normal context boundary; call existing proposal/promotion operations only on explicit handoff. REST can serve streaming; MCP can serve submit/query/get/promotion. A model-selectable MCP tool alone does not ensure background listening or automatic context injection. Codex, Claude and ChatGPT-compatible MCP clients can use the tool contract where their host exposes MCP, but completion hooks, tool-result capture, long-lived SSE, and wake-to-model delivery require each external harness to supply and verify those capabilities. A manual submit/query fallback remains valid. [OpenAI MCP documentation](https://developers.openai.com/learn/docs-mcp) describes tool connectivity; [ChatGPT custom MCP documentation](https://developers.openai.com/api/docs/guides/custom-mcp-server) describes its MCP surface, not a general guarantee of autonomous agent wakeups.

Minimum implementation sequence, each behind focused tests:

1. Freeze the reviewed scope/retention/restart decisions below; specify one explicit shared-task membership rule. Add schema and validation to the existing Lobby boundary, keeping V1 calls compatible.
2. Add provisional item storage, idempotent submission, correction/expiry and authorized contextual query; keep it outside durable Ember records and FUR/ELLA.
3. Add a Lobby-specific ordered event source and SSE route using existing stream patterns; verify reconnect, cross-process behavior, backpressure and resets.
4. Make durable handoff idempotent across publish/close/retry, reusing `Evidence`, `MemoryProposal`, `Failure`, session links and promotion policy.
5. Add minimal REST/MCP adapters and one integrated harness adapter at a time. Manual fallback first; background delivery only where a harness exposes a tested hook.

Completion requires focused tests for session ownership and inactive sessions on REST/MCP; namespace and explicit scope read/write/revocation; private-room and sensitive-artifact rejection; missing evidence without fabrication; claim/evidence separation; deterministic contextual ranking without FUR/ELLA writes; duplicate submission/promotion/close; correction, supersession, expiry and accepted handoff retention; SSE reconnect, stale/ahead cursor reset, bounded slow client, multi-worker catch-up and auth expiry; plus an actual end-to-end harness test for each claimed integration. These are future gates, not tests run for this document.

## Remaining implementation parameters

The five architectural decisions above are closed. Concrete values for retention, bounded capacity, reference allowlist and harness hooks are implementation parameters to validate in the batch work. No automatic background wakeup is implied by MCP availability.
