"""Normal-server research endpoints. Observer credentials confer no write rights."""
from pathlib import Path
from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import HTMLResponse
from ..integration import consolidated
from ..core.domain import call
router=APIRouter()

def auth(a,t):
    from . import _get_db, v1
    db=_get_db()
    return db,v1.require_agent(db,a,t).agent_id

def execute(fn,*args,**kwargs):
    try:return fn(*args,**kwargs)
    except PermissionError as e:raise HTTPException(403,str(e)) from e
    except (ValueError,TypeError,KeyError) as e:raise HTTPException(400,str(e)) from e

@router.get('/v1/research/settings/{namespace:path}')
async def read_settings(namespace:str,x_ember_agent_id:str|None=Header(None),x_ember_token:str|None=Header(None)):
    db,actor=auth(x_ember_agent_id,x_ember_token)
    return execute(consolidated.settings,db,namespace,actor)

@router.post('/v1/research/settings/{namespace:path}')
async def write_settings(namespace:str,body:dict,x_ember_agent_id:str|None=Header(None),x_ember_token:str|None=Header(None)):
    db,actor=auth(x_ember_agent_id,x_ember_token)
    return execute(consolidated.configure,db,namespace,actor,body)

@router.post('/v1/research/validate/{namespace:path}')
async def validate_settings(namespace:str,body:dict,x_ember_agent_id:str|None=Header(None),x_ember_token:str|None=Header(None)):
    db,actor=auth(x_ember_agent_id,x_ember_token)
    execute(db.require_namespace_access,namespace,actor,'read')
    result=execute(call,'config',body.get('config'))
    execute(call,'fur_policy',body.get('policy'))
    return result

@router.post('/v1/research/recall/{namespace:path}')
async def research_recall(namespace:str,body:dict,x_ember_agent_id:str|None=Header(None),x_ember_token:str|None=Header(None)):
    from .session_gate import current_session_id
    db,actor=auth(x_ember_agent_id,x_ember_token)
    return execute(consolidated.recall,db,namespace,actor,**{**body,'session_id':current_session_id() or body.get('session_id')})

@router.get('/research/settings',response_class=HTMLResponse)
async def settings_page():
    return HTMLResponse(Path(__file__).with_name('research_settings.html').read_text(encoding='utf-8'),headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'"})

@router.post('/v1/memory/pair-relationship')
async def pair_relationship(body:dict,x_ember_agent_id:str|None=Header(None),x_ember_token:str|None=Header(None)):
    from ..integration.pairing import link
    db,actor=auth(x_ember_agent_id,x_ember_token)
    return execute(link,db,actor,**body)

@router.get('/v1/epistemic/state/{namespace:path}')
async def epistemic_state(namespace:str,memory_id:str,x_ember_agent_id:str|None=Header(None),x_ember_token:str|None=Header(None)):
    from ..cognitive.epistemic import EpistemicLedger
    db,actor=auth(x_ember_agent_id,x_ember_token)
    return execute(EpistemicLedger(db,namespace).read,memory_id,actor)

@router.post('/v1/epistemic/feedback/{namespace:path}')
async def epistemic_feedback(namespace:str,body:dict,x_ember_agent_id:str|None=Header(None),x_ember_token:str|None=Header(None)):
    from ..cognitive.epistemic import EpistemicLedger
    from .session_gate import current_session_id
    db,actor=auth(x_ember_agent_id,x_ember_token)
    if set(body)-{'action','payload','request_id','expected_revision'}:raise HTTPException(400,'unsupported command fields')
    payload={**body.get('payload',{})}
    if current_session_id():payload['session_id']=current_session_id()
    return execute(EpistemicLedger(db,namespace).apply,body.get('action'),payload,actor=actor,
                   request_id=body.get('request_id'),expected_revision=body.get('expected_revision'))
