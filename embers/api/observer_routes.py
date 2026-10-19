"""Research observer capabilities cannot authenticate any ordinary Ember operation."""
import json
import time
import asyncio
import logging
import uuid
from contextlib import aclosing
from fastapi import APIRouter, Request, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse, Response
from ..integration import research_observer as observer
from .usefulness_routes import authorized
from . import observer_build

router=APIRouter()


def lobby_access(request):
    from . import _get_db
    from ..lobby import working
    db=_get_db();code=request.cookies.get('ember_observer')
    try:
        grant=observer.verify(db,code)
        scope=grant.get('lobby_scope_id');session=grant.get('lobby_session_id')
        if not scope or not session:raise PermissionError('Lobby observation was not delegated')
        # This read-only projection checks current task membership, session and ACL.
        working.observation_snapshot(db,scope,session,grant['observed_agent_id'])
    except (PermissionError,working.WorkingError) as e:
        raise HTTPException(403,str(e)) from e
    return db,code,grant


@router.get('/v1/observer/lobby')
async def lobby_snapshot(request:Request):
    from ..lobby import working
    db,_,grant=lobby_access(request)
    return JSONResponse(working.observation_snapshot(db,grant['lobby_scope_id'],
                        grant['lobby_session_id'],grant['observed_agent_id']),
                        headers={'Cache-Control':'no-store'})


@router.get('/v1/observer/lobby/stream')
async def lobby_stream(request:Request,after:int=0):
    from ..lobby import working
    from ..lobby.realtime_v2 import stream
    if after<0:raise HTTPException(400,'invalid cursor')
    db,code,grant=lobby_access(request)
    scope=grant['lobby_scope_id'];session=grant['lobby_session_id'];actor=grant['observed_agent_id']
    def permit():
        current=observer.verify(db,code)
        if current.get('lobby_scope_id')!=scope or current.get('lobby_session_id')!=session:
            raise PermissionError('Lobby observation changed')
    return StreamingResponse(stream(db,scope,session,actor,after,request.is_disconnected,
                                    read=working.observation,permit=permit),
                             media_type='text/event-stream',
                             headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})


def access(request):
    from . import _get_db
    db=_get_db();code=request.cookies.get('ember_observer')
    try:grant=observer.verify(db,code)
    except PermissionError as e:raise HTTPException(403,str(e)) from e
    return db,code,grant


def filters(request):
    return {k:request.query_params[k] for k in ('namespace','session','context','memory','event_type') if request.query_params.get(k)}


@router.post('/v1/observer-access')
async def create(body:dict,request:Request,x_ember_agent_id:str|None=Header(default=None),x_ember_token:str|None=Header(default=None)):
    from .session_gate import current_session_id
    db,actor=authorized(x_ember_agent_id,x_ember_token)
    try:
        if body.get('action','create')=='revoke':return observer.revoke(db,body.get('observer_id'),actor)
        if body.get('action','create')!='create':raise ValueError('Invalid action')
        result=observer.issue(db,actor,ttl_seconds=body.get('ttl_seconds',3600),namespaces=body.get('namespaces'),
                              session_id=current_session_id(),span_sessions=body.get('span_sessions',True),observer_target=body.get('observer_target'),
                              lobby_scope_id=body.get('lobby_scope_id'),lobby_session_id=body.get('lobby_session_id'))
        result['visualization_url']=str(request.base_url).rstrip('/')+result['viewer_path']
        return JSONResponse(result,headers={'Cache-Control':'no-store'})
    except PermissionError as e:raise HTTPException(403,str(e)) from e
    except (ValueError,TypeError) as e:raise HTTPException(400,str(e)) from e


@router.post('/v1/observer/exchange')
async def exchange(body:dict,request:Request):
    from . import _get_db
    code=body.get('code')
    try:grant=observer.verify(_get_db(),code)
    except PermissionError as e:raise HTTPException(403,str(e)) from e
    response=JSONResponse({**{k:grant[k] for k in ('observer_id','authorized_observer','observed_agent_id','scope','span_sessions','expires_at')},
                           'lobby_scope_id':grant.get('lobby_scope_id')},headers={'Cache-Control':'no-store'})
    response.set_cookie('ember_observer',code,max_age=max(0,int(grant['expires_at']-time.time())),
                        httponly=True,secure=request.url.scheme=='https',samesite='strict',path='/v1/observer')
    return response


