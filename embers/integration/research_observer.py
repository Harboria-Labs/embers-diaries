"""Read-only agent observation; capability metadata lives outside the memory store."""
import asyncio
import base64
import hashlib
import heapq
import json
import os
from pathlib import Path
import secrets
import time
import uuid
from ..cognitive.usefulness import KIND, digest
from .observation_stream import _lock, _listeners, key, frame, public_payload

PROTOCOL = 'ember-agent-observer.v1'
MAX_NAMESPACES = 128


def _directory(db):
    root=Path(db._path).resolve()
    return root.parent / (root.name+'-observer-access')


def _id(code):
    if not isinstance(code,str) or len(code)!=48 or any(c not in '0123456789abcdef' for c in code):
        raise PermissionError('Invalid observer capability')
    return hashlib.sha256(code.encode()).hexdigest()


def _save(path, data):
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.'+secrets.token_hex(8)+'.tmp')
    try:
        with open(os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as out:
            json.dump(data,out);out.flush();os.fsync(out.fileno())
        os.replace(tmp,path)
    finally:
        tmp.unlink(missing_ok=True)


def _events(db, actor, namespaces=None):
    # Reads only committed RAW journal records; never calls recall/learning.
    for rid in db._store.all_ids():
        if not rid.startswith('usefulness-'):continue
        rec=db._store.read(rid)
        if rec is None or not isinstance(rec.data,dict):continue
        e=rec.data
        if e.get('kind')!=KIND or e.get('actor')!=actor:continue
        ns=e.get('namespace')
        if namespaces is not None and ns not in namespaces:continue
        if not db.check_namespace_access(ns,actor,'read'):continue
        if rec.namespace!=ns or not rec.verify_integrity() or digest({k:v for k,v in e.items() if k!='seal'})!=e.get('seal'):
            raise ValueError('Observer journal integrity failure')
        yield e


def issue(db, actor, *, ttl_seconds=3600, namespaces=None, session_id=None, span_sessions=True, observer_target=None):
    if observer_target is not None and observer_target!=actor:
        raise PermissionError('Agents may delegate observation of themselves only')
    if type(ttl_seconds) is not int or not 60<=ttl_seconds<=86400:raise ValueError('ttl_seconds must be 60..86400')
    if type(span_sessions) is not bool:raise ValueError('span_sessions must be boolean')
    if namespaces is not None:
        if not isinstance(namespaces,list) or not 1<=len(namespaces)<=MAX_NAMESPACES or any(not isinstance(n,str) or not n or len(n)>256 for n in namespaces):raise ValueError('Invalid namespace scope')
        for ns in namespaces:db.require_namespace_access(ns,actor,'read')
        namespaces=list(dict.fromkeys(namespaces))
    if not span_sessions:
        actual=db.get_session(session_id) if session_id else None
        if actual is None or actual.agent_id!=actor or actual.status.value!='active':raise PermissionError('Active own session required')
    baseline={}
    with db._writer.lock:
        for e in _events(db,actor,namespaces):baseline[e['namespace']]=max(baseline.get(e['namespace'],0),e['revision'])
        if len(baseline)>MAX_NAMESPACES:raise ValueError('Observer namespace capacity exceeded; supply a bounded scope')
        code=secrets.token_hex(24);observer_id=_id(code)
        data={'observer_id':observer_id,'authorized_observer':'capability-holder:'+uuid.uuid4().hex,
              'observed_agent_id':actor,'scope':'agent-activity:read','namespaces':namespaces,
              'session_id':None if span_sessions else session_id,'span_sessions':span_sessions,
              'created_at':time.time(),'expires_at':time.time()+ttl_seconds,'baseline':baseline,'revoked':False}
        _save(_directory(db)/(observer_id+'.json'),data)
    return {'code':code,'observer_id':observer_id,'authorized_observer':data['authorized_observer'],
            'observed_agent_id':actor,'scope':data['scope'],'span_sessions':span_sessions,'expires_at':data['expires_at'],
            'viewer_path':'/visualizer?mode=observer#observe='+code}


def verify(db, code):
    path=_directory(db)/(_id(code)+'.json')
    try:data=json.loads(path.read_text())
    except (OSError,ValueError):raise PermissionError('Observer capability unavailable') from None
    if data.get('revoked') or data['expires_at']<=time.time():raise PermissionError('Observer capability expired or revoked')
    if data.get('session_id'):
        s=db.get_session(data['session_id'])
        if s is None or s.agent_id!=data['observed_agent_id'] or s.status.value!='active':raise PermissionError('Observed session ended')
    return data


def revoke(db, observer_id, actor):
    if not isinstance(observer_id,str) or len(observer_id)!=64 or any(c not in '0123456789abcdef' for c in observer_id):raise PermissionError('Invalid observer ID')
    path=_directory(db)/(observer_id+'.json')
    try:data=json.loads(path.read_text())
    except (OSError,ValueError):raise PermissionError('Observer capability unavailable') from None
    if data['observed_agent_id']!=actor:raise PermissionError('Only the observed agent may revoke this capability')
    data['revoked']=True;_save(path,data)
    return {'observer_id':observer_id,'revoked':True}


def _ref(value):
    return 'session-sha256:'+hashlib.sha256(value.encode()).hexdigest() if value else None


def _metadata(e):
    obs=e.get('observation',{});r=e.get('report',{});exps=e.get('experiences',[])
    contexts=list(dict.fromkeys([x.get('context') for x in exps]+[d.get('after',{}).get('context') for d in e.get('transitions',[])]))
    context=obs.get('context',r.get('context',contexts[0] if len(contexts)==1 else None))
    session=obs.get('session_id') or r.get('session_id')
    ids=list(dict.fromkeys(obs.get('candidate_ids',[])+obs.get('returned_ids',[])+r.get('target',{}).get('memory_ids',[])+[m for x in exps for m in x['target']['memory_ids']]))
    return {'namespace':e['namespace'],'session':_ref(session),'context':context,'contexts':contexts,
            'memory_ids':ids,'event_type':obs.get('operation') or r.get('feedback_type') or e['action']},session


def _project(db,e):
    meta,_=_metadata(e);obs=e.get('observation',{});r=e.get('report',{})
    changes=[]
    for delta in e.get('transitions',[])[:100]:
        def state(s):return {k:s.get(k) for k in ('metric','value','N_eff','context','target')} if s else None
        changes.append({'before':state(delta.get('before')),'after':state(delta.get('after'))})
    memories=[]
    for rid in meta['memory_ids'][:100]:
        rec=db._reader.get(rid,track_access=False)
        if rec is not None and rec.namespace==e['namespace'] and rec.record_type in db._DURABLE_MEMORY_TYPES:
            d=rec.data if isinstance(rec.data,dict) else {}
            memories.append({'id':rid,'namespace':rec.namespace,'preview':str(d.get('content',''))[:500],
                             'subject':d.get('subject'),'primary_context':d.get('primary_context'),'verify_status':d.get('verify_status')})
    stage=lambda yes:'OBSERVED' if yes else 'NOT OBSERVED'
    out={**meta,'event_id':e['id'],'revision':e['revision'],'timestamp':e['created_at'],
         'observed_agent_id':e['actor'],'request_id':e['request_id'],'action':e['action'],
         'affected_experience_ids':[x['id'] for x in e.get('experiences',[])][:100],
         'experiences':[{'id':x['id'],'target':x['target'],'resolution_status':x.get('resolution_status'),
                         'outcome':x.get('resolved_feedback_type'),'active':x.get('active')} for x in e.get('experiences',[])][:100],
         'changes':changes,'memories':memories,'actual_H':obs.get('observed_heat',{}),
         'heat_source':obs.get('heat_source'),'query':obs.get('query'),'returned_ids':obs.get('returned_ids',[])[:100],
         'candidate_ids':obs.get('candidate_ids',[])[:100],'budget':obs.get('budget'),
         'pipeline':{'query':stage(bool(obs.get('query'))),'context':stage(meta['context'] is not None),
                     'retrieval':stage(bool(obs)),'direct_memory':'NOT OBSERVED','LADC_reactivation':'NOT OBSERVED',
                     'pair_expansion':'NOT OBSERVED','returned_memory':stage(bool(obs.get('returned_ids'))),
                     'agent_use':'REPORTED' if r.get('feedback_type') in ('CONTRIBUTED','PAIR_HELPED','GROUP_SUCCESS','MISLEADING') else 'NOT OBSERVED',
                     'feedback':stage(bool(r) or e['action']=='resolve'),'evidence':stage(bool(e.get('experiences'))),
                     'U_W_update':stage(bool(changes))}}
    out=public_payload(out)
    if len(json.dumps(out).encode())>32768:
        out={k:out[k] for k in ('namespace','session','context','event_type','event_id','revision','timestamp','observed_agent_id','request_id','action')}
        out.update(details_omitted=True,memory_ids=[],memories=[],changes=[],experiences=[],actual_H={},pipeline={})
    return out


def _cursor(value, grant):
    if value is None:return dict(grant['baseline'])
    if not isinstance(value,str) or len(value)>180000:raise ValueError('Invalid observer cursor')
    try:d=json.loads(base64.urlsafe_b64decode(value+'='*(-len(value)%4)))
    except Exception:raise ValueError('Invalid observer cursor') from None
    if d.get('observer_id')!=grant['observer_id']:raise ValueError('Cursor belongs to another observer')
    seen=d.get('seen')
    if not isinstance(seen,dict) or len(seen)>MAX_NAMESPACES or any(not isinstance(k,str) or len(k)>256 or type(v) is not int or v<0 for k,v in seen.items()):raise ValueError('Invalid observer cursor')
    return {k:max(v,grant['baseline'].get(k,0)) for k,v in {**grant['baseline'],**seen}.items()}


def _encode(seen,grant):
    if len(seen)>MAX_NAMESPACES:raise ValueError('Observer namespace capacity exceeded; narrow capability scope')
    return base64.urlsafe_b64encode(json.dumps({'observer_id':grant['observer_id'],'seen':seen},separators=(',',':')).encode()).decode().rstrip('=')


def page(db,code,cursor=None,filters=None,limit=20):
    grant=verify(db,code);seen=_cursor(cursor,grant);filters=filters or {}
    if set(filters)-{'namespace','session','context','memory','event_type'} or any(not isinstance(v,str) or len(v)>512 for v in filters.values()):raise ValueError('Invalid observer filters')
    if type(limit) is not int or not 1<=limit<=50:raise ValueError('limit must be 1..50')
    buckets={}
    with db._writer.lock:
        for e in _events(db,grant['observed_agent_id'],grant['namespaces']):
            if e['revision']>seen.get(e['namespace'],0):buckets.setdefault(e['namespace'],[]).append(e)
        heap=[]
        for ns,events in buckets.items():
            events.sort(key=lambda e:e['revision']);e=events[0]
            heapq.heappush(heap,(e['created_at'],ns,0))
        out=[];scanned=0;size=0
        while heap and len(out)<limit and scanned<500:
            _,ns,i=heapq.heappop(heap);e=buckets[ns][i];meta,session=_metadata(e)
            seen[ns]=e['revision'];scanned+=1
            if i+1<len(buckets[ns]):heapq.heappush(heap,(buckets[ns][i+1]['created_at'],ns,i+1))
            if grant['session_id'] and session!=grant['session_id']:continue
            if any((v not in meta['memory_ids'] if k=='memory' else meta.get(k)!=v) for k,v in filters.items() if v):continue
            projected=_project(db,e);out.append(projected);size+=len(json.dumps(projected).encode())
            if size>160000:break
        verify(db,code)
        out=[e for e in out if db.check_namespace_access(e['namespace'],grant['observed_agent_id'],'read')]
        return {'protocol':PROTOCOL,'observer_id':grant['observer_id'],'observed_agent_id':grant['observed_agent_id'],
                'events':out,'cursor':_encode(seen,grant),'more':bool(heap),
                'limits':{'events':limit,'namespaces':MAX_NAMESPACES,'event_bytes':32768,'page_bytes':262144},
                'scope':grant['scope'],'span_sessions':grant['span_sessions']}


async def stream(db,code,cursor,filters,disconnected,interval=1.):
    grant=verify(db,code)
    wake=asyncio.Event();listener=(asyncio.get_running_loop(),wake);stream_key=key(db,None)
    with _lock:_listeners.setdefault(stream_key,set()).add(listener)
    try:
        yield frame('ready',{'transport':'SSE','protocol':PROTOCOL,'observed_agent_id':grant['observed_agent_id']})
        while not await disconnected():
            verify(db,code);wake.clear();data=page(db,code,cursor,filters)
            next_cursor=data['cursor']
            verify(db,code)  # Revalidate immediately before delivery.
            if data['events'] or next_cursor!=cursor:
                # ID is a namespace-vector checkpoint, never a fabricated global revision.
                yield frame('observer',data,next_cursor)
            else:yield ': heartbeat\n\n'
            cursor=next_cursor
            if data['more']:continue
            try:await asyncio.wait_for(wake.wait(),interval)
            except asyncio.TimeoutError:pass
    finally:
        with _lock:
            listeners=_listeners.get(stream_key,set());listeners.discard(listener)
            if not listeners:_listeners.pop(stream_key,None)
