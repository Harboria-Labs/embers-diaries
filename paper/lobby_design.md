# Lobby design (Features 10 / 11 / 25 / 27; 26 deferred)

Status: implemented on main as a request/response board. Realtime (§26) deferred.
Tool: `ember_lobby` actions `open | publish | corroborate | board | promote | close`.
Posts are not Ember records. Room gate is default-deny. Off until `action=open`.

Lobby is an on-demand work board for a shared task. It is not chat,
not memory, and not on by default.

## Invariant

Lobby is deliberately outside Ember's append-only guarantee. That is
why it is allowed to forget. Posts are not records: no `content_hash`,
not in the WAL, not in the conflict engine. TTL and board close are
not deletions of memory. If someone files "ttl deleted a memory," the
answer is: it was never one.

## Why it exists

An agent should see another agent's live failure or discovery before
repeating the same work. Durable Ember is the wrong place for that
signal: too slow, too permanent, too easy to treat as fact.

## Why it stays off

Always-on lobby leaks. Personal facts, preferences, secrets, and
single-agent private work belong in Ember with a room, never on a
board other agents read.

Default: no board. Connecting or registering is not a trigger.

## Trigger

Open only when all hold:

1. Shared task (project/task work, not personal storage).
2. More than one agent may act, or a failure/discovery would waste
   the next agent.
3. Caller opens explicitly (`action=open`).

## Gate — structural, no guessing

Ember does not classify free text as "this is a preference." The old
line "body looks like a durable fact" is a no-op and is dropped.

Publish requires an explicit room. Default-deny:

- missing room → reject
- `personal` → reject
- `unscoped` → reject (that is the forget-bucket, not a shared task)
- `project` or `task` → allowed

Also reject: no `session_id`; board closed; type not in the allow-list.

Room on a lobby post is a **gate**, not a lobby channel. Durable
room/kind (Feature 8 / 28) stay how memories are stored after promote.

## Types this cut

`failure` | `discovery` | `question`

`status` is out. The board is a per-turn snapshot. Status without
realtime is stale the moment after the poll. Collision avoidance
under polling would be a claim/lock, not a status broadcast. Ship
three types first.

`warning` stays out of this cut. It is distinct from failure but not
needed to prove the board. Spec §10's other kinds (hypothesis,
request-for-help) fold into durable `verify_status` and `question`.

## Auth

Register once. `ember_start_session` once with the pair. Every lobby
call passes `session_id`. No last-client memory on the shared HTTP
`EmberMCP`. Board state keyed by `board_id` / task + namespace.

## Objects (ephemeral)

### Board

- `board_id`
- `task`
- `namespace`
- `room` (project | task only)
- `status`: open | closed
- `opened_by` (agent_id, session_id)
- `participants` (session_ids that opened or published)
- `ttl`

### Post

- `post_id`
- `board_id`
- `session_id` / `agent_id`
- `room` (copied from publish; project | task)
- `type`: failure | discovery | question
- `body`
- `approach` (optional; same key space as durable failures)
- `corroborations` (list of {agent_id, session_id, at})
- `created_at`

## Corroborate (the missing §11 step)

Spec flow: lobby update → other agents review → evidence → proposal.
Promote-only skips review. PromotionEngine CONSENSUS already counts

```
agents = {ev.agent_id for ev in proposal.evidence}
```

threshold default 2. That is live in `promotion.py`. It does nothing
for lobby if promote creates a one-author proposal.

`action=corroborate` on a post: "I hit this too." Stored on the post,
still ephemeral. Author cannot corroborate their own post.

On `action=promote`:

- each corroboration becomes an `Evidence` row with that `agent_id`
- author evidence included
- then existing Feature 12 (`propose` → `submit` / CONSENSUS / HUMAN)

Do not open a proposal at first publish. That would fill Ember with
unreviewed drafts. Corroboration lives on the board until promote.

## Close — failures must not vanish silently

Discoveries and questions die with the board unless promoted.

Unpromoted **failure** posts on close: auto `ember_report_failure`
(same approach key, agent/session from the post, room already gated).
Durable failure is a negative result, not a truth claim. That is
Feature 13. Silent drop is ruled out.

`action=close` returns what was flushed (`failure_ids`) so the caller
can see it. Who may close: opener or any participant (someone who
published or corroborated). A stranger session_id cannot. Combined
with failure flush, one participant closing does not erase the only
copy of "X died."

## Path into Ember

```
publish (failure|discovery|question)
  → optional corroborate (other agents)
  → promote → MemoryProposal + evidence per corroborator
  → existing promotion engine
```

```
close
  → unpromoted failures → report_failure
  → board empty
```

No silent `ember_write`. No conflict map from board text.
Two agents disagreeing on the board is conversation.

## Surface — one tool

Spec §32 names `ember_lobby_publish` / `read` / `status` / `promote`.
This cut uses **one** tool so a 25-tool catalog does not grow four
dead entries while the default board is off:

```
ember_lobby
  action: open | publish | corroborate | board | promote | close
  session_id
  room          required on open and publish (project|task)
  task          open
  namespace     open
  type, body, approach   publish
  post_id       corroborate | promote
```

`read` → `board` (snapshot). `status` type dropped. `open`/`close`
are the off-by-default thesis and are deliberate spec drift.

`ember_get_session` always includes `board`: object or `null`.
Clients do not branch on key existence.

Realtime (WebSocket / SSE) remains Feature 26, not this cut.

## This cut does not include

- WebSocket / SSE (§26)
- Lobby rows in the WAL
- Conflict mapping from lobby text
- Persist-on-shutdown as memory
- Auto-open on register
- `status` / `warning` publish types
