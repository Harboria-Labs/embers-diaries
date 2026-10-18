"""Restart-resilient provisional Lobby state, separate from Ember records."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import re
import sqlite3
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from ..core.types import SessionStatus

TTL_SECONDS = 7 * 86400
MAX_ITEMS_PER_SCOPE = 1000
MAX_EVENTS_PER_SCOPE = 2000
MAX_CLAIM_BYTES = 4096
MAX_EVIDENCE = 16
KINDS = {"finding", "failure", "question", "outcome"}
UNCERTAINTY = {"unverified", "observed", "contradicted", "resolved"}


class WorkingError(ValueError):
    pass


def path(db):
    p = Path(db._path).resolve()
    return p.parent / (p.name + "-lobby-v2.sqlite3")


@contextmanager
def _open(db):
    p = path(db)
    conn = sqlite3.connect(p, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS scopes (
      id TEXT PRIMARY KEY, namespace TEXT NOT NULL, task TEXT NOT NULL,
      room TEXT NOT NULL, owner TEXT NOT NULL, status TEXT NOT NULL,
      created REAL NOT NULL, floor_seq INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS members (
      scope_id TEXT NOT NULL, agent_id TEXT NOT NULL, role TEXT NOT NULL,
      PRIMARY KEY(scope_id,agent_id));
    CREATE TABLE IF NOT EXISTS items (
      id TEXT PRIMARY KEY, scope_id TEXT NOT NULL, agent_id TEXT NOT NULL,
      session_id TEXT NOT NULL, submission_id TEXT NOT NULL,
      version INTEGER NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL,
      created REAL NOT NULL, updated REAL NOT NULL, expires REAL NOT NULL,
      UNIQUE(scope_id,agent_id,submission_id));
    CREATE INDEX IF NOT EXISTS items_scope ON items(scope_id,status,expires);
    CREATE TABLE IF NOT EXISTS events (
      seq INTEGER PRIMARY KEY AUTOINCREMENT, scope_id TEXT NOT NULL,
      item_id TEXT NOT NULL, version INTEGER NOT NULL, kind TEXT NOT NULL,
      created REAL NOT NULL);
    CREATE INDEX IF NOT EXISTS events_scope ON events(scope_id,seq);
    """)
    if "floor_seq" not in {row[1] for row in conn.execute("PRAGMA table_info(scopes)")}:
        conn.execute("ALTER TABLE scopes ADD COLUMN floor_seq INTEGER NOT NULL DEFAULT 0")
    before = conn.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]
    try:
        with conn:
            yield conn
        scopes = [row[0] for row in conn.execute(
            "SELECT DISTINCT scope_id FROM events WHERE seq>?", (before,))]
        if scopes:
            from .realtime_v2 import wake
            for scope_id in scopes:
                wake(db, scope_id)
    finally:
        conn.close()


def _session(db, session_id, actor):
    session = db.get_session(session_id)
    if session is None or session.status != SessionStatus.ACTIVE or session.agent_id != actor:
        raise PermissionError("active owned session required")
    return session


def _scope(conn, db, scope_id, session_id, actor, operation):
    session = _session(db, session_id, actor)
    scope = conn.execute("SELECT * FROM scopes WHERE id=?", (scope_id,)).fetchone()
    if scope is None or scope["status"] != "open":
        raise WorkingError("shared task is not open")
    if session.namespace != scope["namespace"] or session.task != scope["task"]:
        raise PermissionError("session task or namespace does not match shared task")
    member = conn.execute("SELECT role FROM members WHERE scope_id=? AND agent_id=?",
                          (scope_id, actor)).fetchone()
    if member is None:
        raise PermissionError("shared-task membership required")
    db.require_namespace_access(scope["namespace"], actor, operation)
    return scope, member["role"]


