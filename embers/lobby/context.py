"""Where a board belongs. One rule, shared by every surface.

A board's namespace decides two things that are easy to get silently wrong:

  * **who may join it.** `LobbyStore.open()` keys boards on
    `(task, namespace, room)`. Two agents who name the same task but resolve
    to different namespaces land on two separate boards and see an empty
    room where the other agent's work should be. No error is raised — the
    coordination the lobby exists to provide just doesn't happen.

  * **where a promotion is filed.** `lobby_surface._promote()` reads
    `board["namespace"]` and writes the durable proposal or failure there.
    A board in the wrong namespace misfiles durable memory.

Before this module each surface resolved the namespace on its own terms —
the MCP adapter from `protocol.namespace`, the REST route from the session —
so both failures were live:

  * an MCP agent and an HTTP agent working the same task never met, because
    `ember_start_session` defaulted a session to `protocol.namespace`
    ("memories") while `POST /v1/sessions` defaulted it to "default"; and
  * an MCP agent that opened a session in namespace "parser-work" opened its
    board in "memories" anyway, because the adapter never consulted the
    session — so its promoted discovery was filed under "memories".

Both surfaces now call `resolve_namespace()`, so the rule is in one place and
changing it changes both doors at once.
"""

from __future__ import annotations


def resolve_namespace(explicit: str | None, session, fallback: str) -> str:
    """Resolve the namespace a lobby board belongs to.

    Precedence, most specific first:

    1. ``explicit`` — the caller named a namespace on this request. An agent
       may deliberately open a board outside its session's namespace, so an
       explicit value always wins.
    2. ``session.namespace`` — the session is the authoritative carrier of
       "what am I working in". Both `ember_start_session` and
       `POST /v1/sessions` record it, so this is the value the two surfaces
       can actually agree on.
    3. ``fallback`` — the surface's own default, for the case where the
       session is gone or carries no namespace.

    ``session`` may be ``None`` or any object without a ``namespace``
    attribute; callers on the MCP side pass whatever ``db.get_session()``
    returned without having to branch on it.
    """
    explicit = (explicit or "").strip()
    if explicit:
        return explicit
    from_session = (getattr(session, "namespace", None) or "").strip()
    if from_session:
        return from_session
    return (fallback or "").strip() or "default"
