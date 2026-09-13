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

Auth: register once, start session, then `X-Ember-Session-Id` or agent+token.

## Still not here

- User config file
- Lobby websocket
- Scheduler default-on (must set EMBER_MAINTENANCE_INTERVAL_SECONDS)
