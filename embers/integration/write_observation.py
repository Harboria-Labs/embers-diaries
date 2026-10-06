"""Durable, non-learning write receipts. Recovery is startup/write-side only.

The pending receipt is fsynced before a memory write; it is not a success event.
Only a sealed, persisted matching memory can produce a journal observation.
"""
from contextvars import ContextVar
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import time

context = ContextVar('ember_write_observation_context', default=None)
log = logging.getLogger('embers.observation.write')


def bind(actor, session=None, request=None):
    context.set({'actor': actor, 'session': session, 'request': request})


def directory(db):
    root=Path(db._path).resolve()
    return root.parent/(root.name+'-write-observations')


def session_ref(value):
    return 'session-sha256:'+hashlib.sha256(value.encode()).hexdigest() if value else None


def eligible(db,record):
    return (record.record_type in db._DURABLE_MEMORY_TYPES and record.retrieval_candidate
            and not record.namespace.startswith('_'))


def _save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    tmp=path.with_name(path.name+'.'+secrets.token_hex(8)+'.tmp')
    try:
        with open(os.open(tmp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'w') as f:
            json.dump(data,f);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
        if os.name!='nt':
            fd=os.open(path.parent,os.O_RDONLY)
            try:os.fsync(fd)
            finally:os.close(fd)
    finally:tmp.unlink(missing_ok=True)


def receipt_path(db,rid):
    return directory(db)/(hashlib.sha256(rid.encode()).hexdigest()+'.json')


def prepare(db,record):
    if not eligible(db,record):return
    ctx=context.get() or {}
    data={'record_id':record.id,'content_hash':record.content_hash,'namespace':record.namespace,
          'actor':ctx.get('actor') or 'system',
          'session_ref':session_ref(ctx.get('session') or record.session_id),
          'request_id':ctx.get('request'),'prepared_at':time.time()}
    _save(receipt_path(db,record.id),data)


def provenance(db,record):
    """Allowlisted, namespace-safe provenance; never copy source_data or credentials."""
    data=record.data if isinstance(record.data,dict) else {}
    extra=data.get('source_ids',[]) if data.get('consolidated') else []
    refs=list(record.derived_from or [])+(extra if isinstance(extra,list) else [])
    sources=[]
    for rid in dict.fromkeys(x for x in refs if isinstance(x,str)):
        if len(sources)>=32:break
        source=db._store.read(rid)
        if source is not None and source.namespace==record.namespace and source.record_type in db._DURABLE_MEMORY_TYPES:
            sources.append(rid)
    return {'creator_agent':record.agent_id,
            'created_at':record.created_at.isoformat(),'version':record.version,
            'source_ids':sources,'consolidated':bool(data.get('consolidated')),
            'source_limit':32,'session_ref':session_ref(record.session_id)}


def deliver(db,path):
    pending=json.loads(path.read_text())
    record=db._store.read(pending['record_id'])
    if record is None:return False
    if (not eligible(db,record) or not record.verify_integrity() or record.content_hash!=pending['content_hash']
        or record.namespace!=pending['namespace']):raise ValueError('Write receipt does not match committed record')
    from .observation_journal import append
    request='memory-write:'+record.id+':'+record.content_hash
    p=provenance(db,record);data=record.data if isinstance(record.data,dict) else {}
    observation={'operation':'memory_consolidated' if p['consolidated'] else 'memory_written',
        'memory_ids':[record.id],'context':data.get('primary_context'),
        'session_ref':pending.get('session_ref'),'origin_request_id':pending.get('request_id'),
        'source_ids':p['source_ids'],'provenance':p,'durable_content_hash':record.content_hash,
        'memory_committed_at':record.created_at.isoformat()}
    event={'kind':'ember.write-observation.v1','id':request,'namespace':record.namespace,
        'actor':pending['actor'],'request_id':request,'created_at':time.time(),
        'action':'observation','observation':observation}
    append(db,event,request)
    from .observation_stream import committed
    committed(db,record.namespace)
    path.unlink(missing_ok=True)
    return True


def install(db):
    if getattr(db,'_write_observations_installed',False):return
    from .observation_journal import initialize,sync
    initialize(db)
    sync(db)
    db._write_observations_installed=True
    def before(record):
        try:prepare(db,record)
        except Exception:log.exception('Write receipt preparation failed; observation recovery may be unavailable')
    def after(record,operation):
        if operation not in ('write','update') or not eligible(db,record):return
        try:
            path=receipt_path(db,record.id)
            if not path.exists():prepare(db,record)
            deliver(db,path)
        except Exception:log.exception('Memory committed; write observation pending recovery')
    db._writer.register_prepare_callback(before)
    db._writer.register_callback(after)
    recover(db)


def recover(db):
    """Explicit boot-time repair; never called from a snapshot/stream/read route."""
    with db._writer.lock:
        for path in directory(db).glob('*.json'):
            try:
                if not deliver(db,path):path.unlink(missing_ok=True) # No committed memory: no success event.
            except Exception:log.exception('Pending write observation recovery failed')
