"""MCP surface for feedback, lifecycle, and on-demand maintenance."""

from __future__ import annotations

from ..core.feedback import Feedback, FeedbackOutcome, FeedbackAttribution
from ..maintenance import run_maintenance_cycle
from . import server
from .server import _text, _err

_INSTALLED = False

_TOOLS = [
    {
        "name": "ember_feedback",
        "description": (
            "Report what happened after using a durable memory. "
            "outcome required. attribution optional and always YOUR diagnosis. "
            "Ember does not change the memory. Pass session_id. "
            "Not valid on lobby posts or proposals."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string"},
                "outcome": {"type": "string"},
                "usefulness": {"type": "number"},
                "accuracy": {"type": "number"},
                "attribution": {"type": "string"},
                "note": {"type": "string"},
                "session_id": {"type": "string"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["memory_id", "outcome"],
        },
    },
    {
        "name": "ember_feedback_for",
        "description": "Every outcome report filed against a durable memory.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string"},
                "session_id": {"type": "string"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["memory_id"],
        },
    },
    {
        "name": "ember_lifecycle",
        "description": (
            "Compute a memory's lifecycle from live signals "
            "(decay, access, mapped conflict, deprecated). Not stored."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "record_id": {"type": "string"},
                "session_id": {"type": "string"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["record_id"],
        },
    },
    {
        "name": "ember_run_maintenance",
        "description": (
            "Run one reflect + consolidate pass now. "
            "The HTTP scheduler stays off unless "
            "EMBER_MAINTENANCE_INTERVAL_SECONDS is set."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "namespaces": {"type": "array", "items": {"type": "string"}},
                "session_id": {"type": "string"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": [],
        },
    },
]


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    names = {t["name"] for t in server.TOOLS}
    for tool in _TOOLS:
        if tool["name"] not in names:
            server.TOOLS.append(tool)
    orig = server.EmberMCP._call

    def _call(self, name: str, args: dict):
        if name == "ember_feedback":
            agent = self._auth(args)
            fb = Feedback(
                memory_id=args["memory_id"],
                agent_id=agent.agent_id,
                outcome=FeedbackOutcome(args["outcome"]),
                usefulness=args.get("usefulness"),
                accuracy=args.get("accuracy"),
                attribution=(FeedbackAttribution(args["attribution"])
                             if args.get("attribution") else None),
                note=args.get("note", ""),
                session_id=args.get("session_id"),
            )
            fid = self.db.give_feedback(args["memory_id"], fb)
            return _text({"feedback_id": fid})
        if name == "ember_feedback_for":
            self._auth(args)
            records = self.db.feedback_for(args["memory_id"])
            return _text([r.data for r in records])
        if name == "ember_lifecycle":
            self._auth(args)
            report = self.protocol.get_lifecycle(args["record_id"])
            if report is None:
                return _err(f"record {args['record_id']} not found")
            return _text(report.to_dict())
        if name == "ember_run_maintenance":
            self._auth(args)
            namespaces = args.get("namespaces") or [self.protocol.namespace]
            return _text(run_maintenance_cycle(self.protocol, namespaces))
        return orig(self, name, args)

    server.EmberMCP._call = _call
    _INSTALLED = True
