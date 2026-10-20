"""Restart-resilient provisional Lobby state, separate from Ember records."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from ..core.types import SessionStatus

TTL_SECONDS = 7 * 86400
MAX_ITEMS_PER_SCOPE = 1000
MAX_EVENTS_PER_SCOPE = 2000
MAX_CLAIM_BYTES = 4096
MAX_EVIDENCE = 16
MAX_ARTIFACT_BYTES = 16384
MAX_ARTIFACTS_PER_SCOPE = 2000
MAX_OBSERVED_CONTRIBUTIONS = 16
MAX_ARTIFACT_BYTES_PER_SCOPE = 32 * 1024 * 1024
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
    p.chmod(0o600)
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
    CREATE TABLE IF NOT EXISTS handoffs (
      item_id TEXT NOT NULL, version INTEGER NOT NULL, durable_id TEXT NOT NULL,
      durable_kind TEXT NOT NULL, accepted REAL NOT NULL,
      PRIMARY KEY(item_id,version));
    CREATE TABLE IF NOT EXISTS handoff_intents (
      item_id TEXT PRIMARY KEY, scope_id TEXT NOT NULL, version INTEGER NOT NULL,
      durable_id TEXT NOT NULL, durable_kind TEXT NOT NULL, payload TEXT NOT NULL,
      agent_id TEXT NOT NULL, session_id TEXT NOT NULL, created REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS artifacts (
      id TEXT PRIMARY KEY, scope_id TEXT NOT NULL, agent_id TEXT NOT NULL,
      content TEXT NOT NULL, digest TEXT NOT NULL, created REAL NOT NULL,
      expires REAL NOT NULL, submission_key TEXT, retained INTEGER NOT NULL DEFAULT 0);
    """)
    if "floor_seq" not in {row[1] for row in conn.execute("PRAGMA table_info(scopes)")}:
        conn.execute("ALTER TABLE scopes ADD COLUMN floor_seq INTEGER NOT NULL DEFAULT 0")
    if "submission_key" not in {row[1] for row in conn.execute("PRAGMA table_info(artifacts)")}:
        conn.execute("ALTER TABLE artifacts ADD COLUMN submission_key TEXT")
    if "retained" not in {row[1] for row in conn.execute("PRAGMA table_info(artifacts)")}:
        conn.execute("ALTER TABLE artifacts ADD COLUMN retained INTEGER NOT NULL DEFAULT 0")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS artifacts_key ON artifacts(scope_id,agent_id,submission_key)")
    conn.commit()
    before = conn.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]
    try:
        with conn:
            yield conn
        scopes = [row[0] for row in conn.execute(
            "SELECT DISTINCT scope_id FROM events WHERE seq>?", (before,))]
        if scopes:
            from .realtime_v2 import committed, wake
            for scope_id in scopes:
                wake(db, scope_id)
            committed(db)
    finally:
        conn.close()


def _session(db, session_id, actor):
    session = db.get_session(session_id)
    if session is None or session.status != SessionStatus.ACTIVE or session.agent_id != actor:
        raise PermissionError("active owned session required")
    return session


def _scope(conn, db, scope_id, session_id, actor, operation, *, allow_closed=False):
    session = _session(db, session_id, actor)
    scope = conn.execute("SELECT * FROM scopes WHERE id=?", (scope_id,)).fetchone()
    if scope is None or (scope["status"] != "open" and not allow_closed):
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
    if re.fullmatch(r"lobby-artifact:[0-9a-f-]{36}", value):
        return value
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
        try:
            datetime.fromisoformat(observed.replace("Z", "+00:00"))
        except ValueError as exc:
            raise WorkingError("observed_at must be ISO timestamp") from exc
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


@contextmanager
def _read_open(db):
    """Observe an existing Lobby file without schema setup, pruning or commits."""
    p = path(db)
    if not p.is_file():
        raise WorkingError("shared task unavailable")
    conn = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    # One consistent read snapshot: rows and cursor must describe the same commit.
    conn.execute("BEGIN")
    try:
        yield conn
    finally:
        conn.close()


