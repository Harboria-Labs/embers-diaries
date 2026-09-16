# Surfaces that exist now

Not a design doc. What you can call.

## MCP (session_id after start)

- ember_write / ember_recall — room + kind on write
- ember_feedback / ember_feedback_for — outcome only, no memory rewrite
- ember_lifecycle — computed
- ember_run_maintenance — one pass now
- ember_lobby — open, publish, corroborate, board, promote, close
- ember_get_session — work view: memories, discoveries, failures, lobby_posts, feedback

## REST /v1

- POST /v1/memory/{id}/feedback
- GET /v1/memory/{id}/feedback
- GET /v1/memory/{id}/lifecycle
- POST /v1/maintenance
- GET /v1/sessions/{id}/work
- POST /v1/lobby/publish
- GET /v1/lobby/updates
- GET /v1/lobby/status
- POST /v1/lobby/promote
- POST /v1/lobby/corroborate
- POST /v1/lobby/close
- GET /v1/memory/proposals (includes evidence + evidence_authors)

Auth: register once, start session, then `X-Ember-Session-Id` or agent+token.

## Still not here

- Lobby websocket
