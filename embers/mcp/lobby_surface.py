"""One MCP tool: ember_lobby. Ephemeral board. session_id auth."""

from __future__ import annotations

from ..core.evidence import Evidence
from ..core.failure import Failure
from ..core.proposal import MemoryProposal
from ..core.types import SourceType
from ..lobby.store import LobbyError, LobbyStore
from . import server

_INSTALLED = False
STORE = LobbyStore()

_TOOL = {
    "name": "ember_lobby",
    "description": (
        "On-demand shared-task board. Off by default. "
        "action: open | publish | corroborate | board | promote | close. "
        "Pass session_id. room must be project or task. "
        "Not memory. Personal content is rejected."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "action": {"type": "string"},
            "session_id": {"type": "string"},
            "room": {"type": "string"},
            "task": {"type": "string"},
            "namespace": {"type": "string"},
            "type": {"type": "string"},
            "body": {"type": "string"},
            "approach": {"type": "string"},
            "post_id": {"type": "string"},
            "agent_id": {"type": "string"},
            "token": {"type": "string"},
        },
        "required": ["action"],
    },
}


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    if not any(t.get("name") == "ember_lobby" for t in server.TOOLS):
        server.TOOLS.append(_TOOL)
    orig = server.EmberMCP._call

    def _call(self, name: str, args: dict):
        if name == "ember_get_session":
            result = orig(self, name, args)
            return _annotate_session(self, args, result)
        if name != "ember_lobby":
            return orig(self, name, args)
        try:
            agent = self._auth(args)
        except PermissionError as e:
            return server._err(str(e))
        try:
            return server._text(_dispatch(self, agent, args or {}))
        except LobbyError as e:
            return server._err(str(e))
        except PermissionError as e:
            return server._err(str(e))

    server.EmberMCP._call = _call
    _INSTALLED = True


def _annotate_session(mcp, args: dict, result: dict) -> dict:
    if not isinstance(result, dict) or result.get("isError"):
        return result
    try:
        import json
        body = json.loads(result["content"][0]["text"])
        sid = args.get("session_id") or body.get("session_id")
        body["board"] = STORE.summary_for_session(sid)
        result["content"][0]["text"] = json.dumps(body, default=str)
    except Exception:
        return result
    return result


def _dispatch(mcp, agent, args: dict) -> dict:
    action = (args.get("action") or "").strip().lower()
    sid = args.get("session_id")
    if not sid:
        raise PermissionError("Pass session_id, or agent_id and token on ember_start_session.")
    if action == "open":
        return STORE.open(
            session_id=sid,
            agent_id=agent.agent_id,
            task=args.get("task", ""),
            namespace=args.get("namespace") or getattr(mcp.protocol, "namespace", "default"),
            room=args.get("room", ""),
        )
    if action == "publish":
        return STORE.publish(
            session_id=sid,
            agent_id=agent.agent_id,
            room=args.get("room", ""),
            post_type=args.get("type", ""),
            body=args.get("body", ""),
            approach=args.get("approach"),
        )
    if action == "corroborate":
        return STORE.corroborate(
            session_id=sid,
            agent_id=agent.agent_id,
            post_id=args.get("post_id", ""),
        )
    if action == "board":
        return STORE.snapshot(session_id=sid)
    if action == "promote":
        return _promote(mcp, agent, sid, args.get("post_id", ""))
    if action == "close":
        return _close(mcp, sid)
    raise LobbyError("action must be open | publish | corroborate | board | promote | close")


def _promote(mcp, agent, session_id: str, post_id: str) -> dict:
    post = STORE.take_post(session_id=session_id, post_id=post_id)
    evidence = []
    author = Evidence(
        source="lobby-post",
        source_type=SourceType.DIRECTLY_OBSERVED,
        reference=post["post_id"],
        description=post["body"][:200],
        agent_id=post["agent_id"],
        session_id=post["session_id"],
    )
    author.seal()
    evidence.append(author)
    for row in post["corroborations"]:
        ev = Evidence(
            source="lobby-corroboration",
            source_type=SourceType.DIRECTLY_OBSERVED,
            reference=post["post_id"],
            description=f"corroborated: {post['body'][:160]}",
            agent_id=row["agent_id"],
            session_id=row["session_id"],
        )
        ev.seal()
        evidence.append(ev)
    board = STORE.board_for_session(session_id)
    ns = board["namespace"] if board else "default"
    if post["type"] == "failure":
        failure = Failure(
            namespace=ns,
            approach=post.get("approach") or post["body"][:120],
            failed=post["body"],
            cause="",
            agent_id=post["agent_id"],
            session_id=post["session_id"],
            evidence=list(evidence),
        )
        fid = mcp.db.report_failure(failure)
        STORE.mark_promoted(post, fid)
        return {"failure_id": fid, "post_id": post["post_id"], "kind": "failure"}
    proposal = MemoryProposal(
        namespace=ns,
        discovery={"content": post["body"], "room": post["room"],
                   "lobby_post_id": post["post_id"], "lobby_type": post["type"]},
        reason="Promoted from lobby post after review.",
        evidence=evidence,
        confidence=0.6 if post["corroborations"] else 0.5,
        written_by=agent.agent_id,
        agent_id=agent.agent_id,
        session_id=session_id,
    )
    pid = mcp.db.propose(proposal)
    STORE.mark_promoted(post, pid)
    return {
        "proposal_id": pid,
        "post_id": post["post_id"],
        "kind": "proposal",
        "corroborators": len(post["corroborations"]),
    }


def _close(mcp, session_id: str) -> dict:
    board = STORE.board_for_session(session_id)
    ns = board["namespace"] if board else "default"
    closed = STORE.close(session_id=session_id)
    flushed = []
    for post in closed["unpromoted_failures"]:
        failure = Failure(
            namespace=ns,
            approach=post.get("approach") or post["body"][:120],
            failed=post["body"],
            cause="lobby-close",
            agent_id=post["agent_id"],
            session_id=post["session_id"],
        )
        fid = mcp.db.report_failure(failure)
        flushed.append({"post_id": post["post_id"], "failure_id": fid})
    return {
        "board_id": closed["board_id"],
        "status": "closed",
        "flushed_failures": flushed,
    }
