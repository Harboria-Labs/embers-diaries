"""Delivery journal outside the memory store. Never passed to retrieval or FUR.

Revisions here order observer delivery only; source_journal_revision retains the
unchanged FUR revision. Readers open SQLite in read-only mode and never repair it.
"""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from ..cognitive.usefulness import digest


def path(db):
    p=Path(db._path).resolve()
    return p.parent/(p.name+'-observation-journal.sqlite3')


def initialize(db):
    with sqlite3.connect(path(db)) as conn:
        path(db).chmod(0o600)
        conn.execute('PRAGMA synchronous=FULL')
        conn.execute('CREATE TABLE IF NOT EXISTS events(namespace TEXT NOT NULL, revision INTEGER NOT NULL, event_key TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(namespace,revision), UNIQUE(namespace,event_key))')


def read(db, namespace=None):
    p=path(db)
    if not p.exists():return []
    conn=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True)
    try:
        query='SELECT namespace,revision,payload FROM events'
        rows=conn.execute(query+(' WHERE namespace=?' if namespace is not None else '')+' ORDER BY namespace,revision',() if namespace is None else (namespace,))
        out=[];previous={}
        for ns,rev,payload in rows:
            e=json.loads(payload)
            last,seal=previous.get(ns,(0,None))
            if rev!=last+1 or e.get('previous')!=seal or e['namespace']!=ns or e['revision']!=rev or digest({k:v for k,v in e.items() if k!='seal'})!=e.get('seal'):
                raise ValueError('Observation journal integrity failure')
            previous[ns]=(rev,e['seal']);out.append(e)
        return out
    finally:conn.close()


def append(db,event,key,*,source_revision=None):
    """Atomic unique receipt; called by a committed writer/startup, never viewers."""
    conn=sqlite3.connect(path(db),timeout=30)
    try:
        conn.execute('PRAGMA synchronous=FULL');conn.execute('BEGIN IMMEDIATE')
        old=conn.execute('SELECT payload FROM events WHERE namespace=? AND event_key=?',(event['namespace'],key)).fetchone()
        if old:return json.loads(old[0])
        previous=conn.execute('SELECT revision,payload FROM events WHERE namespace=? ORDER BY revision DESC LIMIT 1',(event['namespace'],)).fetchone()
        rev=previous[0]+1 if previous else 1
        e=deepcopy(event);e.pop('seal',None)
        e.update(revision=rev,observation_revision=rev,source_journal_revision=source_revision,
                 previous=json.loads(previous[1])['seal'] if previous else None)
        e['seal']=digest(e)
        conn.execute('INSERT INTO events VALUES(?,?,?,?)',(e['namespace'],rev,key,json.dumps(e,ensure_ascii=False)))
        conn.commit()
        return e
    finally:conn.close()


def sync(db,namespace=None):
    """Mirror authoritative sealed FUR records after commit / at boot only."""
    from .usefulness_service import service
    namespaces={namespace} if namespace is not None else set()
    if namespace is None:
        for rid in db._store.all_ids():
            if rid.startswith('usefulness-'):
                rec=db._store.read(rid)
                if rec is not None:namespaces.add(rec.namespace)
    for ns in namespaces:
        source=service(db,ns);source._load()
        existing={e['id'] for e in read(db,ns)}
        for event in source._events:
            if event['id'] not in existing:append(db,event,'fur:'+event['id'],source_revision=event['revision'])


class Journal:
    def __init__(self,db,namespace):self.db=db;self.namespace=namespace;self._events=read(db,namespace)
    def events(self,*,actor,after=0,limit=100,request_id=None,session_id=None):
        from .write_observation import session_ref
        self.db.require_namespace_access(self.namespace,actor,'read')
        if type(after) is not int or after<0 or type(limit) is not int or not 1<=limit<=200:raise ValueError('invalid event page')
        selected=[]
        for e in self._events[after:]:
            obs=e.get('observation',{});report=e.get('report',{})
            if request_id is not None and request_id not in (e['request_id'],report.get('query_request_id'),obs.get('origin_request_id')):continue
            if session_id is not None and session_id not in (report.get('session_id'),obs.get('session_id')) and obs.get('session_ref')!=session_ref(session_id):continue
            selected.append(deepcopy(e))
            if len(selected)==limit:break
        return selected


def journal(db,namespace):
    from .usefulness_service import service
    if path(db).exists():return Journal(db,namespace)
    source=service(db,namespace);source._load();return source