def create_scope(db, session_id, actor, *, task, namespace, room="task"):
    session = _session(db, session_id, actor)
    if not task or task != session.task or namespace != session.namespace or room not in {"task", "project"}:
        raise WorkingError("scope must match the active session task and namespace")
    db.require_namespace_access(namespace, actor, "write")
    scope_id = str(uuid.uuid4())
    with _open(db) as conn:
        conn.execute("INSERT INTO scopes(id,namespace,task,room,owner,status,created) VALUES(?,?,?,?,?,?,?)",
                     (scope_id, namespace, task, room, actor, "open", time.time()))
        conn.execute("INSERT INTO members VALUES(?,?,?)", (scope_id, actor, "owner"))
    return {"scope_id": scope_id, "task": task, "namespace": namespace, "room": room}


def grant(db, scope_id, session_id, actor, grantee):
    with _open(db) as conn:
        scope, role = _scope(conn, db, scope_id, session_id, actor, "write")
        if role != "owner":
            raise PermissionError("shared-task owner required")
        if not grantee or grantee == actor:
            raise WorkingError("different agent required")
        if db.check_namespace_access(scope["namespace"], grantee, "read") is False:
            raise PermissionError("grantee lacks namespace read access")
        conn.execute("INSERT OR IGNORE INTO members VALUES(?,?,?)", (scope_id, grantee, "member"))
        _event(conn, scope_id, "", 0, "membership_changed")
    return {"scope_id": scope_id, "agent_id": grantee, "role": "member"}


def revoke(db, scope_id, session_id, actor, grantee):
    with _open(db) as conn:
        _, role = _scope(conn, db, scope_id, session_id, actor, "write")
        if role != "owner" or grantee == actor:
            raise PermissionError("shared-task owner required")
        conn.execute("DELETE FROM members WHERE scope_id=? AND agent_id=?", (scope_id, grantee))
        _event(conn, scope_id, "", 0, "membership_changed")
    return {"scope_id": scope_id, "revoked": grantee}


def _reference(value):
    if not isinstance(value, str) or len(value) > 512:
        raise WorkingError("invalid evidence reference")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise WorkingError("evidence reference must be a clean HTTPS URL")
    if re.search(r"(?i)(token|secret|password|key)=", value):
        raise WorkingError("sensitive evidence reference")
    return value


def _payload(data, actor, session_id):
    if data.get("share") is not True:
        raise WorkingError("explicit share=true required")
    kind, uncertainty = data.get("kind"), data.get("uncertainty", "unverified")
    claim = data.get("claim")
    if kind not in KINDS or uncertainty not in UNCERTAINTY or not isinstance(claim, str) or not claim.strip():
        raise WorkingError("kind, uncertainty and nonempty claim required")
    if len(claim.encode("utf-8")) > MAX_CLAIM_BYTES:
        raise WorkingError("claim too large")
    if data.get("evidence_status", "unavailable") not in {"attached", "unavailable", "withheld"}:
        raise WorkingError("invalid evidence status")
    evidence = data.get("evidence_refs", [])
    if not isinstance(evidence, list) or len(evidence) > MAX_EVIDENCE:
        raise WorkingError("invalid evidence list")
    clean = []
    for row in evidence:
        if not isinstance(row, dict):
            raise WorkingError("invalid evidence row")
        ref = _reference(row.get("reference"))
        observed = row.get("observed_at")
        if not isinstance(observed, str) or not observed:
            raise WorkingError("observed_at required")
        clean.append({"evidence_id": str(uuid.uuid4()), "reference": ref,
                      "source": str(row.get("source", "agent-declared"))[:100],
                      "observed_at": observed, "agent_id": actor,
                      "session_id": session_id,
                      "execution_context": str(row.get("execution_context", ""))[:500],
                      "uncertainty": str(row.get("uncertainty", "unverified"))[:100],
                      "availability": "external"})
    return {"kind": kind, "claim": claim.strip(), "uncertainty": uncertainty,
            "uncertainty_note": str(data.get("uncertainty_note", ""))[:500],
            "approach": str(data.get("approach", ""))[:200],
            "evidence_status": "attached" if clean else data.get("evidence_status", "unavailable"),
            "evidence_refs": clean, "contributions": []}