def observation(db, scope_id, session_id, actor, *, after=0, limit=100):
    """Read-only scoped projection for a delegated Observatory capability."""
    if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise WorkingError("invalid event cursor or limit")
    with _read_open(db) as conn:
        scope, _ = _scope(conn, db, scope_id, session_id, actor, "read", allow_closed=True)
        floor = scope["floor_seq"]
        last = conn.execute("SELECT COALESCE(MAX(seq),0) FROM events WHERE scope_id=?", (scope_id,)).fetchone()[0]
        if after < floor or after > last or (after and after != floor and not conn.execute(
                "SELECT 1 FROM events WHERE scope_id=? AND seq=?", (scope_id, after)).fetchone()):
            return {"reset": True, "reason": "missed_history" if after < floor else "invalid_cursor",
                    "events": [], "cursor": last}
        rows = conn.execute("SELECT seq,item_id,version,kind,created FROM events WHERE scope_id=? AND seq>? ORDER BY seq LIMIT ?",
                            (scope_id, after, limit)).fetchall()
        changes = [{"cursor": r["seq"], "item_id": r["item_id"], "version": r["version"],
                    "change": r["kind"], "at": r["created"]} for r in rows]
        return {"reset": False, "events": changes,
                "cursor": changes[-1]["cursor"] if changes else after}


def observation_snapshot(db, scope_id, session_id, actor):
    """Bounded Lobby status view; no artifact content or evidence references."""
    with _read_open(db) as conn:
        scope, _ = _scope(conn, db, scope_id, session_id, actor, "read", allow_closed=True)
        members = [r["agent_id"] for r in conn.execute(
            "SELECT agent_id FROM members WHERE scope_id=? ORDER BY agent_id", (scope_id,))]
        rows = conn.execute("SELECT * FROM items WHERE scope_id=? ORDER BY updated DESC LIMIT ?",
                            (scope_id, MAX_ITEMS_PER_SCOPE)).fetchall()
        handoffs = {r["item_id"]: {"durable_id": r["durable_id"], "durable_kind": r["durable_kind"],
                                     "accepted_at": r["accepted"]} for r in conn.execute(
            "SELECT item_id,durable_id,durable_kind,accepted FROM handoffs WHERE item_id IN "
            "(SELECT id FROM items WHERE scope_id=?)", (scope_id,))}
        pending = {r["item_id"] for r in conn.execute(
            "SELECT item_id FROM handoff_intents WHERE scope_id=?", (scope_id,))}
        items = []
        now = time.time()
        def evidence_summary(ref):
            # No URLs, artifact bodies, execution context, or bearer session IDs.
            result = {k: ref.get(k) for k in
                      ("evidence_id", "source", "observed_at", "agent_id", "availability")}
            reference = ref.get("reference", "")
            if reference.startswith("lobby-artifact:"):
                artifact = conn.execute("SELECT expires FROM artifacts WHERE id=? AND scope_id=?",
                    (reference.split(":", 1)[1], scope_id)).fetchone()
                result["availability"] = "lobby" if artifact and artifact["expires"] > now else "unavailable"
            return result
        for row in rows:
            payload = json.loads(row["payload"])
            status = "expired" if row["status"] == "active" and row["expires"] <= now else row["status"]
            items.append({"item_id": row["id"], "scope_id": scope_id, "agent_id": row["agent_id"],
                          "kind": payload["kind"], "claim": payload["claim"] if status == "active" else None,
                          "uncertainty": payload["uncertainty"], "status": status, "version": row["version"],
                          "uncertainty_note": payload.get("uncertainty_note") if status == "active" else None,
                          "evidence_status": payload.get("evidence_status", "unavailable"),
                          "evidence": [evidence_summary(e) for e in payload.get("evidence_refs", [])],
                          "contributions": [{"agent_id": c["agent_id"], "kind": c["kind"],
                              "created_at": c["created_at"],
                              "evidence": [evidence_summary(e) for e in c.get("evidence_refs", [])]}
                              for c in payload.get("contributions", [])[-MAX_OBSERVED_CONTRIBUTIONS:]],
                          "contributions_truncated": len(payload.get("contributions", [])) > MAX_OBSERVED_CONTRIBUTIONS,
                          "created_at": row["created"], "updated_at": row["updated"],
                          "expires_at": row["expires"],
                          "evidence_count": len(payload.get("evidence_refs", [])) + sum(
                              len(c.get("evidence_refs", [])) for c in payload.get("contributions", [])),
                          "contribution_count": len(payload.get("contributions", [])),
                          "handoff": handoffs.get(row["id"]),
                          "handoff_pending": row["id"] in pending})
        cursor = conn.execute("SELECT COALESCE(MAX(seq),0) FROM events WHERE scope_id=?",
                              (scope_id,)).fetchone()[0]
        return {"scope": {"scope_id": scope_id, "namespace": scope["namespace"],
                          "task": scope["task"], "status": scope["status"], "created_at": scope["created"],
                          "members": members}, "items": items, "cursor": cursor,
                "observed_at": now, "item_limit": MAX_ITEMS_PER_SCOPE}


