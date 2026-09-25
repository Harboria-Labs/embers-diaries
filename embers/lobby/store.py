"""In-memory lobby. Outside the WAL. Allowed to forget."""

from __future__ import annotations

from datetime import datetime, timezone
from functools import wraps
from threading import RLock
import uuid

from ..config import LobbyConfig
from .realtime import LobbyEventBroker

ALLOWED_ROOMS = {"project", "task"}
ALLOWED_TYPES = {"failure", "discovery", "question"}


def _locked(method):
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper



class LobbyError(ValueError):
    pass


class LobbyStore:
    def __init__(self, config: LobbyConfig | None = None):
        self._lock = RLock()
        self.config = config or LobbyConfig()
        self.boards: dict[str, dict] = {}
        self.by_session: dict[str, str] = {}
        self.events = LobbyEventBroker()

    @_locked
    def configure(self, config: LobbyConfig) -> None:
        """Apply process configuration without discarding active boards."""
        self.config = config

    @_locked
    def reset(self) -> None:
        self.boards.clear()
        self.by_session.clear()
        self.events.reset()

    @_locked
    def board_for_session(self, session_id: str | None) -> dict | None:
        self._ensure_enabled()
        if not session_id:
            return None
        bid = self.by_session.get(session_id)
        if not bid:
            return None
        board = self.boards.get(bid)
        if board is None or board["status"] != "open":
            return None
        self._expire_posts(board)
        return board

    @_locked
    def summary_for_session(self, session_id: str | None) -> dict | None:
        if not self.config.enabled:
            return None
        board = self.board_for_session(session_id)
        if board is None:
            return None
        return {
            "board_id": board["board_id"],
            "task": board["task"],
            "namespace": board["namespace"],
            "room": board["room"],
            "status": board["status"],
            "posts": len(board["posts"]),
        }

    @_locked
    def open(self, *, session_id: str, agent_id: str, task: str,
             namespace: str, room: str) -> dict:
        self._ensure_enabled()
        room = (room or "").strip().lower()
        if room not in ALLOWED_ROOMS:
            raise LobbyError("room must be project or task")
        task = (task or "").strip()
        if not task:
            raise LobbyError("task required")
        existing = self.board_for_session(session_id)
        if existing is not None:
            self._touch(existing, session_id, agent_id)
            return self._public(existing)
        for board in self.boards.values():
            if (board["status"] == "open"
                    and board["task"] == task
                    and board["namespace"] == (namespace or "default")
                    and board["room"] == room):
                self._check_capacity(board, session_id)
                board["participants"].add(session_id)
                self.by_session[session_id] = board["board_id"]
                self._touch(board, session_id, agent_id)
                joined = self._public(board)
                self._emit(board, "lobby.participant_joined", {
                    "agent_id": agent_id, "session_id": session_id})
                return joined
        board = {
            "board_id": str(uuid.uuid4()),
            "task": task,
            "namespace": namespace or "default",
            "room": room,
            "status": "open",
            "opened_by": {"agent_id": agent_id, "session_id": session_id},
            "participants": {session_id},
            "posts": [],
            "presence": {},
            "opened_at": datetime.now(timezone.utc).isoformat(),
        }
        self._touch(board, session_id, agent_id)
        self.boards[board["board_id"]] = board
        self.by_session[session_id] = board["board_id"]
        opened = self._public(board)
        self._emit(board, "lobby.opened", {"board": opened})
        return opened

    @_locked
    def publish(self, *, session_id: str, agent_id: str, room: str,
                post_type: str, body: str, approach: str | None) -> dict:
        board = self.board_for_session(session_id)
        if board is None:
            raise LobbyError("no open board for this session")
        room = (room or "").strip().lower()
        if room not in ALLOWED_ROOMS:
            raise LobbyError("room must be project or task")
        if room != board["room"]:
            raise LobbyError("publish room must match the board room")
        post_type = (post_type or "").strip().lower()
        if post_type not in ALLOWED_TYPES:
            raise LobbyError("type must be failure | discovery | question")
        body = (body or "").strip()
        if not body:
            raise LobbyError("body required")
        body_size = len(body.encode("utf-8"))
        if (self.config.max_message_bytes
                and body_size > self.config.max_message_bytes):
            raise LobbyError(
                f"message is {body_size} bytes; lobby.max_message_bytes is "
                f"{self.config.max_message_bytes}")
        post = {
            "post_id": str(uuid.uuid4()),
            "board_id": board["board_id"],
            "session_id": session_id,
            "agent_id": agent_id,
            "room": room,
            "type": post_type,
            "body": body,
            "approach": (approach or "").strip() or None,
            "corroborations": [],
            "promoted_to": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        board["posts"].append(post)
        self._check_capacity(board, session_id)
        board["participants"].add(session_id)
        self.by_session[session_id] = board["board_id"]
        self._touch(board, session_id, agent_id)
        published = dict(post)
        self._emit(board, "lobby.post_published", {"post": published})
        return published

    @_locked
    def corroborate(self, *, session_id: str, agent_id: str, post_id: str) -> dict:
        board, post = self._find_post(session_id, post_id)
        if post["session_id"] == session_id or post["agent_id"] == agent_id:
            raise LobbyError("cannot corroborate your own post")
        for row in post["corroborations"]:
            if row["session_id"] == session_id or row["agent_id"] == agent_id:
                return dict(post)
        self._check_capacity(board, session_id)
        post["corroborations"].append({
            "agent_id": agent_id,
            "session_id": session_id,
            "at": datetime.now(timezone.utc).isoformat(),
        })
        board["participants"].add(session_id)
        self.by_session[session_id] = board["board_id"]
        self._touch(board, session_id, agent_id)
        corroborated = dict(post)
        self._emit(board, "lobby.post_corroborated", {
            "post": corroborated, "agent_id": agent_id,
            "session_id": session_id})
        return corroborated

    @_locked
    def heartbeat(self, *, session_id: str, agent_id: str) -> dict:
        """Refresh ephemeral presence; future real-time transport can call this."""
        board = self.board_for_session(session_id)
        if board is None:
            raise LobbyError("no open board for this session")
        self._touch(board, session_id, agent_id)
        presence = dict(board["presence"][session_id])
        self._emit(board, "lobby.presence_updated", {"presence": presence})
        return presence

    @_locked
    def leave(self, *, session_id: str) -> None:
        """Remove a session from presence without closing the shared board."""
        board = self.board_for_session(session_id)
        if board is None:
            return
        board["participants"].discard(session_id)
        board["presence"].pop(session_id, None)
        self.by_session.pop(session_id, None)
        self._emit(board, "lobby.participant_left", {"session_id": session_id})

    @_locked
    def snapshot(self, *, session_id: str) -> dict:
        board = self.board_for_session(session_id)
        if board is None:
            raise LobbyError("no open board for this session")
        return self._public(board)

    @_locked
    def take_post(self, *, session_id: str, post_id: str) -> dict:
        _board, post = self._find_post(session_id, post_id)
        return post

    @_locked
    def mark_promoted(self, post: dict, proposal_id: str) -> None:
        post["promoted_to"] = proposal_id
        board = self.boards.get(post["board_id"])
        if board is not None:
            self._emit(board, "lobby.post_promoted", {
                "post_id": post["post_id"], "promoted_to": proposal_id})

    @_locked
    def close(self, *, session_id: str) -> dict:
        board = self.board_for_session(session_id)
        if board is None:
            raise LobbyError("no open board for this session")
        if session_id not in board["participants"] and (
            board["opened_by"]["session_id"] != session_id
        ):
            raise LobbyError("only a participant can close this board")
        unpromoted_failures = [
            dict(p) for p in board["posts"]
            if p["type"] == "failure" and not p.get("promoted_to")
        ]
        board["status"] = "closed"
        for sid in list(board["participants"]):
            if self.by_session.get(sid) == board["board_id"]:
                del self.by_session[sid]
        result = {
            "board_id": board["board_id"],
            "status": "closed",
            "unpromoted_failures": unpromoted_failures,
        }
        self._emit(board, "lobby.closed", result)
        return result

    def _find_post(self, session_id: str, post_id: str) -> tuple[dict, dict]:
        board = self.board_for_session(session_id)
        if board is None:
            raise LobbyError("no open board for this session")
        for post in board["posts"]:
            if post["post_id"] == post_id:
                return board, post
        raise LobbyError("post not found on this board")

    def _ensure_enabled(self) -> None:
        if not self.config.enabled:
            raise LobbyError("lobby is disabled by configuration")

    def _check_capacity(self, board: dict, session_id: str) -> None:
        if (self.config.max_agents
                and session_id not in board["participants"]
                and len(board["participants"]) >= self.config.max_agents):
            raise LobbyError(
                f"lobby.max_agents limit of {self.config.max_agents} reached")

    def _touch(self, board: dict, session_id: str, agent_id: str) -> None:
        board["presence"][session_id] = {
            "agent_id": agent_id,
            "session_id": session_id,
            "last_seen": datetime.now(timezone.utc).isoformat(),
        }

    def _expire_posts(self, board: dict) -> None:
        ttl = self.config.message_ttl_seconds
        if not ttl:
            return
        now = datetime.now(timezone.utc)
        board["posts"] = [
            post for post in board["posts"]
            if (now - datetime.fromisoformat(post["created_at"])).total_seconds()
            <= ttl
        ]

    def _public(self, board: dict) -> dict:
        self._expire_posts(board)
        now = datetime.now(timezone.utc)
        presence = []
        for row in board["presence"].values():
            item = dict(row)
            age = (now - datetime.fromisoformat(item["last_seen"])).total_seconds()
            item["status"] = (
                "online" if age <= self.config.presence_timeout_seconds
                else "stale")
            presence.append(item)
        return {
            "board_id": board["board_id"],
            "task": board["task"],
            "namespace": board["namespace"],
            "room": board["room"],
            "status": board["status"],
            "opened_by": dict(board["opened_by"]),
            "participants": sorted(board["participants"]),
            "presence": sorted(presence, key=lambda item: item["session_id"]),
            "heartbeat_interval_seconds": self.config.heartbeat_interval_seconds,
            "posts": [dict(p) for p in board["posts"]],
        }

    def _emit(self, board: dict, event_type: str, data: dict) -> None:
        self.events.publish(board["board_id"], event_type, data)
