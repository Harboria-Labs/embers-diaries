"""Read-only SSE delivery. Wakeups carry no data; the durable journal is authoritative."""
import asyncio
import json
import hashlib
import threading
from pathlib import Path
from .usefulness_service import snapshot, service

_lock = threading.Lock()
_listeners = {}
COLLECTIONS = ('nodes', 'edges', 'states', 'experiences', 'reports')


def key(db, namespace):
    return (str(Path(db._path).resolve()), namespace)


def committed(db, namespace):
    """Called only after native write success. Never persist or publish credentials."""
    with _lock:
        listeners = tuple(_listeners.get(key(db, namespace), ()))
    for loop, wake in listeners:
        try:
            loop.call_soon_threadsafe(wake.set)
        except RuntimeError:
            pass  # Disconnected/closed loops have no delivery authority.


def item_key(kind, item):
    if kind in ('nodes', 'experiences', 'reports'):
        return item['id']
    fields = [item['target'], item['context']] if kind == 'states' else [
        item['kind'], item['from'], item['to'], item['type'], item.get('context')]
    return json.dumps(fields, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def patch(previous, current):
    changes = {}
    for kind in COLLECTIONS:
        old = {item_key(kind, x): x for x in previous.get(kind, [])}
        new = {item_key(kind, x): x for x in current[kind]}
        changes[kind] = {'upsert': [v for k,v in new.items() if old.get(k) != v],
                         'remove': [k for k in old if k not in new]}
    return {'collections': changes, 'events': current['events'],
            'meta': {k:v for k,v in current.items() if k not in COLLECTIONS and k != 'events'}}


def public_payload(value):
    """Session IDs are bearer credentials in Ember; stream correlation uses hashes."""
    if isinstance(value, dict):
        return {k: ('session-sha256:' + hashlib.sha256(str(v).encode()).hexdigest()
                    if k in ('session_id', 'cluster_session') and v is not None else public_payload(v))
                for k,v in value.items() if k.lower() not in ('token','authorization','x-ember-token')}
    if isinstance(value, list):
        return [public_payload(v) for v in value]
    return value


def frame(event, data, revision=None):
    prefix = f'id: {revision}\n' if revision is not None else ''
    return prefix + 'event: ' + event + '\ndata: ' + json.dumps(public_payload(data), ensure_ascii=False, separators=(',', ':')) + '\n\n'


async def stream(db, namespace, actor, after, authorize, disconnected, *, interval=1.):
    """Coalesced in-process push, persisted catch-up and cross-worker recovery.

    Poll the journal on heartbeat as recovery for missed/local-external wakeups.
    No per-client event queue: slow clients recover by durable revision.
    """
    wake = asyncio.Event()
    listener = (asyncio.get_running_loop(), wake)
    stream_key = key(db, namespace)
    with _lock:
        _listeners.setdefault(stream_key, set()).add(listener)
    previous = {}
    cursor = after
    try:
        while not await disconnected():
            authorize()  # Revalidate session/token and namespace, including idle connections.
            wake.clear()  # Clear BEFORE reading; a concurrent commit cannot be lost.
            ledger = service(db, namespace)
            pending = ledger.events(actor=actor, after=cursor, limit=1)
            if pending or not previous:
                current = snapshot(db, namespace, actor, after=cursor, limit=20)
                if cursor > current['revision']:
                    yield frame('reset', {'reason':'cursor_ahead_of_journal'})
                    return
                next_cursor = current['next_after']
                # Limit events to the confirmed prefix actually delivered by snapshot.
                payload = patch(previous, current)
                yield frame('patch', payload, next_cursor)
                previous = current
                cursor = next_cursor
                if cursor < current['revision']:
                    continue
            else:
                yield ': heartbeat\n\n'
            try:
                await asyncio.wait_for(wake.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
    finally:
        with _lock:
            bucket = _listeners.get(stream_key, set())
            bucket.discard(listener)
            if not bucket:
                _listeners.pop(stream_key, None)