def _prune(conn, scope_id):
    # An expired key remains retryable through one additional TTL, then leaves
    # provisional storage. Durable handoff receipts will be separate in Batch 3.
    now = time.time()
    expired = conn.execute("SELECT id,version FROM items WHERE scope_id=? AND status='active' AND expires<=?",
                           (scope_id, now)).fetchall()
    for row in expired:
        conn.execute("UPDATE items SET status='expired',version=version+1 WHERE id=?", (row["id"],))
        _event(conn, scope_id, row["id"], row["version"] + 1, "removed")
    conn.execute("""DELETE FROM items WHERE scope_id=? AND expires<? AND status!='active'
      AND id NOT IN (SELECT item_id FROM handoffs)
      AND id NOT IN (SELECT item_id FROM handoff_intents)""",
                 (scope_id, now - TTL_SECONDS))
    conn.execute("DELETE FROM artifacts WHERE scope_id=? AND expires<? AND retained=0",
                 (scope_id, now))
    return len(expired)


def _view(row, *, evidence=False):
    payload = json.loads(row["payload"])
    payload.pop("_submission_digest", None)
    if evidence:
        payload["evidence_refs"] = [{**e, "session_id": "session-sha256:" + hashlib.sha256(e["session_id"].encode()).hexdigest()}
                                    for e in payload["evidence_refs"]]
    else:
        payload["evidence_refs"] = [{k: e[k] for k in ("evidence_id", "source", "observed_at", "agent_id", "availability")}
                                    for e in payload["evidence_refs"]]
    for contribution in payload.get("contributions", []):
        contribution["session_id"] = "session-sha256:" + hashlib.sha256(
            contribution["session_id"].encode()).hexdigest()
        contribution.pop("digest", None)
        contribution.pop("client_contribution_id", None)
        if evidence:
            contribution["evidence_refs"] = [
                {**e, "session_id": "session-sha256:" + hashlib.sha256(e["session_id"].encode()).hexdigest()}
                for e in contribution["evidence_refs"]]
        else:
            contribution["evidence_refs"] = [
                {k: e[k] for k in ("evidence_id", "source", "observed_at", "agent_id", "availability")}
                for e in contribution["evidence_refs"]]
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
        for evidence in payload["evidence_refs"]:
            reference = evidence["reference"]
            if reference.startswith("lobby-artifact:"):
                artifact = conn.execute("SELECT * FROM artifacts WHERE id=? AND scope_id=? AND expires>?",
                                        (reference.split(":", 1)[1], scope_id, time.time())).fetchone()
                if artifact is None or artifact["agent_id"] != actor:
                    raise WorkingError("artifact unavailable or not owned by submitter")
                evidence["content_digest"] = artifact["digest"]
                evidence["availability"] = "lobby"
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
        if conn.execute("SELECT 1 FROM handoff_intents WHERE item_id=?", (item_id,)).fetchone():
            raise WorkingError("handoff in progress; retry handoff first")
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
        return _prune(conn, scope_id)


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
        _event(conn, scope_id, "", 0, "scope_closed")
        return {"scope_id": scope_id, "status": "closed", "expired": len(rows)}


