"""HTTP auth is per session, same rule as MCP."""

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
    import os
    import asyncio
    import logging
    import embers.api.v1 as v1
    from ..db import EmberDB
    from ..db_feedback import bind as bind_feedback

    bind_feedback(EmberDB)
    v1.require_agent = resolve_agent
    from .feedback_routes import router as feedback_router
    app.include_router(feedback_router)

    @app.middleware("http")
    async def bind_session_header(request, call_next):
        token = _header_session.set(request.headers.get("x-ember-session-id"))
        try:
            return await call_next(request)
        finally:
            _header_session.reset(token)

    holder = {}

    @app.on_event("startup")
    async def _start_maintenance():
        interval = int(os.environ.get("EMBER_MAINTENANCE_INTERVAL_SECONDS", "0") or "0")
        if interval <= 0:
            return
        from ..maintenance import maintenance_loop
        import embers.api as api_mod
        namespaces = [n.strip() for n in
                      os.environ.get("EMBER_MAINTENANCE_NAMESPACES", "memories").split(",")
                      if n.strip()]
        logging.getLogger(__name__).info(
            "maintenance scheduler enabled: every %ss, namespaces=%s",
            interval, namespaces)
        holder["task"] = asyncio.create_task(
            maintenance_loop(api_mod._get_protocol(), namespaces, interval))

    @app.on_event("shutdown")
    async def _stop_maintenance():
        task = holder.get("task")
        if task is not None:
            task.cancel()
