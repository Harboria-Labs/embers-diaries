"""Write APIs are separate from read-only visualizer GET endpoints."""
from pathlib import Path
from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from ..integration.usefulness_service import update, snapshot

router = APIRouter()


def authorized(agent_id, token):
    from . import _get_db
    from .session_gate import resolve_agent
    db = _get_db()
    return db, resolve_agent(db, agent_id, token).agent_id


@router.post('/v1/usefulness/{namespace:path}')
async def mutate(namespace: str, body: dict, x_ember_agent_id: str | None = Header(default=None), x_ember_token: str | None = Header(default=None)):
    db, actor = authorized(x_ember_agent_id, x_ember_token)
    try:
        from .session_gate import current_session_id
        return update(db, namespace, actor, body, session_id=current_session_id())
    except PermissionError as error: raise HTTPException(403,str(error)) from error
    except (ValueError,TypeError,KeyError) as error: raise HTTPException(400,str(error)) from error


@router.get('/v1/visualizer/{namespace:path}')
async def view(namespace: str, after: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=200), request_id: str | None = None, session_id: str | None = None,
               x_ember_agent_id: str | None = Header(default=None), x_ember_token: str | None = Header(default=None)):
    db, actor = authorized(x_ember_agent_id, x_ember_token)
    try:
        return JSONResponse(snapshot(db, namespace, actor, after=after, limit=limit, request_id=request_id, session_id=session_id), headers={'Cache-Control':'no-store'})
    except PermissionError as error: raise HTTPException(403,str(error)) from error
    except ValueError as error: raise HTTPException(400,str(error)) from error


@router.get('/visualizer', response_class=HTMLResponse)
async def visualizer():
    # Public shell contains no memory data. Every data request authenticates.
    return HTMLResponse(Path(__file__).with_name('visualizer.html').read_text(), headers={
        'Cache-Control':'no-store', 'Content-Security-Policy': "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'"})