def contribute(db, scope_id, session_id, actor, item_id, data):
    key = data.get("client_contribution_id")
    kind = data.get("kind")
    if not isinstance(key, str) or not 1 <= len(key) <= 128 or kind not in {"support", "contradiction"}:
        raise WorkingError("contribution id and support/contradiction kind required")
    note = data.get("note")
    if not isinstance(note, str) or not note.strip() or len(note.encode()) > MAX_CLAIM_BYTES:
        raise WorkingError("valid contribution note required")
    clean = _payload({"share": True, "kind": "finding", "claim": note,
                      "evidence_refs": data.get("evidence_refs", [])}, actor, session_id)
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()
    with _open(db) as conn:
        _scope(conn, db, scope_id, session_id, actor, "write")
        if conn.execute("SELECT 1 FROM handoff_intents WHERE item_id=?", (item_id,)).fetchone():
            raise WorkingError("handoff in progress; retry handoff first")
        row = conn.execute("SELECT * FROM items WHERE id=? AND scope_id=?", (item_id, scope_id)).fetchone()
        if row is None or row["status"] != "active" or row["expires"] <= time.time():
            raise WorkingError("item no longer active")
        if row["agent_id"] == actor:
            raise WorkingError("author must correct their own item")
        payload = json.loads(row["payload"])
        for existing in payload["contributions"]:
            if existing["agent_id"] == actor and existing["client_contribution_id"] == key:
                if existing["digest"] != digest:
                    raise WorkingError("contribution key reused with different content")
                return _view(row)
        payload["contributions"].append({"contribution_id": str(uuid.uuid4()),
          "client_contribution_id": key, "digest": digest, "kind": kind,
          "note": note.strip(), "agent_id": actor, "session_id": session_id,
          "evidence_refs": clean["evidence_refs"], "created_at": time.time()})
        version = row["version"] + 1
        conn.execute("UPDATE items SET version=?,payload=?,updated=? WHERE id=?",
                     (version, json.dumps(payload), time.time(), item_id))
        _event(conn, scope_id, item_id, version, "changed")
        return _view(conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone())


