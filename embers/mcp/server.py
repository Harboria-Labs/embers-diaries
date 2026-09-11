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
from ..core.types import (
    MemoryStatus, PromotionMethod, ProposalStatus, SourceType, ConflictType,
    ConflictStatus,
)
from ..db import EmberDB
from ..identity.registry import AgentRegistry
from ..integration import MemoryProtocol
from .tools import TOOLS

PROTOCOL = "2024-11-05"


def _text(obj: Any) -> dict:
    if isinstance(obj, str):
        text = obj
    else:
        text = json.dumps(obj, default=str)
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _err(msg: str) -> dict:
    return {"content": [{"type": "text", "text": msg}], "isError": True}


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
            content = args["content"]
            subject = args.get("subject")
            if subject is not None:
                content = {"content": content, "subject": subject}
            rid = self.protocol.remember(
                content,
                namespace=args.get("namespace"),
                written_by=agent.agent_id,
                agent_id=agent.agent_id,
                session_id=args.get("session_id"),
                creation_reason=args.get("creation_reason"),
                tags=args.get("tags"),
            )
            if args.get("session_id") and self.db.get_session(args["session_id"]):
                self.db.record_memory_write(
                    args["session_id"], rid, changed_by=agent.agent_id)
            return _text({
                "id": rid,
                "agent_id": agent.agent_id,
                "possible_conflicts": getattr(self.protocol, "_last_conflict_hints", []) or [],
            })

        if name == "ember_read":
            self._auth(args)
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
                "tags": rec.tags,
                "annotations": [a.to_dict() for a in rec.annotations],
            })

        if name == "ember_search":
            self._auth(args)
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
            self._auth(args)
            hist = self.db.get_history(args["record_id"])
            return _text([{"id": r.id, "data": r.data} for r in hist])

        if name == "ember_get_graph":
            self._auth(args)
            neighbors = self.db.neighbors(
                args["record_id"], depth=int(args.get("depth", 1)))
            return _text([{"id": r.id, "data": r.data} for r in neighbors])

        if name == "ember_get_session":
            self._auth(args)
            session = self.db.get_session(args["session_id"])
            if session is None:
                return _err("session not found")
            return _text(session.to_dict())

        if name == "ember_start_session":
            agent = self._auth(args)
            sid = self.db.start_session(
                agent_id=agent.agent_id,
                task=args.get("task", ""),
                namespace=args.get("namespace", self.protocol.namespace),
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
                namespace=args.get("namespace", self.protocol.namespace),
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

        if name == "ember_submit":
            agent = self._auth(args)
            result = self.db.submit(args["proposal_id"],
                                    validated_by=agent.agent_id)
            return _text({
                "promoted": result.promoted,
                "memory_id": result.memory_id,
                **result.decision.to_dict(),
            })

        if name == "ember_promotion_route":
            self._auth(args)
            decision = self.db.promotion_route(args["proposal_id"])
            return _text(decision.to_dict())

        if name == "ember_promote":
            agent = self._auth(args)
            status = args.get("status")
            memory_id, proposal_id = self.db.promote(
                args["proposal_id"],
                validated_by=agent.agent_id,
                status=MemoryStatus(status) if status else None,
                promotion_method=PromotionMethod.HUMAN,
            )
            return _text({
                "memory_id": memory_id,
                "proposal_id": proposal_id,
                "method": PromotionMethod.HUMAN.value,
                "status": self.db.memory_status(memory_id).value,
            })

        if name == "ember_reject":
            agent = self._auth(args)
            rejected_id = self.db.reject(
                args["proposal_id"], reason=args.get("reason", ""),
                rejected_by=agent.agent_id)
            return _text({"proposal_id": rejected_id, "status": "rejected"})

        if name == "ember_list_proposals":
            self._auth(args)
            status = args.get("status")
            found = self.db.proposals(
                args["namespace"],
                status=ProposalStatus(status) if status else None)
            return _text([{
                "proposal_id": p.proposal_id,
                "discovery": p.discovery,
                "reason": p.reason,
                "confidence": p.confidence,
                "status": p.status.value,
                "agent_id": p.agent_id,
                "evidence_count": len(p.evidence),
            } for p in found])

        if name == "ember_attach_evidence":
            agent = self._auth(args)
            ev = Evidence(
                source=args["source"],
                source_type=SourceType(args.get("source_type", "directly_observed")),
                reference=args.get("reference", ""),
                description=args.get("description", ""),
                agent_id=agent.agent_id,
                session_id=args.get("session_id"),
            )
            ev.seal()
            eid = self.db.attach_evidence(args["memory_id"], ev)
            return _text({"evidence_id": eid, "memory_id": args["memory_id"]})

        if name == "ember_evidence_for":
            self._auth(args)
            records = self.db.evidence_for(args["memory_id"])
            return _text([{
                "id": r.id,
                "source": (r.data or {}).get("source"),
                "source_type": (r.data or {}).get("source_type"),
                "description": (r.data or {}).get("description"),
                "agent_id": r.agent_id,
                "content_hash": r.content_hash,
            } for r in records])

        if name == "ember_report_failure":
            agent = self._auth(args)
            failure = Failure(
                namespace=args.get("namespace", self.protocol.namespace),
                approach=args["approach"],
                failed=args.get("failed", args["approach"]),
                cause=args.get("cause", ""),
                agent_id=agent.agent_id,
                session_id=args.get("session_id"),
            )
            fid = self.db.report_failure(failure)
            return _text({"failure_id": fid, "agent_id": agent.agent_id})

        if name == "ember_map_conflict":
            agent = self._auth(args)
            ctype = ConflictType(args.get("conflict_type", "semantic"))
            cid = self.db.map_conflict(
                args["memory_a"], args["memory_b"],
                detected_by=agent.agent_id,
                conflict_type=ctype,
                note=args.get("note", ""))
            return _text({"conflict_id": cid})

        if name == "ember_conflicts_for":
            self._auth(args)
            conflicts = self.db.conflicts_for(
                args["memory_id"],
                include_closed=args.get("include_closed", False))
            return _text([{
                "conflict_id": c.conflict_id,
                "namespace": c.namespace,
                "memory_a": c.memory_a,
                "memory_b": c.memory_b,
                "conflict_type": c.conflict_type.value,
                "status": c.status.value,
                "detected_by": c.detected_by,
                "resolution": c.resolution,
                "note": c.note,
            } for c in conflicts])

        if name == "ember_open_conflicts":
            self._auth(args)
            ns = args.get("namespace") or self.protocol.namespace
            queue = {ConflictStatus.OPEN, ConflictStatus.INVESTIGATING}
            found = []
            for c in self.db.conflict_records(namespace=ns):
                if c.status in queue:
                    found.append({
                        "conflict_id": c.conflict_id,
                        "namespace": c.namespace,
                        "memory_a": c.memory_a,
                        "memory_b": c.memory_b,
                        "conflict_type": c.conflict_type.value,
                        "status": c.status.value,
                        "detected_by": c.detected_by,
                        "resolution": c.resolution,
                        "note": c.note,
                    })
            return _text(found)

        if name == "ember_resolve_conflict":
            agent = self._auth(args)
            raw = (args.get("status") or "resolved").lower()
            mapping = {
                "investigating": ConflictStatus.INVESTIGATING,
                "resolved": ConflictStatus.RESOLVED,
                "accepted_both": ConflictStatus.ACCEPTED_BOTH,
                "dismissed": ConflictStatus.SUPERSEDED,
            }
            status = mapping.get(raw)
            if status is None:
                return _err(
                    "status must be investigating | resolved | accepted_both | dismissed")
            note = args.get("resolution") or ""
            if args.get("winner_id"):
                note = (note + f" winner={args['winner_id']}").strip()
            if raw == "dismissed" and "dismissed" not in note.lower():
                note = (note + " dismissed: not a conflict").strip()
            new_id, old_id = self.db.update_conflict_status(
                args["conflict_id"], status, note, changed_by=agent.agent_id)
            return _text({
                "conflict_id": new_id,
                "superseded": old_id,
                "status": status.value,
            })

        if name == "ember_reflect":
            self._auth(args)
            annotations = self.protocol.reflect(
                namespace=args.get("namespace"),
                limit=int(args.get("limit", 50)))
            ns = args.get("namespace") or self.protocol.namespace
            queue = {ConflictStatus.OPEN, ConflictStatus.INVESTIGATING}
            open_conflicts = []
            for c in self.db.conflict_records(namespace=ns):
                if c.status in queue:
                    open_conflicts.append({
                        "conflict_id": c.conflict_id,
                        "namespace": c.namespace,
                        "memory_a": c.memory_a,
                        "memory_b": c.memory_b,
                        "status": c.status.value,
                        "note": c.note,
                    })
            return _text({
                "reflections": len(annotations),
                "annotations": [
                    {"content": a.content, "type": a.annotation_type,
                     "target_record_id": a.target_record_id}
                    for a in annotations
                ],
                "open_conflicts": open_conflicts,
            })

        if name == "ember_consolidate":
            self._auth(args)
            new_ids = self.protocol.consolidate(namespace=args.get("namespace"))
            return _text({"consolidated": len(new_ids), "new_record_ids": new_ids})

        if name == "ember_segment_episodes":
            self._auth(args)
            episodes = self.protocol.segment_episodes(namespace=args.get("namespace"))
            return _text(episodes)

        return _err(f"unknown tool: {name}")

    def handle(self, message: dict) -> dict | None:
        if not isinstance(message, dict):
            return {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "Invalid Request"},
            }

        method = message.get("method")
        is_notification = "id" not in message
        msg_id = message.get("id")
        if method == "initialize":
            if is_notification:
                return None
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
            if is_notification:
                return None
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
        if method == "tools/call":
            params = message.get("params") or {}
            result = self.call_tool(params.get("name", ""), params.get("arguments") or {})
            if is_notification:
                return None
            return {"jsonrpc": "2.0", "id": msg_id, "result": result}
        if method == "ping":
            if is_notification:
                return None
            return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        if is_notification:
            return None
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }


