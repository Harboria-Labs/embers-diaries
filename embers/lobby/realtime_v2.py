"""Lobby-only SSE wakeups; ordered events live in the provisional SQLite file."""

import asyncio
import json
import os
import threading

from .working import WorkingError, authorize, events, path

_lock = threading.Lock()
_listeners = {}


def _signal_path(db):
    return str(path(db)) + ".signal"


def committed(db):
    """Notify other workers after the SQLite transaction has committed."""
    signal_path = _signal_path(db)
    with open(signal_path, "wb") as signal_file:
        signal_file.write(os.urandom(8))


async def _cross_worker_watch(db, scope_id, signal, stop):
    """Use OS file notifications, with a ready barrier that closes the subscribe race."""
    from watchfiles._rust_notify import RustNotify

    loop = asyncio.get_running_loop()
    ready = threading.Event()
    failures = []
    signal_path = os.path.normcase(os.path.abspath(_signal_path(db)))
    wal_path = os.path.normcase(os.path.abspath(str(path(db)) + "-wal"))

    def watch():
        try:
            with RustNotify([os.path.dirname(signal_path)], False, False, 300,
                            False, False) as watcher:
                ready.set()
                while not stop.is_set():
                    changes = watcher.watch(100, 10, 1000, stop)
                    if isinstance(changes, (set, list, tuple)) and any(
                            os.path.normcase(os.path.abspath(change[1])) in {signal_path, wal_path}
                            for change in changes):
                        loop.call_soon_threadsafe(signal.set)
        except Exception as exc:
            failures.append(exc)
            loop.call_soon_threadsafe(signal.set)
        finally:
            ready.set()

    task = asyncio.create_task(asyncio.to_thread(watch))
    if not await asyncio.to_thread(ready.wait, 5):
        stop.set()
        raise WorkingError("cross-worker notifier unavailable")
    if failures:
        raise WorkingError("cross-worker notifier unavailable") from failures[0]
    if task.done():
        await task
    return task


def _key(db, scope_id):
    return str(path(db)), scope_id


def wake(db, scope_id):
    with _lock:
        listeners = tuple(_listeners.get(_key(db, scope_id), ()))
    for loop, signal in listeners:
        try:
            loop.call_soon_threadsafe(signal.set)
        except RuntimeError:
            pass


def frame(kind, data, cursor=None):
    id_line = f"id: {cursor}\n" if cursor is not None else ""
    return id_line + "event: " + kind + "\ndata: " + json.dumps(data, separators=(",", ":")) + "\n\n"


async def stream(db, scope_id, session_id, actor, after, disconnected, *, auth_interval=30,
                 read=events, permit=None):
    """Push committed events across workers, with ordered restart catch-up."""
    if permit:
        permit()
    read(db, scope_id, session_id, actor, after=after, limit=1)
    signal = asyncio.Event()
    stop = threading.Event()
    listener = (asyncio.get_running_loop(), signal)
    key = _key(db, scope_id)
    with _lock:
        _listeners.setdefault(key, set()).add(listener)
    try:
        watcher = await _cross_worker_watch(db, scope_id, signal, stop)
    except BaseException:
        with _lock:
            _listeners[key].discard(listener)
            if not _listeners[key]:
                _listeners.pop(key)
        raise
    cursor = after
    try:
        yield frame("ready", {"protocol": "ember-lobby-v2", "scope_id": scope_id})
        while not await disconnected():
            try:
                if watcher.done() and not stop.is_set():
                    raise WorkingError("cross-worker notifier stopped")
                signal.clear()  # Clear before reading so a racing commit remains visible.
                if permit:
                    permit()
                page = read(db, scope_id, session_id, actor, after=cursor)
                if page["reset"]:
                    yield frame("reset", {"reason": page["reason"]})
                    return
                if page["events"]:
                    for event in page["events"]:
                        yield frame("working_memory_changed" if event["change"] == "changed" else
                                    "working_memory_removed" if event["change"] == "removed" else
                                    "scope_changed", event, event["cursor"])
                        cursor = event["cursor"]
                    continue
                try:
                    await asyncio.wait_for(signal.wait(), timeout=auth_interval)
                except asyncio.TimeoutError:
                    # Authorization timeout, not a delivery poll.
                    if permit:
                        permit()
                    if read is events:
                        authorize(db, scope_id, session_id, actor)
                    else:
                        read(db, scope_id, session_id, actor, after=cursor, limit=1)
                    yield ": heartbeat\n\n"
            except PermissionError:
                yield frame("denied", {"reason": "authorization_ended"})
                return
            except WorkingError:
                yield frame("reset", {"reason": "delivery_unavailable"})
                return
    finally:
        stop.set()
        await watcher
        with _lock:
            bucket = _listeners.get(key, set())
            bucket.discard(listener)
            if not bucket:
                _listeners.pop(key, None)
