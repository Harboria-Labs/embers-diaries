"""HTTP auth is per session, same rule as MCP.

Token pair opens a session. Later requests may send X-Ember-Session-Id.
"""

from __future__ import annotations

from contextvars import ContextVar

from fastapi import HTTPException

from ..core.types import SessionStatus
from ..identity.registry import AgentRegistry

_header_session: ContextVar[str | None] = ContextVar("ember_http_session", default=None)


def current_session_id() -> str | None:
    return _header_session.get()


def resolve_agent(db, agent_id: str | None, token: str | None,
                  session_id: str | None = None):
    session_id = session_id or _header_session.get()
    if agent_id and token:
        try:
            return AgentRegistry(db).authenticate(agent_id, token)
        except PermissionError as e:
            raise HTTPException(401, str(e)) from e
    if session_id:
        session = db.get_session(session_id)
        if session is None:
            raise HTTPException(401, "unknown session")
        if session.status != SessionStatus.ACTIVE:
            raise HTTPException(401, "session is not active")
        ident = AgentRegistry(db).get(session.agent_id)
        if ident is None:
            raise HTTPException(401, "unknown agent")
        return ident
    raise HTTPException(
        401,
        "No session. Pass X-Ember-Session-Id, or X-Ember-Agent-Id and X-Ember-Token.",
    )


def install(app) -> None:
    import embers.api.v1 as v1

    v1.require_agent = resolve_agent

    @app.middleware("http")
    async def bind_session_header(request, call_next):
        token = _header_session.set(request.headers.get("x-ember-session-id"))
        try:
            return await call_next(request)
        finally:
            _header_session.reset(token)