def _write(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _read() -> dict | None:
    line = sys.stdin.readline()
    if line == "":
        return None
    stripped = line.strip()
    if stripped.startswith("{"):
        return json.loads(stripped)
    if not stripped:
        return _read()
    if ":" not in stripped:
        raise json.JSONDecodeError("Expected a JSON-RPC message", stripped, 0)
    headers: dict[str, str] = {}
    while True:
        key, value = stripped.split(":", 1)
        headers[key.lower()] = value.strip()
        line = sys.stdin.readline()
        if line == "":
            raise json.JSONDecodeError("Unexpected EOF in headers", "", 0)
        stripped = line.strip()
        if not stripped:
            break
        if ":" not in stripped:
            raise json.JSONDecodeError("Malformed header", stripped, 0)
    try:
        length = int(headers["content-length"])
    except (KeyError, ValueError) as exc:
        raise json.JSONDecodeError("Missing or invalid Content-Length", "", 0) from exc
    if length < 0:
        raise json.JSONDecodeError("Invalid Content-Length", str(length), 0)
    body = sys.stdin.read(length)
    while body.startswith("\n"):
        body = body[1:] + sys.stdin.read(1)
    if len(body) != length:
        raise json.JSONDecodeError("Unexpected EOF in message body", body, len(body))
    return json.loads(body)


def main() -> None:
    server = EmberMCP()
    while True:
        try:
            message = _read()
        except (json.JSONDecodeError, UnicodeDecodeError):
            _write({
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            })
            continue
        if message is None:
            break
        try:
            reply = server.handle(message)
        except Exception:
            reply = None if isinstance(message, dict) and "id" not in message else {
                "jsonrpc": "2.0",
                "id": message.get("id") if isinstance(message, dict) else None,
                "error": {"code": -32603, "message": "Internal error"},
            }
        if reply is not None:
            _write(reply)


if __name__ == "__main__":
    main()
