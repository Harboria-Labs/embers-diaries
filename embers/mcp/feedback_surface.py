"""MCP surface for feedback, lifecycle, and on-demand maintenance."""

from __future__ import annotations

from ..core.feedback import Feedback, FeedbackOutcome, FeedbackAttribution
from ..maintenance import run_maintenance_cycle
from . import server
from .server import _text, _err

_INSTALLED = False

_TOOLS = [
    {"name": "ember_candidate_recall",
     "description": "Experimental contextual recall using agent-scored memory IDs, LADC and learned directional pairs. Persists activation only; does not reinforce or verify memories. elapsed is explicit model time.",
     "inputSchema": {"type": "object", "properties": {
         "namespace": {"type": "string"}, "context_id": {"type": "string"},
         "query_id": {"type": "string"}, "direct_scores": {"type": "object", "additionalProperties": {"type": "number", "minimum": 0, "maximum": 1}},
         "elapsed": {"type": "number", "minimum": 0}, "format": {"type": "string", "enum": ["structured", "text", "messages"]},
         "agent_id": {"type": "string"}, "token": {"type": "string"}, "session_id": {"type": "string"}},
         "required": ["namespace", "context_id", "query_id", "direct_scores", "elapsed"]}},
    {
        "name": "ember_resolve_relevance",
        "description": "Resolve or correct a scoped outcome using a preconfigured resolver policy. Does not change truth. Requires request_id and expected_revision.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"}, "context_id": {"type": "string"},
                "decision": {"type": "object"}, "request_id": {"type": "string"},
                "expected_revision": {"type": "integer", "minimum": 0},
                "agent_id": {"type": "string"}, "token": {"type": "string"},
                "session_id": {"type": "string"},
            },
            "required": ["namespace", "context_id", "decision", "request_id", "expected_revision"],
        },
    },
    {
        "name": "ember_relevance_state",
        "description": "Read contextual learned memory biases, typed pair strengths and unresolved dependencies. No truth promotion.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"}, "context_id": {"type": "string"},
                "agent_id": {"type": "string"}, "token": {"type": "string"},
                "session_id": {"type": "string"},
            },
            "required": ["namespace", "context_id"],
        },
    },
    {
        "name": "ember_feedback",
        "description": (
            "Report what happened after using a durable memory. "
            "outcome required. attribution optional and always YOUR diagnosis. "
            "Ember stores reports only; it does not learn or verify from them yet. "
            "For explicit channels use schema_version=2, channel, outcome_id, "
            "context_id and context. Relevance needs signal; correctness needs supporting_refs. "
            "Pass session_id. "
            "Not valid on lobby posts or proposals."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string"},
                "outcome": {"type": "string"},
                "schema_version": {"type": "integer", "enum": [1, 2]},
                "channel": {"type": "string", "enum": ["relevance", "correctness"]},
                "outcome_id": {"type": "string"},
                "context_id": {"type": "string"},
                "context": {"type": "object"},
                "signal": {"type": "number", "minimum": -1, "maximum": 1},
                "supporting_refs": {"type": "array", "items": {"type": "string"}},
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
        if name == "ember_candidate_recall":
            from ..integration.feedback_service import candidate_recall
            agent = self._auth(args)
            return _text(candidate_recall(self.db, args["namespace"], args["context_id"], agent.agent_id,
                {key: args[key] for key in ("query_id", "direct_scores", "elapsed", "format") if key in args}))
        if name == "ember_resolve_relevance":
            from ..integration.feedback_service import resolve
            agent = self._auth(args)
            return _text(resolve(
                self.db, args["namespace"], args["context_id"], agent.agent_id,
                {key: args[key] for key in ("decision", "request_id", "expected_revision")},
            ))
        if name == "ember_relevance_state":
            from ..integration.feedback_service import projection
            agent = self._auth(args)
            return _text(projection(
                self.db, args["namespace"], args["context_id"], agent.agent_id,
            ))
        if name == "ember_feedback":
            agent = self._auth(args)
            fb = Feedback.from_submission(
                args["memory_id"], agent.agent_id, args,
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