def _event(conn, scope_id, item_id, version, kind):
    conn.execute("INSERT INTO events(scope_id,item_id,version,kind,created) VALUES(?,?,?,?,?)",
                 (scope_id, item_id, version, kind, time.time()))
    # Keep an ordered, bounded replay window for the future SSE route.
    cutoff = conn.execute("SELECT seq FROM events WHERE scope_id=? ORDER BY seq DESC LIMIT 1 OFFSET ?",
                          (scope_id, MAX_EVENTS_PER_SCOPE)).fetchone()
    if cutoff:
        conn.execute("DELETE FROM events WHERE scope_id=? AND seq<=?", (scope_id, cutoff[0]))
        conn.execute("UPDATE scopes SET floor_seq=? WHERE id=?", (cutoff[0], scope_id))


def events(db, scope_id, session_id, actor, *, after=0, limit=100):
    if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise WorkingError("invalid event cursor or limit")
    with _open(db) as conn:
        scope, _ = _scope(conn, db, scope_id, session_id, actor, "read")
        floor = scope["floor_seq"]
        if after < floor:
            return {"reset": True, "reason": "missed_history", "events": [], "cursor": floor}
        last = conn.execute("SELECT COALESCE(MAX(seq),0) FROM events WHERE scope_id=?", (scope_id,)).fetchone()[0]
        if after > last or (after and after != floor and not conn.execute(
                "SELECT 1 FROM events WHERE scope_id=? AND seq=?", (scope_id, after)).fetchone()):
            return {"reset": True, "reason": "invalid_cursor", "events": [], "cursor": last}
        rows = conn.execute("SELECT seq,item_id,version,kind,created FROM events WHERE scope_id=? AND seq>? ORDER BY seq LIMIT ?",
                            (scope_id, after, limit)).fetchall()
        items = [{"cursor": r["seq"], "item_id": r["item_id"], "version": r["version"],
                  "change": r["kind"], "at": r["created"]} for r in rows]
        return {"reset": False, "events": items, "cursor": items[-1]["cursor"] if items else after}


def authorize(db, scope_id, session_id, actor):
    with _open(db) as conn:
        _scope(conn, db, scope_id, session_id, actor, "read")


def _prune(conn, scope_id):
    # An expired key remains retryable through one additional TTL, then leaves
    # provisional storage. Durable handoff receipts will be separate in Batch 3.
    conn.execute("DELETE FROM items WHERE scope_id=? AND expires<? AND status!='active'",
                 (scope_id, time.time() - TTL_SECONDS))


def _view(row, *, evidence=False):
    payload = json.loads(row["payload"])
    payload.pop("_submission_digest", None)
    if evidence:
        payload["evidence_refs"] = [{**e, "session_id": "session-sha256:" + hashlib.sha256(e["session_id"].encode()).hexdigest()}
                                    for e in payload["evidence_refs"]]
    else:
        payload["evidence_refs"] = [{k: e[k] for k in ("evidence_id", "source", "observed_at", "agent_id", "availability")}
                                    for e in payload["evidence_refs"]]
    return {"item_id": row["id"], "scope_id": row["scope_id"],
            "agent_id": row["agent_id"], "version": row["version"],
            "status": row["status"], "created_at": row["created"],
            "updated_at": row["updated"], "expires_at": row["expires"], **payload}


def submit(db, scope_id, session_id, actor, data):
    key = data.get("client_submission_id")
    if not isinstance(key, str) or not 1 <= len(key) <= 128:
        raise WorkingError("client_submission_id required")
    payload = _payload(data, actor, session_id)
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()
    payload["_submission_digest"] = digest
    with _open(db) as conn:
        _scope(conn, db, scope_id, session_id, actor, "write")
        _prune(conn, scope_id)
        old = conn.execute("SELECT * FROM items WHERE scope_id=? AND agent_id=? AND submission_id=?",
                           (scope_id, actor, key)).fetchone()
        if old:
            if json.loads(old["payload"]).get("_submission_digest") != digest:
                raise WorkingError("submission key reused with different content")
            return _view(old)
        count = conn.execute("SELECT count(*) FROM items WHERE scope_id=? AND status='active' AND expires>?",
                             (scope_id, time.time())).fetchone()[0]
        if count >= MAX_ITEMS_PER_SCOPE:
            raise WorkingError("shared task capacity reached")
        now, item_id = time.time(), str(uuid.uuid4())
        conn.execute("INSERT INTO items VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (item_id, scope_id, actor, session_id, key, 1, "active",
                      json.dumps(payload), now, now, now + TTL_SECONDS))
        _event(conn, scope_id, item_id, 1, "changed")
        return _view(conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone())


