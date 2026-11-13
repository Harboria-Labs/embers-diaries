"""Delivery journal outside the memory store. Never passed to retrieval or FUR.

Revisions here order observer delivery only; source_journal_revision retains the
unchanged FUR revision. Readers open SQLite in read-only mode and never repair it.
"""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import threading
from ..cognitive.usefulness import digest

_LOCK = threading.Lock()
# (journal path, namespace) -> verified events for that file stat.
# A write used to re-read and re-hash the whole chain; the cache makes the
# next read of an unchanged file O(1). read(namespace=None) stays uncached.
_CACHE: dict[tuple[str, str], dict] = {}
_STATS = {"full_reads": 0, "cache_hits": 0}


def read_stats():
    """How many journal reads verified the chain versus reused it."""
    return dict(_STATS)


def _file_stat(p):
    st = p.stat()
    return st.st_size, st.st_mtime_ns


def _load_rows(db, namespace):
    p = path(db)
    conn = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True)
    try:
        query = "SELECT namespace,revision,payload FROM events"
        rows = conn.execute(
            query + (" WHERE namespace=?" if namespace is not None else "")
            + " ORDER BY namespace,revision",
            () if namespace is None else (namespace,))
        out = []
        previous = {}
        for ns, rev, payload in rows:
            e = json.loads(payload)
            last, seal = previous.get(ns, (0, None))
            if (rev != last + 1 or e.get("previous") != seal or e["namespace"] != ns
                    or e["revision"] != rev
                    or digest({k: v for k, v in e.items() if k != "seal"}) != e.get("seal")):
                raise ValueError("Observation journal integrity failure")
            previous[ns] = (rev, e["seal"])
            out.append(e)
        return out
    finally:
        conn.close()


def _cache_append(p, event):
    key = (str(p), event["namespace"])
    bucket = _CACHE.get(key)
    if not bucket:
        return
    rows = bucket["events"]
    if rows:
        if (rows[-1]["revision"] + 1 != event["revision"]
                or event.get("previous") != rows[-1].get("seal")):
            _CACHE.pop(key, None)
            return
    elif event["revision"] != 1 or event.get("previous") is not None:
        _CACHE.pop(key, None)
        return
    rows.append(event)
    try:
        size, mtime = _file_stat(p)
    except OSError:
        _CACHE.pop(key, None)
        return
    bucket["size"] = size
    bucket["mtime_ns"] = mtime


def path(db):
    p=Path(db._path).resolve()
    return p.parent/(p.name+'-observation-journal.sqlite3')


def initialize(db):
    with sqlite3.connect(path(db)) as conn:
        path(db).chmod(0o600)
        conn.execute('PRAGMA synchronous=FULL')
        conn.execute('CREATE TABLE IF NOT EXISTS events(namespace TEXT NOT NULL, revision INTEGER NOT NULL, event_key TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(namespace,revision), UNIQUE(namespace,event_key))')


def read(db, namespace=None):
    p = path(db)
    if not p.exists():
        return []
    if namespace is None:
        _STATS["full_reads"] += 1
        return _load_rows(db, None)
    key = (str(p), namespace)
    with _LOCK:
        try:
            size, mtime = _file_stat(p)
        except OSError:
            return []
        bucket = _CACHE.get(key)
        if bucket and bucket["size"] == size and bucket["mtime_ns"] == mtime:
            _STATS["cache_hits"] += 1
            return list(bucket["events"])
        _STATS["full_reads"] += 1
        events = _load_rows(db, namespace)
        try:
            size2, mtime2 = _file_stat(p)
        except OSError:
            return events
        if (size2, mtime2) == (size, mtime):
            _CACHE[key] = {"size": size, "mtime_ns": mtime, "events": events}
        return list(events)


def append(db, event, key, *, source_revision=None):
    """Atomic unique receipt; called by a committed writer/startup, never viewers."""
    p = path(db)
    with _LOCK:
        conn = sqlite3.connect(p, timeout=30)
        try:
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute(
                "SELECT payload FROM events WHERE namespace=? AND event_key=?",
                (event["namespace"], key)).fetchone()
            if old:
                return json.loads(old[0])
            previous = conn.execute(
                "SELECT revision,payload FROM events WHERE namespace=? ORDER BY revision DESC LIMIT 1",
                (event["namespace"],)).fetchone()
            rev = previous[0] + 1 if previous else 1
            e = deepcopy(event)
            e.pop("seal", None)
            e.update(revision=rev, observation_revision=rev, source_journal_revision=source_revision,
                     previous=json.loads(previous[1])["seal"] if previous else None)
            e["seal"] = digest(e)
            conn.execute("INSERT INTO events VALUES(?,?,?,?)",
                         (e["namespace"], rev, key, json.dumps(e, ensure_ascii=False)))
            conn.commit()
        finally:
            conn.close()
        _cache_append(p, e)
        return e


def sync(db,namespace=None):
    """Mirror authoritative sealed FUR records after commit / at boot only."""
    from .usefulness_service import service
    namespaces={namespace} if namespace is not None else set()
    if namespace is None:
        for rid in db._store.all_ids():
            if rid.startswith('usefulness-'):
                rec=db._store.read(rid)
                if rec is not None:namespaces.add(rec.namespace)
    from ..cognitive.epistemic import EpistemicLedger
    ella_namespaces={db._store.read(rid).namespace for rid in db._store.all_ids() if rid.startswith('epistemic-')}
    for ns in sorted(ella_namespaces):
        if namespace is not None and ns!=namespace:continue
        _,events=EpistemicLedger(db,ns).load()
        existing={e['id'] for e in read(db,ns)}
        for event in events:
            if event['id'] not in existing:append(db,event,'ella:'+event['id'],source_revision=event['revision'])
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
