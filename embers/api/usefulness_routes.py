"""Write APIs are separate from read-only visualizer GET endpoints."""
from pathlib import Path
import json
from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from ..integration.usefulness_service import update, snapshot

router = APIRouter()


def authorized(agent_id, token):
    from . import _get_db
    from .session_gate import resolve_agent
    db = _get_db()
    return db, resolve_agent(db, agent_id, token).agent_id


def authorized_view(request, agent_id, token, namespace):
    from . import _get_db
    from .session_gate import current_session_id
    from ..integration.visualizer_access import verify
    if agent_id or token or current_session_id():
        return authorized(agent_id, token)
    code = request.cookies.get('ember_view')
    if not code:
        raise HTTPException(401, 'Enter an agent-created viewing code')
    db = _get_db()
    try:
        data = verify(db, code, namespace)
    except PermissionError as error:
        raise HTTPException(403, str(error)) from error
    return db, data['actor']


@router.post('/v1/visualizer-access')
async def create_access(body: dict, request: Request, x_ember_agent_id: str | None = Header(default=None), x_ember_token: str | None = Header(default=None)):
    from ..integration.visualizer_access import issue, revoke
    from .session_gate import current_session_id
    db, actor = authorized(x_ember_agent_id, x_ember_token)
    try:
        if body.get('action','create') == 'revoke':
            return revoke(db, body.get('grant_id'), actor)
        if body.get('action','create') != 'create': raise ValueError('Unsupported action')
        result=issue(db, body['namespace'], actor, ttl_seconds=body.get('ttl_seconds',900), session_id=current_session_id())
        result['viewer_url']=str(request.base_url).rstrip('/')+result['viewer_path']
        return JSONResponse(result,headers={'Cache-Control':'no-store'})
    except PermissionError as error: raise HTTPException(403,str(error)) from error
    except (KeyError,ValueError,TypeError) as error: raise HTTPException(400,str(error)) from error


@router.post('/v1/visualizer-access/exchange')
async def exchange_view(body: dict, request: Request):
    from . import _get_db
    from ..integration.visualizer_access import verify, normalize
    try:
        code=normalize(body.get('code'))
        data=verify(_get_db(),code)
    except PermissionError as error: raise HTTPException(403,str(error)) from error
    import time
    response=JSONResponse({'namespace':data['namespace'],'expires_at':data['expires_at'],'scope':'visualizer:read'},headers={'Cache-Control':'no-store'})
    response.set_cookie('ember_view',code,max_age=max(0,int(data['expires_at']-time.time())),httponly=True,
                        secure=request.url.scheme=='https',samesite='strict',path='/v1')
    return response


@router.get('/v1/visualizer-tokenization/{namespace:path}')
async def tokenization(namespace: str, request: Request, memory_id: str,
                       x_ember_agent_id: str | None = Header(default=None), x_ember_token: str | None = Header(default=None)):
    db, actor = authorized_view(request,x_ember_agent_id,x_ember_token,namespace)
    try:
        db.require_namespace_access(namespace,actor,'read')
        rec=db._reader.get(memory_id,track_access=False)
        if rec is None or rec.namespace!=namespace or rec.record_type not in db._DURABLE_MEMORY_TYPES:
            raise HTTPException(404,'Memory not available')
        import os, tiktoken
        encoding=tiktoken.get_encoding(os.environ.get('EMBER_TOKEN_ENCODING','cl100k_base'))
        raw=str(rec.data.get('content','')).encode()
        text=raw[:4096].decode('utf-8',errors='ignore')
        ids=encoding.encode(text,disallowed_special=())
        return JSONResponse({'memory_id':memory_id,'tokenizer':'tiktoken:'+encoding.name,
            'scope':'content preview only; not rendered retrieval context or model internals',
            'input_bytes':len(text.encode()),'input_truncated':len(raw)>4096,'token_count':len(ids),
            'tokens':[{'id':i,'text':encoding.decode_single_token_bytes(i).decode('utf-8',errors='replace'),
                       'bytes_hex':encoding.decode_single_token_bytes(i).hex()} for i in ids[:96]],
            'tokens_truncated':len(ids)>96},headers={'Cache-Control':'no-store'})
    except PermissionError as error: raise HTTPException(403,str(error)) from error


