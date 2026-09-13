"""Feature 29: session is the bridge from live work to durable Ember."""

from __future__ import annotations

import json

from . import server
from .lobby_surface import STORE

_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    orig = server.EmberMCP._call

    def _call(self, name: str, args: dict):
        result = orig(self, name, args)
        if isinstance(result, dict) and result.get("isError"):
            return result
        sid = (args or {}).get("session_id")
        agent_id = (args or {}).get("agent_id")
        try:
            if name == "ember_propose_memory" and sid:
                body = json.loads(result["content"][0]["text"])
                _link(self.db, sid, "discovery", body.get("proposal_id"), agent_id)
            elif name == "ember_report_failure" and sid:
                body = json.loads(result["content"][0]["text"])
                _link(self.db, sid, "failure", body.get("failure_id"), agent_id)
            elif name == "ember_lobby":
                _link_lobby(self.db, sid, args or {}, result, agent_id)
            elif name == "ember_get_session":
                return _work_view(self, args or {}, result)
        except Exception:
            return result
        return result

    server.EmberMCP._call = _call
    _INSTALLED = True


def _link(db, session_id, kind, rid, agent_id) -> None:
    if not session_id or not rid or db.get_session(session_id) is None:
        return
    fn = {
        "discovery": db.record_discovery,
        "failure": db.record_failure,
        "memory": db.record_memory_write,
    }.get(kind)
    if fn is None:
        return
    try:
        fn(session_id, rid, changed_by=agent_id or "system")
    except TypeError:
        fn(session_id, rid)


def _link_lobby(db, session_id, args, result, agent_id) -> None:
    action = (args.get("action") or "").strip().lower()
    body = json.loads(result["content"][0]["text"])
    if action == "promote":
        if body.get("proposal_id"):
            _link(db, session_id, "discovery", body["proposal_id"], agent_id)
        if body.get("failure_id"):
            _link(db, session_id, "failure", body["failure_id"], agent_id)
    elif action == "close":
        for row in body.get("flushed_failures") or []:
            _link(db, session_id, "failure", row.get("failure_id"), agent_id)


def _work_view(mcp, args, result) -> dict:
    body = json.loads(result["content"][0]["text"])
    sid = args.get("session_id") or body.get("session_id")
    session = mcp.db.get_session(sid) if sid else None
    body["board"] = STORE.summary_for_session(sid)
    live = []
    board = STORE.board_for_session(sid)
    if board:
        live = [{"post_id": p["post_id"], "type": p["type"],
                 "body": p["body"], "promoted_to": p.get("promoted_to")}
                for p in board["posts"]]
    feedback_ids = []
    if sid:
        for r in mcp.db.get_by_session(sid):
            rt = getattr(r, "record_type", None)
            if rt is not None and getattr(rt, "value", rt) == "feedback":
                feedback_ids.append(r.id)
    body["work"] = {
        "task": body.get("task") or (session.task if session else ""),
        "agent_id": body.get("agent_id") or (session.agent_id if session else None),
        "memories": list(session.memory_writes) if session else [],
        "discoveries": list(session.discoveries) if session else [],
        "failures": list(session.failures) if session else [],
        "board": body["board"],
        "lobby_posts": live,
        "feedback": feedback_ids,
    }
    result["content"][0]["text"] = json.dumps(body, default=str)
    return result
