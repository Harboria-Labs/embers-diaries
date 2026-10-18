"""Lobby-only SSE wakeups; ordered events live in the provisional SQLite file."""

import asyncio
import json
import threading

from .working import WorkingError, authorize, events, path

_lock = threading.Lock()
_listeners = {}


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


async def stream(db, scope_id, session_id, actor, after, disconnected, *, auth_interval=30):
    """Push after local commits, with ordered restart catch-up and no event polling.

    Idle timeout rechecks authorization; it never reads the event table for
    changes. A different worker cannot wake this process; reconnect catches up.
    """
    events(db, scope_id, session_id, actor, after=after, limit=1)
    signal = asyncio.Event()
    listener = (asyncio.get_running_loop(), signal)
    key = _key(db, scope_id)
    with _lock:
        _listeners.setdefault(key, set()).add(listener)
    cursor = after
    try:
        yield frame("ready", {"protocol": "ember-lobby-v2", "scope_id": scope_id})
        while not await disconnected():
            try:
                signal.clear()  # Clear before reading so a racing commit remains visible.
                page = events(db, scope_id, session_id, actor, after=cursor)
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
                    authorize(db, scope_id, session_id, actor)
                    yield ": heartbeat\n\n"
            except (PermissionError, WorkingError):
                yield frame("denied", {"reason": "authorization_ended"})
                return
    finally:
        with _lock:
            bucket = _listeners.get(key, set())
            bucket.discard(listener)
            if not bucket:
                _listeners.pop(key, None)
