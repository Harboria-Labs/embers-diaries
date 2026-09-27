"""Research observer capabilities cannot authenticate any ordinary Ember operation."""
import json
import time
from fastapi import APIRouter, Request, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from ..integration import research_observer as observer
from .usefulness_routes import authorized

router=APIRouter()


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
                              session_id=current_session_id(),span_sessions=body.get('span_sessions',True),observer_target=body.get('observer_target'))
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
    response=JSONResponse({k:grant[k] for k in ('observer_id','authorized_observer','observed_agent_id','scope','span_sessions','expires_at')},headers={'Cache-Control':'no-store'})
    response.set_cookie('ember_observer',code,max_age=max(0,int(grant['expires_at']-time.time())),
                        httponly=True,secure=request.url.scheme=='https',samesite='strict',path='/v1/observer')
    return response


@router.get('/v1/observer/events')
async def events(request:Request,cursor:str|None=None):
    db,code,_=access(request)
    try:return JSONResponse(observer.page(db,code,cursor,filters(request)),headers={'Cache-Control':'no-store'})
    except PermissionError as e:raise HTTPException(403,str(e)) from e
    except ValueError as e:raise HTTPException(400,str(e)) from e


@router.get('/v1/observer/stream')
async def stream(request:Request,cursor:str|None=None,last_event_id:str|None=Header(default=None)):
    db,code,grant=access(request)
    cursor=last_event_id or cursor
    try:
        observer._cursor(cursor,grant)
        selected=filters(request)
        if any(len(v)>512 for v in selected.values()):raise ValueError('Filter exceeds bound')
    except ValueError as e:raise HTTPException(400,str(e)) from e
    async def delivery():
        try:
            async for chunk in observer.stream(db,code,cursor,selected,request.is_disconnected):
                yield chunk.replace(code,'[REDACTED]')
        except PermissionError:
            yield 'event: denied\ndata: {}\n\n'
        except ValueError:
            yield 'event: reset\ndata: {"reason":"Observer cursor or journal unavailable; reopen explicitly"}\n\n'
    return StreamingResponse(delivery(),media_type='text/event-stream',headers={
        'Cache-Control':'no-store, no-transform','X-Accel-Buffering':'no','X-Ember-Observation-Protocol':observer.PROTOCOL})