@router.post('/v1/usefulness/{namespace:path}')
async def mutate(namespace: str, body: dict, x_ember_agent_id: str | None = Header(default=None), x_ember_token: str | None = Header(default=None)):
    db, actor = authorized(x_ember_agent_id, x_ember_token)
    try:
        from .session_gate import current_session_id
        return update(db, namespace, actor, body, session_id=current_session_id())
    except PermissionError as error: raise HTTPException(403,str(error)) from error
    except (ValueError,TypeError,KeyError) as error: raise HTTPException(400,str(error)) from error


@router.get('/v1/visualizer-stream/{namespace:path}')
async def live(namespace: str, request: Request, after: int = Query(default=0, ge=0),
               last_event_id: str | None = Header(default=None),
               x_ember_agent_id: str | None = Header(default=None), x_ember_token: str | None = Header(default=None)):
    from .session_gate import resolve_agent, current_session_id
    from ..integration.observation_stream import stream
    db, actor = authorized_view(request,x_ember_agent_id,x_ember_token,namespace)
    session = current_session_id()
    viewer_code = request.cookies.get('ember_view') if not (x_ember_agent_id or x_ember_token or session) else None
    def authorize():
        if viewer_code:
            from ..integration.visualizer_access import verify
            verify(db,viewer_code,namespace)
            return
        identity = resolve_agent(db, x_ember_agent_id, x_ember_token, session_id=session)
        if identity.agent_id != actor:
            raise PermissionError('stream identity changed')
        # A supplied session is bound to this actor even when token auth is used.
        if session:
            actual = db.get_session(session)
            if actual is None or actual.agent_id != actor or actual.status.value != 'active':
                raise PermissionError('stream session is no longer authorized')
        db.require_namespace_access(namespace, actor, 'read')
    try:
        authorize()
        cursor = int(last_event_id) if last_event_id is not None else after
        if cursor < 0: raise ValueError('negative revision')
        from ..integration.usefulness_service import service
        if cursor > service(db, namespace).read(actor=actor)['revision']:
            raise ValueError('cursor ahead of journal; reload snapshot explicitly')
    except PermissionError as error: raise HTTPException(403, str(error)) from error
    except ValueError as error: raise HTTPException(400, str(error)) from error
    async def delivery():
        try:
            async for chunk in stream(db, namespace, actor, cursor, authorize, request.is_disconnected):
                for secret in (x_ember_token, session, viewer_code):
                    if secret:
                        chunk = chunk.replace(json.dumps(secret, ensure_ascii=False)[1:-1], '[REDACTED]')
                yield chunk
        except (PermissionError, HTTPException):
            yield 'event: denied\ndata: {}\n\n'
    return StreamingResponse(delivery(), media_type='text/event-stream', headers={
        'Cache-Control':'no-store', 'X-Accel-Buffering':'no', 'Connection':'keep-alive'})


@router.get('/v1/visualizer/{namespace:path}')
async def view(namespace: str, request: Request, after: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=200), request_id: str | None = None, session_id: str | None = None,
               x_ember_agent_id: str | None = Header(default=None), x_ember_token: str | None = Header(default=None)):
    db, actor = authorized_view(request,x_ember_agent_id,x_ember_token,namespace)
    try:
        from ..integration.observation_stream import public_payload
        data=public_payload(snapshot(db, namespace, actor, after=after, limit=limit, request_id=request_id, session_id=session_id))
        return JSONResponse(data, headers={'Cache-Control':'no-store'})
    except PermissionError as error: raise HTTPException(403,str(error)) from error
    except ValueError as error: raise HTTPException(400,str(error)) from error


@router.get('/visualizer', response_class=HTMLResponse)
async def visualizer():
    # Public shell contains no memory data. Every data request authenticates.
    return HTMLResponse(Path(__file__).with_name('visualizer.html').read_text(), headers={
        'Cache-Control':'no-store', 'Referrer-Policy':'no-referrer', 'Content-Security-Policy': "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'"})
