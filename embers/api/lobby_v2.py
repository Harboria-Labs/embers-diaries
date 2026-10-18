"""Authenticated V2 working-memory API; provisional state never enters Ember records."""

from fastapi import APIRouter, Header, HTTPException, Query

from ..lobby import working

router = APIRouter(prefix="/v1/lobby/v2")


def _caller(agent_id, token):
    from . import _get_db
    from .v1 import require_agent
    db = _get_db()
    agent = require_agent(db, agent_id, token)
    return db, agent.agent_id


def _run(fn):
    try:
        return fn()
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except working.WorkingError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/scopes")
async def create_scope(body: dict, x_ember_agent_id: str | None = Header(default=None),
                       x_ember_token: str | None = Header(default=None)):
    db, actor = _caller(x_ember_agent_id, x_ember_token)
    return _run(lambda: working.create_scope(db, body.get("session_id"), actor,
                  task=body.get("task"), namespace=body.get("namespace"),
                  room=body.get("room", "task")))


@router.post("/scopes/{scope_id}/members")
async def grant(scope_id: str, body: dict, x_ember_agent_id: str | None = Header(default=None),
                x_ember_token: str | None = Header(default=None)):
    db, actor = _caller(x_ember_agent_id, x_ember_token)
    return _run(lambda: working.grant(db, scope_id, body.get("session_id"), actor,
                                      body.get("agent_id")))


@router.delete("/scopes/{scope_id}/members/{grantee}")
async def revoke(scope_id: str, grantee: str, session_id: str,
                 x_ember_agent_id: str | None = Header(default=None),
                 x_ember_token: str | None = Header(default=None)):
    db, actor = _caller(x_ember_agent_id, x_ember_token)
    return _run(lambda: working.revoke(db, scope_id, session_id, actor, grantee))


@router.post("/scopes/{scope_id}/items")
async def submit(scope_id: str, body: dict, x_ember_agent_id: str | None = Header(default=None),
                 x_ember_token: str | None = Header(default=None)):
    db, actor = _caller(x_ember_agent_id, x_ember_token)
    return _run(lambda: working.submit(db, scope_id, body.get("session_id"), actor, body))


@router.get("/scopes/{scope_id}/items")
async def query(scope_id: str, session_id: str, context: str = "",
                limit: int = Query(default=20, ge=1, le=100),
                x_ember_agent_id: str | None = Header(default=None),
                x_ember_token: str | None = Header(default=None)):
    db, actor = _caller(x_ember_agent_id, x_ember_token)
    return _run(lambda: {"items": working.query(db, scope_id, session_id, actor,
                                                 context=context, limit=limit)})


@router.get("/scopes/{scope_id}/items/{item_id}")
async def get(scope_id: str, item_id: str, session_id: str,
              x_ember_agent_id: str | None = Header(default=None),
              x_ember_token: str | None = Header(default=None)):
    db, actor = _caller(x_ember_agent_id, x_ember_token)
    return _run(lambda: working.get(db, scope_id, session_id, actor, item_id))


@router.get("/scopes/{scope_id}/items/{item_id}/evidence")
async def evidence(scope_id: str, item_id: str, session_id: str,
                   x_ember_agent_id: str | None = Header(default=None),
                   x_ember_token: str | None = Header(default=None)):
    db, actor = _caller(x_ember_agent_id, x_ember_token)
    return _run(lambda: {"evidence_refs": working.get(db, scope_id, session_id, actor,
                                                       item_id, evidence=True)["evidence_refs"]})


@router.patch("/scopes/{scope_id}/items/{item_id}")
async def change(scope_id: str, item_id: str, body: dict,
                 x_ember_agent_id: str | None = Header(default=None),
                 x_ember_token: str | None = Header(default=None)):
    db, actor = _caller(x_ember_agent_id, x_ember_token)
    return _run(lambda: working.change(db, scope_id, body.get("session_id"), actor, item_id,
                                        version=body.get("version"),
                                        correction=body.get("correction"),
                                        withdraw=body.get("withdraw") is True))


@router.post("/scopes/{scope_id}/expire")
async def expire(scope_id: str, body: dict,
                 x_ember_agent_id: str | None = Header(default=None),
                 x_ember_token: str | None = Header(default=None)):
    db, actor = _caller(x_ember_agent_id, x_ember_token)
    return _run(lambda: {"expired": working.expire(db, scope_id, body.get("session_id"), actor)})


@router.post("/scopes/{scope_id}/close")
async def close(scope_id: str, body: dict,
                x_ember_agent_id: str | None = Header(default=None),
                x_ember_token: str | None = Header(default=None)):
    db, actor = _caller(x_ember_agent_id, x_ember_token)
    return _run(lambda: working.close_scope(db, scope_id, body.get("session_id"), actor))