def handoff(db, scope_id, session_id, actor, item_id):
    """Freeze one version and reuse its deterministic durable ID on retry."""
    from ..core.evidence import Evidence
    from ..core.failure import Failure
    from ..core.proposal import MemoryProposal
    from ..core.types import SourceType

    # Commit the intent and pin referenced artifacts BEFORE the durable write.
    # A crash after the Ember write can then be reconciled by the same ID even
    # after ordinary provisional retention has passed.
    with _open(db) as conn:
        conn.execute("BEGIN IMMEDIATE")
        _scope(conn, db, scope_id, session_id, actor, "write", allow_closed=True)
        row = conn.execute("SELECT * FROM items WHERE id=? AND scope_id=?", (item_id, scope_id)).fetchone()
        if row is None or row["agent_id"] != actor:
            raise PermissionError("author must request durable handoff")
        receipt = conn.execute("SELECT * FROM handoffs WHERE item_id=? ORDER BY version DESC LIMIT 1",
                               (item_id,)).fetchone()
        if receipt:
            return {"item_id": item_id, "version": receipt["version"],
                    "durable_id": receipt["durable_id"], "kind": receipt["durable_kind"]}
        intent = conn.execute("SELECT * FROM handoff_intents WHERE item_id=?", (item_id,)).fetchone()
        if intent is None:
            if row["status"] != "active" or row["expires"] <= time.time():
                raise WorkingError("item no longer active")
            payload = json.loads(row["payload"])
            version = row["version"]
            durable_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"ember-lobby-v2:{item_id}:{version}"))
            kind = "failure" if payload["kind"] == "failure" else "proposal"
            conn.execute("INSERT INTO handoff_intents VALUES(?,?,?,?,?,?,?,?,?)",
                         (item_id, scope_id, version, durable_id, kind, json.dumps(payload),
                          actor, session_id, time.time()))
            for ref in payload["evidence_refs"] + [e for c in payload["contributions"] for e in c["evidence_refs"]]:
                if ref["reference"].startswith("lobby-artifact:"):
                    conn.execute("UPDATE artifacts SET retained=1 WHERE id=? AND scope_id=?",
                                 (ref["reference"].split(":", 1)[1], scope_id))

    with _open(db) as conn:
        conn.execute("BEGIN IMMEDIATE")
        scope, _ = _scope(conn, db, scope_id, session_id, actor, "write", allow_closed=True)
        intent = conn.execute("SELECT * FROM handoff_intents WHERE item_id=?", (item_id,)).fetchone()
        receipt = conn.execute("SELECT * FROM handoffs WHERE item_id=? ORDER BY version DESC LIMIT 1",
                               (item_id,)).fetchone()
        if receipt:
            return {"item_id": item_id, "version": receipt["version"],
                    "durable_id": receipt["durable_id"], "kind": receipt["durable_kind"]}
        if intent is None or intent["agent_id"] != actor:
            raise WorkingError("handoff intent missing")
        payload = json.loads(intent["payload"])
        version, durable_id, kind = intent["version"], intent["durable_id"], intent["durable_kind"]
        source_session_ref = "session-sha256:" + hashlib.sha256(
            intent["session_id"].encode()).hexdigest()
        evidence = []
        for ref in payload["evidence_refs"] + [e for c in payload["contributions"] for e in c["evidence_refs"]]:
            ev = Evidence(evidence_id=ref["evidence_id"], source=ref["source"],
                          source_type=SourceType.REPORTED, reference=ref["reference"],
                          description="Lobby V2 attributed observation; artifact digest: " +
                                      ref.get("content_digest", "external-unverified"),
                          observed_at=datetime.fromisoformat(ref["observed_at"].replace("Z", "+00:00")),
                          agent_id=ref["agent_id"],
                          session_id="session-sha256:" + hashlib.sha256(ref["session_id"].encode()).hexdigest())
            ev.seal()
            evidence.append(ev)
        if kind == "failure":
            if db.get_failure(durable_id) is None:
                failure = Failure(failure_id=durable_id, namespace=scope["namespace"],
                                  approach=payload["approach"] or payload["claim"][:120],
                                  failed=payload["claim"], cause="Lobby V2 provisional report; claim unverified",
                                  agent_id=actor, session_id=None, evidence=evidence,
                                  context={"lobby_item_id": item_id, "version": version,
                                           "uncertainty": payload["uncertainty"],
                                           "source_session_ref": source_session_ref})
                db.report_failure(failure)
            session = db.get_session(session_id)
            if session and durable_id not in session.failures:
                db.record_failure(session_id, durable_id, changed_by=actor)
        else:
            if db.get_proposal(durable_id) is None:
                proposal = MemoryProposal(proposal_id=durable_id, namespace=scope["namespace"],
                    discovery={"content": payload["claim"], "lobby_item_id": item_id,
                               "lobby_version": version, "uncertainty": payload["uncertainty"],
                               "source_session_ref": source_session_ref,
                               "contributions": [{"kind": c["kind"], "note": c["note"],
                                                  "agent_id": c["agent_id"]}
                                                 for c in payload["contributions"]]},
                    reason="Explicit Lobby V2 durable handoff; provisional interpretation",
                    evidence=evidence, confidence=0.5, written_by=actor,
                    agent_id=actor, session_id=None)
                db.propose(proposal)
            session = db.get_session(session_id)
            if session and durable_id not in session.discoveries:
                db.record_discovery(session_id, durable_id, changed_by=actor)
        conn.execute("INSERT INTO handoffs VALUES(?,?,?,?,?)",
                     (item_id, version, durable_id, kind, time.time()))
        conn.execute("DELETE FROM handoff_intents WHERE item_id=?", (item_id,))
        return {"item_id": item_id, "version": version, "durable_id": durable_id,
                "kind": kind}