def get(db, scope_id, session_id, actor, item_id, *, evidence=False):
    with _open(db) as conn:
        _scope(conn, db, scope_id, session_id, actor, "read")
        row = conn.execute("SELECT * FROM items WHERE id=? AND scope_id=?", (item_id, scope_id)).fetchone()
        if row is None:
            raise WorkingError("item not found")
        if row["status"] != "active" or row["expires"] <= time.time():
            raise WorkingError("item no longer active")
        return _view(row, evidence=evidence)


def query(db, scope_id, session_id, actor, *, context="", limit=20):
    if not isinstance(limit, int) or not 1 <= limit <= 100:
        raise WorkingError("invalid limit")
    with _open(db) as conn:
        _scope(conn, db, scope_id, session_id, actor, "read")
        rows = conn.execute("SELECT * FROM items WHERE scope_id=? AND status='active' AND expires>? ORDER BY updated DESC LIMIT ?",
                            (scope_id, time.time(), MAX_ITEMS_PER_SCOPE)).fetchall()
    words = set(re.findall(r"\w+", context.lower())) if isinstance(context, str) else set()
    ranked = sorted(rows, key=lambda r: (-len(words & set(re.findall(r"\w+", json.loads(r["payload"])["claim"].lower()))),
                                         -r["updated"], r["id"]))
    return [_view(r) for r in ranked[:limit]]


def change(db, scope_id, session_id, actor, item_id, *, version, correction=None, withdraw=False):
    with _open(db) as conn:
        _scope(conn, db, scope_id, session_id, actor, "write")
        row = conn.execute("SELECT * FROM items WHERE id=? AND scope_id=?", (item_id, scope_id)).fetchone()
        if row is None or row["agent_id"] != actor:
            raise PermissionError("only the author may change this item")
        if row["status"] != "active" or row["expires"] <= time.time():
            raise WorkingError("item no longer active")
        if row["version"] != version:
            raise WorkingError("stale version")
        payload = json.loads(row["payload"])
        if not withdraw:
            if not isinstance(correction, str) or not correction.strip() or len(correction.encode()) > MAX_CLAIM_BYTES:
                raise WorkingError("valid correction required")
            payload["claim"] = correction.strip()
            payload["uncertainty"] = "unverified"
        new_version = version + 1
        status = "withdrawn" if withdraw else "active"
        conn.execute("UPDATE items SET version=?,status=?,payload=?,updated=? WHERE id=?",
                     (new_version, status, json.dumps(payload), time.time(), item_id))
        _event(conn, scope_id, item_id, new_version, "removed" if withdraw else "changed")
        return _view(conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone())


def expire(db, scope_id, session_id, actor):
    with _open(db) as conn:
        _scope(conn, db, scope_id, session_id, actor, "write")
        _prune(conn, scope_id)
        rows = conn.execute("SELECT id,version FROM items WHERE scope_id=? AND status='active' AND expires<=?",
                            (scope_id, time.time())).fetchall()
        for row in rows:
            conn.execute("UPDATE items SET status='expired',version=version+1 WHERE id=?", (row["id"],))
            _event(conn, scope_id, row["id"], row["version"] + 1, "removed")
        return len(rows)


def close_scope(db, scope_id, session_id, actor):
    with _open(db) as conn:
        _, role = _scope(conn, db, scope_id, session_id, actor, "write")
        if role != "owner":
            raise PermissionError("shared-task owner required")
        rows = conn.execute("SELECT id,version FROM items WHERE scope_id=? AND status='active'",
                            (scope_id,)).fetchall()
        for row in rows:
            conn.execute("UPDATE items SET status='expired',version=version+1 WHERE id=?", (row["id"],))
            _event(conn, scope_id, row["id"], row["version"] + 1, "removed")
        _prune(conn, scope_id)
        conn.execute("UPDATE scopes SET status='closed' WHERE id=?", (scope_id,))
        return {"scope_id": scope_id, "status": "closed", "expired": len(rows)}
