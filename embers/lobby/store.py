"""In-memory lobby. Outside the WAL. Allowed to forget."""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

ALLOWED_ROOMS = {"project", "task"}
ALLOWED_TYPES = {"failure", "discovery", "question"}


class LobbyError(ValueError):
    pass


class LobbyStore:
    def __init__(self):
        self.boards: dict[str, dict] = {}
        self.by_session: dict[str, str] = {}

    def reset(self) -> None:
        self.boards.clear()
        self.by_session.clear()

    def board_for_session(self, session_id: str | None) -> dict | None:
        if not session_id:
            return None
        bid = self.by_session.get(session_id)
        if not bid:
            return None
        board = self.boards.get(bid)
        if board is None or board["status"] != "open":
            return None
        return board

    def summary_for_session(self, session_id: str | None) -> dict | None:
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

    def open(self, *, session_id: str, agent_id: str, task: str,
             namespace: str, room: str) -> dict:
        room = (room or "").strip().lower()
        if room not in ALLOWED_ROOMS:
            raise LobbyError("room must be project or task")
        task = (task or "").strip()
        if not task:
            raise LobbyError("task required")
        existing = self.board_for_session(session_id)
        if existing is not None:
            return self._public(existing)
        for board in self.boards.values():
            if (board["status"] == "open"
                    and board["task"] == task
                    and board["namespace"] == (namespace or "default")
                    and board["room"] == room):
                board["participants"].add(session_id)
                self.by_session[session_id] = board["board_id"]
                return self._public(board)
        board = {
            "board_id": str(uuid.uuid4()),
            "task": task,
            "namespace": namespace or "default",
            "room": room,
            "status": "open",
            "opened_by": {"agent_id": agent_id, "session_id": session_id},
            "participants": {session_id},
            "posts": [],
            "opened_at": datetime.now(timezone.utc).isoformat(),
        }
        self.boards[board["board_id"]] = board
        self.by_session[session_id] = board["board_id"]
        return self._public(board)

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
        board["participants"].add(session_id)
        self.by_session[session_id] = board["board_id"]
        return dict(post)

    def corroborate(self, *, session_id: str, agent_id: str, post_id: str) -> dict:
        board, post = self._find_post(session_id, post_id)
        if post["session_id"] == session_id or post["agent_id"] == agent_id:
            raise LobbyError("cannot corroborate your own post")
        for row in post["corroborations"]:
            if row["session_id"] == session_id or row["agent_id"] == agent_id:
                return dict(post)
        post["corroborations"].append({
            "agent_id": agent_id,
            "session_id": session_id,
            "at": datetime.now(timezone.utc).isoformat(),
        })
        board["participants"].add(session_id)
        self.by_session[session_id] = board["board_id"]
        return dict(post)

    def snapshot(self, *, session_id: str) -> dict:
        board = self.board_for_session(session_id)
        if board is None:
            raise LobbyError("no open board for this session")
        return self._public(board)

    def take_post(self, *, session_id: str, post_id: str) -> dict:
        _board, post = self._find_post(session_id, post_id)
        return post

    def mark_promoted(self, post: dict, proposal_id: str) -> None:
        post["promoted_to"] = proposal_id

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
        return {
            "board_id": board["board_id"],
            "status": "closed",
            "unpromoted_failures": unpromoted_failures,
        }

    def _find_post(self, session_id: str, post_id: str) -> tuple[dict, dict]:
        board = self.board_for_session(session_id)
        if board is None:
            raise LobbyError("no open board for this session")
        for post in board["posts"]:
            if post["post_id"] == post_id:
                return board, post
        raise LobbyError("post not found on this board")

    def _public(self, board: dict) -> dict:
        return {
            "board_id": board["board_id"],
            "task": board["task"],
            "namespace": board["namespace"],
            "room": board["room"],
            "status": board["status"],
            "opened_by": dict(board["opened_by"]),
            "participants": sorted(board["participants"]),
            "posts": [dict(p) for p in board["posts"]],
        }