def upload_artifact(db, scope_id, session_id, actor, *, content, safe_to_share=False,
                    redacted=False, client_artifact_id=None):
    if safe_to_share is not True or redacted is not True:
        raise WorkingError("explicit safe_to_share and redacted required")
    if not isinstance(content, str) or not content or len(content.encode("utf-8")) > MAX_ARTIFACT_BYTES:
        raise WorkingError("invalid or oversized artifact")
    if client_artifact_id is not None and (not isinstance(client_artifact_id, str) or not 1 <= len(client_artifact_id) <= 128):
        raise WorkingError("invalid client_artifact_id")
    artifact_id = str(uuid.uuid4())
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    now = time.time()
    with _open(db) as conn:
        _scope(conn, db, scope_id, session_id, actor, "write")
        _prune(conn, scope_id)
        old = conn.execute("SELECT * FROM artifacts WHERE scope_id=? AND agent_id=? AND submission_key=?",
                           (scope_id, actor, client_artifact_id)).fetchone() if client_artifact_id else None
        if old:
            if old["digest"] != digest:
                raise WorkingError("artifact key reused with different content")
            return {"reference": "lobby-artifact:" + old["id"], "sha256": old["digest"],
                    "expires_at": old["expires"],
                    "observed_at": datetime.fromtimestamp(old["created"], timezone.utc).isoformat()}
        usage = conn.execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(CAST(content AS BLOB))),0) FROM artifacts WHERE scope_id=?",
                             (scope_id,)).fetchone()
        if usage[0] >= MAX_ARTIFACTS_PER_SCOPE or usage[1] + len(content.encode("utf-8")) > MAX_ARTIFACT_BYTES_PER_SCOPE:
            raise WorkingError("shared-task artifact capacity reached")
        conn.execute("INSERT INTO artifacts(id,scope_id,agent_id,content,digest,created,expires,submission_key) VALUES(?,?,?,?,?,?,?,?)",
                     (artifact_id, scope_id, actor, content, digest, now, now + TTL_SECONDS,
                      client_artifact_id))
    return {"reference": "lobby-artifact:" + artifact_id, "sha256": digest,
            "expires_at": now + TTL_SECONDS,
            "observed_at": datetime.fromtimestamp(now, timezone.utc).isoformat()}


def get_artifact(db, scope_id, session_id, actor, artifact_id):
    with _open(db) as conn:
        scope, _ = _scope(conn, db, scope_id, session_id, actor, "read", allow_closed=True)
        row = conn.execute("SELECT * FROM artifacts WHERE id=? AND scope_id=? AND (expires>? OR retained=1)",
                           (artifact_id, scope_id, time.time())).fetchone()
        if row is None or (scope["status"] != "open" and not row["retained"]):
            raise WorkingError("artifact unavailable")
        if hashlib.sha256(row["content"].encode("utf-8")).hexdigest() != row["digest"]:
            raise WorkingError("artifact integrity failure")
        return {"reference": "lobby-artifact:" + row["id"], "sha256": row["digest"],
                "content": row["content"], "created_at": row["created"]}
