"""
Ember's Diaries — REST API Server

A FastAPI server that exposes the EmberDB interface over HTTP.
Run: uvicorn embers.api:app --port 9200
"""

import os
from typing import Optional
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager

from ..db import EmberDB
from ..core.record import EmberRecord
from ..core.annotation import Annotation
from ..core.types import RecordType, DeprecationReason
from ..identity.registry import AgentRegistry
from ..integration import MemoryProtocol


_db: Optional[EmberDB] = None
_protocol: Optional[MemoryProtocol] = None
_registry: Optional[AgentRegistry] = None


def _get_db() -> EmberDB:
    global _db
    if _db is None:
        store_path = os.environ.get("EMBER_STORE", "./ember_store")
        _db = EmberDB.connect(store_path)
    return _db


def _get_protocol() -> MemoryProtocol:
    global _protocol
    if _protocol is None:
        _protocol = MemoryProtocol(_get_db())
    return _protocol


def _get_registry() -> AgentRegistry:
    global _registry
    if _registry is None:
        _registry = AgentRegistry(_get_db())
    return _registry


def _legacy_agent(request: Request):
    """Return the authenticated agent stamped by the legacy API middleware."""
    agent = getattr(request.state, "ember_agent", None)
    if agent is None:
        raise HTTPException(401, "No session. Pass X-Ember-Session-Id, or X-Ember-Agent-Id and X-Ember-Token.")
    return agent


@asynccontextmanager
async def lifespan(app: FastAPI):
    _get_db()
    yield


app = FastAPI(
    title="Ember's Diaries API",
    version="0.2.0",
    description="Cognitive database engine for AI memory systems. Nothing is ever deleted.",
    lifespan=lifespan,
)

_LEGACY_PROTECTED_PREFIXES = (
    "/records", "/namespaces", "/search", "/query", "/graph",
    "/memory", "/timeline",
)
_PUBLIC_PATHS = {"/health", "/docs", "/redoc", "/openapi.json"}


def _is_legacy_protected(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/")
               for prefix in _LEGACY_PROTECTED_PREFIXES)


@app.middleware("http")
async def require_legacy_agent(request: Request, call_next):
    path = request.url.path.rstrip("/") or "/"
    if (path in _PUBLIC_PATHS or path.startswith("/v1")
            or path == "/mcp"):
        return await call_next(request)
    if not _is_legacy_protected(path):
        return await call_next(request)

    agent_id = request.headers.get("x-ember-agent-id")
    token = request.headers.get("x-ember-token")
    session_id = request.headers.get("x-ember-session-id")
    try:
        if agent_id and token:
            request.state.ember_agent = _get_registry().authenticate(agent_id, token)
        elif session_id:
            from ..core.types import SessionStatus
            session = _get_db().get_session(session_id)
            if session is None:
                raise PermissionError("unknown session")
            if session.status != SessionStatus.ACTIVE:
                raise PermissionError("session is not active")
            ident = _get_registry().get(session.agent_id)
            if ident is None:
                raise PermissionError("unknown agent")
            request.state.ember_agent = ident
            request.state.ember_session_id = session_id
        else:
            return JSONResponse(
                {"detail": "No session. Pass X-Ember-Session-Id, or X-Ember-Agent-Id and X-Ember-Token."},
                status_code=401,
                headers={"WWW-Authenticate": "Ember"},
            )
    except PermissionError as exc:
        return JSONResponse(
            {"detail": str(exc)},
            status_code=401,
            headers={"WWW-Authenticate": "Ember"},
        )
    return await call_next(request)


_cors_origins = [origin.strip() for origin in
                 os.environ.get("EMBER_CORS_ORIGINS", "").split(",")
                 if origin.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Ember-Agent-Id", "X-Ember-Token", "X-Ember-Session-Id"],
    )

from .v1 import router as v1_router
from .mcp_http import router as mcp_router
from .session_gate import install as install_session_gate
app.include_router(v1_router)
app.include_router(mcp_router)
install_session_gate(app)
