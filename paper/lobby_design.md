# Lobby design (Features 10 / 11 / 25 / 26 / 27)

Status: design only. Not implemented.
Against Ember `main` after session-id-only auth (`739be5b`).

Lobby is an on-demand work board for a shared task. It is not chat,
not memory, and not on by default.

## Why it exists

An agent should see another agent's live status, failure, or discovery
before repeating the same work. Durable Ember is the wrong place for
that signal: too slow, too permanent, too easy to treat as fact.

## Why it stays off

Lobby is a leak if it is always open. Personal facts, preferences,
secrets, and single-agent private work belong in Ember with a room
(`personal` / `project` / `task`), never on a board other agents read.

Default: no board. Trigger only when a shared task needs coordination.

## Trigger

Open a board only when all of these hold:

1. The session has a shared task (not personal preference/storage work).
2. More than one agent may act on that task, or a failure/discovery
   would waste the next agent.
3. The caller explicitly opens the board (`ember_lobby_open`).
   Connecting or registering is not a trigger.

Close on `ember_lobby_close` or when the session ends.

## Gate (publish rejected)

- No `session_id`
- Session or payload marked `room=personal`
- Missing type, or type is free chat
- Body is a durable preference/fact with no shared task

Allowed types only: `status` | `failure` | `discovery` | `question`.
Short body. Optional `approach` key (same key space as durable failures).

## Auth

Same as the rest of Ember after `739be5b`:

- Register once.
- `ember_start_session` once with agent_id + token.
- Every lobby call passes `session_id`.
- Session is paired to the agent in the store.
- Do not remember last client on the shared HTTP `EmberMCP`.

Board state is keyed by `board_id` / task + namespace, never by
`self._last_*` on the process.

## Objects (ephemeral)

### Board

- `board_id`
- `task`
- `namespace`
- `status`: open | closed
- `opened_by` (agent_id, session_id)
- `ttl`

### Post

- `post_id`
- `board_id`
- `session_id` / `agent_id`
- `type`: status | failure | discovery | question
- `body`
- `approach` (optional)
- `created_at`

Posts die with TTL or board close. They are not Ember records.
They do not get `content_hash` in the memory WAL.
They do not enter the conflict engine.

## Path into Ember (Feature 27)

```
lobby post
  → ember_lobby_promote (explicit)
  → MemoryProposal  (existing Feature 12)
  → submit / promote / reject
  → durable Ember
```

Failure posts may instead become `ember_report_failure` when the
caller says so. Nothing in the lobby auto-calls `ember_write`.

Semantic conflict mapping stays on durable memories only.
Two agents disagreeing on the board is conversation.

## Surfaces

Same core for MCP and REST.

MCP:

- `ember_lobby_open`     task, namespace, session_id
- `ember_lobby_publish`  type, body, optional approach, session_id
- `ember_lobby_board`    snapshot: presence, last status, failures, discoveries
- `ember_lobby_promote`  post_id → proposal (or failure record)
- `ember_lobby_close`    session_id

REST (later, same names under `/v1/lobby/...`).

Also: a short board summary on `ember_get_session` so a client that
cannot see lobby tools still knows whether a board is open.

`ember_lobby_board` is a snapshot for this turn. Realtime fan-out
(WebSocket / SSE) is Feature 26 and is out of this cut.

## Room vs kind

Unchanged, Feature 8 / 28. Kind = what a durable memory is.
Room = where it belongs. Not used as lobby channels.
`room=personal` is a publish reject, not a lobby name.

## This cut does not include

- WebSocket / SSE
- Lobby rows in the WAL
- Conflict mapping from lobby text
- Persist-on-shutdown as memory
- Auto-open on register
- Config.toml lobby knobs until the board exists

## Implementation order (when we leave design)

1. In-memory board + posts keyed by board_id, TTL, session_id auth.
2. Four MCP tools + get_session summary.
3. Promote → existing proposal / failure APIs.
4. Tests: personal gate, no session_id reject, shared HTTP instance
   cannot inherit another agent's board, close drops posts.
5. REST mirror.
6. Only then realtime transport.