@router.get('/v1/observer/events')
async def events(request:Request,cursor:str|None=None):
    db,code,_=access(request)
    try:return JSONResponse(observer.page(db,code,cursor,filters(request)),headers={'Cache-Control':'no-store'})
    except PermissionError as e:raise HTTPException(403,str(e)) from e
    except ValueError as e:raise HTTPException(400,str(e)) from e


@router.get('/v1/observer/transport.js')
async def transport_script(v:str|None=None):
    if v is not None and v != observer_build.TRANSPORT_SHA256:
        return JSONResponse({'detail':'Observer asset version mismatch; reload the observer page'},
                            status_code=409,headers=observer_build.headers())
    return Response(observer_build.TRANSPORT,media_type='application/javascript',
                    headers=observer_build.headers())


@router.get('/v1/observer/build')
async def build_identity():
    return JSONResponse(observer_build.metadata(),headers=observer_build.headers())


@router.get('/v1/observer/status')
async def transport_status(request:Request):
    _,_,grant=access(request)
    return JSONResponse({'observed_agent_id':grant['observed_agent_id'],
        'expires_at':grant['expires_at'],'remaining_seconds':max(0,grant['expires_at']-time.time()),
        'scope':grant['scope']},headers={'Cache-Control':'no-store'})


@router.get('/v1/observer/stream')
async def stream(request:Request,cursor:str|None=None,last_event_id:str|None=Header(default=None)):
    db,code,grant=access(request)
    cursor=last_event_id or cursor
    try:
        observer._cursor(cursor,grant)
        selected=filters(request)
        if any(len(v)>512 for v in selected.values()):raise ValueError('Filter exceeds bound')
    except ValueError as e:raise HTTPException(400,str(e)) from e
    stream_id=uuid.uuid4().hex
    log=logging.getLogger('embers.observer.transport')
    async def delivery():
        started=time.monotonic();chunks=0;reason='generator_closed'
        async def disconnected():
            nonlocal reason
            gone=await request.is_disconnected()
            if gone:reason='http_disconnect'
            return gone
        log.info('observer_stream_open stream_id=%s',stream_id)
        try:
            # Explicit closing guarantees listener cleanup on ASGI cancellation too.
            async with aclosing(observer.stream(db,code,cursor,selected,disconnected,stream_id=stream_id)) as source:
                async for chunk in source:
                    chunks+=1
                    log.debug('observer_stream_yield stream_id=%s chunk=%s bytes=%s',stream_id,chunks,len(chunk.encode()))
                    yield chunk.replace(code,'[REDACTED]')
                    if chunk.startswith(': heartbeat'):
                        # Transport liveness only: no journal ID, no model/event mutation.
                        yield 'event: keepalive\ndata: '+json.dumps({'stream_id':stream_id})+'\n\n'
        except PermissionError:
            reason='authorization_ended'
            yield 'event: denied\ndata: {"reason":"authorization_ended"}\n\n'
        except ValueError:
            reason='cursor_or_journal_unavailable'
            yield 'event: reset\ndata: {"reason":"Observer cursor or journal unavailable; reopen explicitly"}\n\n'
        except asyncio.CancelledError:
            reason='asgi_cancelled';raise
        except GeneratorExit:
            reason='response_closed';raise
        except OSError:
            reason='socket_write_failed';raise
        finally:
            log.info('observer_stream_close stream_id=%s reason=%s chunks=%s seconds=%.3f',
                     stream_id,reason,chunks,time.monotonic()-started)
    return StreamingResponse(delivery(),media_type='text/event-stream',headers={
        'Cache-Control':'no-store, no-cache, no-transform','X-Accel-Buffering':'no',
        'Content-Encoding':'identity','X-Ember-Stream-Id':stream_id,
        'X-Ember-Observation-Protocol':observer.PROTOCOL})

@router.get('/status')
async def server_status_page():
    # Static observer-only page; no store initialization, retrieval or model calls.
    from pathlib import Path
    from .observer_build import STATUS_MONITOR
    return Response(Path(__file__).with_name('server_status.html').read_text(encoding='utf-8').replace('__STATUS_MONITOR__', STATUS_MONITOR),
                    media_type='text/html',headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer',
                    'Content-Security-Policy':"default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'"})
