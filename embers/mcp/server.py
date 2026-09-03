"""
Ember MCP server (spec §19).

Interface only. Tool handlers call EmberDB + AgentRegistry — same objects
as the REST /v1 routes. No vendor-specific logic (spec §31).
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from ..core.evidence import Evidence
from ..core.failure import Failure
from ..core.proposal import MemoryProposal
from ..core.types import SourceType
from ..db import EmberDB
from ..identity.registry import AgentRegistry
from ..integration import MemoryProtocol

PROTOCOL = "2024-11-05"


def _text(obj: Any) -> dict:
    if isinstance(obj, str):
        text = obj
    else:
        text = json.dumps(obj, default=str)
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _err(msg: str) -> dict:
    return {"content": [{"type": "text", "text": msg}], "isError": True}


TOOLS = [
    {
        "name": "ember_register",
        "description": "Register this agent. Returns agent_id and token. Store both.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "provider": {"type": "string"},
                "model": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "ember_write",
        "description": "Write a durable memory attributed to the authenticated agent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "namespace": {"type": "string"},
                "session_id": {"type": "string"},
                "creation_reason": {"type": "string"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["content"],
        },
    },
    {
        "name": "ember_read",
        "description": "Read a record by id.",
        "inputSchema": {
            "type": "object",
            "properties": {"record_id": {"type": "string"}},
            "required": ["record_id"],
        },
    },
    {
        "name": "ember_search",
        "description": "Full-text search over memories.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "namespace": {"type": "string"},
                "top_k": {"type": "integer"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "ember_recall",
        "description": "Retrieve relevant memories for a query.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "namespace": {"type": "string"},
                "top_k": {"type": "integer"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "ember_get_history",
        "description": "Version history for a record.",
        "inputSchema": {
            "type": "object",
            "properties": {"record_id": {"type": "string"}},
            "required": ["record_id"],
        },
    },
    {
        "name": "ember_get_graph",
        "description": "Graph neighbors of a record.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "record_id": {"type": "string"},
                "depth": {"type": "integer"},
            },
            "required": ["record_id"],
        },
    },
    {
        "name": "ember_get_session",
        "description": "Load a session by id.",
        "inputSchema": {
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"],
        },
    },
    {
        "name": "ember_start_session",
        "description": "Open a session for the authenticated agent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "namespace": {"type": "string"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
        },
    },
    {
        "name": "ember_propose_memory",
        "description": "Submit a memory proposal (not yet durable memory).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "discovery": {},
                "reason": {"type": "string"},
                "confidence": {"type": "number"},
                "namespace": {"type": "string"},
                "session_id": {"type": "string"},
                "evidence": {"type": "array"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["discovery"],
        },
    },
    {
        "name": "ember_report_failure",
        "description": "Record a failed approach so other agents can skip it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "approach": {"type": "string"},
                "failed": {"type": "string"},
                "cause": {"type": "string"},
                "namespace": {"type": "string"},
                "session_id": {"type": "string"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["approach"],
        },
    },
]


class EmberMCP:
    def __init__(self, db: EmberDB | None = None, store_path: str | None = None):
        path = store_path or os.environ.get("EMBER_STORE", "./ember_store")
        self.db = db or EmberDB.connect(path)
        self.registry = AgentRegistry(self.db)
        self.protocol = MemoryProtocol(self.db)

    def _auth(self, args: dict):
        agent_id = args.get("agent_id") or os.environ.get("EMBER_AGENT_ID")
        token = args.get("token") or os.environ.get("EMBER_TOKEN")
        if not agent_id or not token:
            raise PermissionError(
                "agent_id and token required (args or EMBER_AGENT_ID / EMBER_TOKEN)")
        return self.registry.authenticate(agent_id, token)

    def call_tool(self, name: str, args: dict | None) -> dict:
        args = args or {}
        try:
            return self._call(name, args)
        except PermissionError as e:
            return _err(str(e))
        except KeyError as e:
            return _err(str(e))
        except Exception as e:
            return _err(f"{type(e).__name__}: {e}")

    def _call(self, name: str, args: dict) -> dict:
        if name == "ember_register":
            ident, token = self.registry.register(
                name=args.get("name", ""),
                provider=args.get("provider", "unknown"),
                model=args.get("model", "unknown"),
            )
            return _text({
                "agent_id": ident.agent_id,
                "token": token,
                "provider": ident.provider,
                "model": ident.model,
            })

        if name == "ember_write":
            agent = self._auth(args)
            rid = self.protocol.remember(
                args["content"],
                namespace=args.get("namespace"),
                written_by=agent.agent_id,
                agent_id=agent.agent_id,
                session_id=args.get("session_id"),
                creation_reason=args.get("creation_reason"),
            )
            if args.get("session_id") and self.db.get_session(args["session_id"]):
                self.db.record_memory_write(
                    args["session_id"], rid, changed_by=agent.agent_id)
            return _text({"id": rid, "agent_id": agent.agent_id})

        if name == "ember_read":
            rec = self.db.get(args["record_id"], True, True)
            if rec is None:
                return _err("not found")
            return _text({
                "id": rec.id,
                "namespace": rec.namespace,
                "data": rec.data,
                "agent_id": rec.agent_id,
                "session_id": rec.session_id,
                "content_hash": rec.content_hash,
            })

        if name == "ember_search":
            results = self.db.search(
                args["query"], args.get("namespace"), int(args.get("top_k", 10)),
            )
            return _text([{"id": r.id, "score": s, "data": r.data} for r, s in results])

        if name == "ember_recall":
            self._auth(args)
            result = self.protocol.recall(
                args["query"],
                top_k=int(args.get("top_k", 10)),
                namespace=args.get("namespace"),
                format="structured",
            )
            return _text(result)

        if name == "ember_get_history":
            hist = self.db.get_history(args["record_id"])
            return _text([{"id": r.id, "data": r.data} for r in hist])

        if name == "ember_get_graph":
            neighbors = self.db.neighbors(
                args["record_id"], depth=int(args.get("depth", 1)))
            return _text([{"id": r.id, "data": r.data} for r in neighbors])

        if name == "ember_get_session":
            session = self.db.get_session(args["session_id"])
            if session is None:
                return _err("session not found")
            return _text(session.to_dict())

        if name == "ember_start_session":
            agent = self._auth(args)
            sid = self.db.start_session(
                agent_id=agent.agent_id,
                task=args.get("task", ""),
                namespace=args.get("namespace", "default"),
            )
            return _text({"session_id": sid, "agent_id": agent.agent_id})

        if name == "ember_propose_memory":
            agent = self._auth(args)
            evidence = []
            for item in args.get("evidence") or []:
                ev = Evidence(
                    source=item.get("source", ""),
                    source_type=SourceType(item.get("source_type", "directly_observed")),
                    reference=item.get("reference", ""),
                    description=item.get("description", ""),
                    agent_id=agent.agent_id,
                    session_id=args.get("session_id"),
                )
                ev.seal()
                evidence.append(ev)
            proposal = MemoryProposal(
                namespace=args.get("namespace", "default"),
                discovery=args.get("discovery"),
                reason=args.get("reason", ""),
                evidence=evidence,
                confidence=float(args.get("confidence", 0.5)),
                written_by=agent.agent_id,
                agent_id=agent.agent_id,
                session_id=args.get("session_id"),
            )
            pid = self.db.propose(proposal)
            return _text({"proposal_id": pid, "agent_id": agent.agent_id})

        if name == "ember_report_failure":
            agent = self._auth(args)
            failure = Failure(
                namespace=args.get("namespace", "default"),
                approach=args["approach"],
                failed=args.get("failed", args["approach"]),
                cause=args.get("cause", ""),
                agent_id=agent.agent_id,
                session_id=args.get("session_id"),
            )
            fid = self.db.report_failure(failure)
            return _text({"failure_id": fid, "agent_id": agent.agent_id})

        return _err(f"unknown tool: {name}")

    def handle(self, message: dict) -> dict | None:
        method = message.get("method")
        msg_id = message.get("id")
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": PROTOCOL,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "ember-diaries", "version": "0.2.0"},
                },
            }
        if method in ("notifications/initialized", "initialized"):
            return None
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
        if method == "tools/call":
            params = message.get("params") or {}
            result = self.call_tool(params.get("name", ""), params.get("arguments") or {})
            return {"jsonrpc": "2.0", "id": msg_id, "result": result}
        if method == "ping":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        if msg_id is None:
            return None
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }


def _write(msg: dict) -> None:
    body = json.dumps(msg)
    sys.stdout.write(f"Content-Length: {len(body.encode('utf-8'))}\r\n\r\n{body}")
    sys.stdout.flush()


def _read() -> dict | None:
    header = {}
    while True:
        line = sys.stdin.readline()
        if line == "":
            return None
        stripped = line.strip()
        if stripped.startswith("{"):
            return json.loads(stripped)
        if stripped == "":
            break
        if ":" in stripped:
            key, val = stripped.split(":", 1)
            header[key.lower()] = val.strip()
    n = int(header.get("content-length", "0"))
    if n <= 0:
        return None
    body = sys.stdin.read(n)
    return json.loads(body)


def main() -> None:
    server = EmberMCP()
    while True:
        try:
            message = _read()
        except Exception:
            continue
        if message is None:
            break
        reply = server.handle(message)
        if reply is not None:
            _write(reply)


if __name__ == "__main__":
    main()
