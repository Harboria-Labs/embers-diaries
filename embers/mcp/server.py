"""
Ember MCP server (spec §19).

Interface only. Tool handlers call EmberDB + AgentRegistry — same objects
as the REST /v1 routes. No vendor-specific logic (spec §31).
"""

from __future__ import annotations

import json
import argparse
import os
import sys
from typing import Any

from ..core.evidence import Evidence
from ..config import EmberConfig, load_config
from ..core.errors import ConcurrentModificationError
from ..core.failure import Failure
from ..core.proposal import MemoryProposal
from ..core.types import (
    MemoryStatus, PromotionMethod, ProposalStatus, SourceType,
)
from ..db import EmberDB
from ..identity.registry import AgentRegistry
from ..integration import MemoryProtocol
from ..engine.promotion import PromotionPolicy
from ..logging_config import configure_logging
from ..integration.conflict_protocol import (
    conflicts_for as conflict_contract_for,
    map_conflict as conflict_contract_map,
    open_conflicts as conflict_contract_open,
    transition_conflict as conflict_contract_transition,
)
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
    def __init__(self, db: EmberDB | None = None, store_path: str | None = None,
                 config: EmberConfig | None = None,
                 config_path: str | None = None):
        if config is None and db is not None:
            config = EmberConfig()
        if db is None:
            if config is not None and (store_path is not None or config_path is not None):
                raise ValueError(
                    "config cannot be combined with store_path or config_path")
            config = config or load_config(
                config_path=config_path, storage_path=store_path)
            config.require_runtime_supported()
            if not config.api.mcp_enabled:
                raise ValueError("api.mcp_enabled is false")
            policy = PromotionPolicy(
                min_confidence=config.evidence.min_confidence,
                verified_confidence=config.evidence.verified_confidence,
                require_evidence=config.evidence.require_evidence,
                minimum_evidence_items=config.evidence.minimum_items,
            )
            db = EmberDB.connect(
                config.storage.path,
                promotion_policy=policy,
                max_store_bytes=config.storage.max_store_bytes,
                max_record_bytes=config.storage.max_record_bytes,
                max_total_bytes=config.storage.max_total_bytes,
                runtime_config=config,
            )
            from ..integration.server_memory import prepare_memory_services
            prepare_memory_services(db, config.storage.path)
        else:
            config.require_runtime_supported()
            if not config.api.mcp_enabled:
                raise ValueError("api.mcp_enabled is false")
        self.db = db
        self.config = config
        self.registry = AgentRegistry(self.db)
        self.protocol = MemoryProtocol(self.db)
        from .lobby_surface import STORE
        STORE.configure(config.lobby)

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
            namespace = args.get("namespace") or self.protocol.namespace
            self.db.require_namespace_access(
                namespace, agent.agent_id, "write")
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
                primary_context=args.get("primary_context"),
            )
            if args.get("session_id") and self.db.get_session(args["session_id"]):
                self.db.record_memory_write(
                    args["session_id"], rid, changed_by=agent.agent_id)
            possible_conflicts = []
            if self.db.check_namespace_access(
                    namespace, agent.agent_id, "read"):
                possible_conflicts = (
                    getattr(self.protocol, "_last_conflict_hints", []) or [])
            return _text({
                "id": rid,
                "agent_id": agent.agent_id,
                "possible_conflicts": possible_conflicts,
            })

        if name == "ember_update":
            agent = self._auth(args)
            record = self.db.get(
                args["record_id"], include_deprecated=True,
                include_superseded=True)
            if record is None:
                return _err("not found")
            self.db.require_namespace_access(
                record.namespace, agent.agent_id, "read")
            self.db.require_namespace_access(
                record.namespace, agent.agent_id, "write")
            try:
                new_id, old_id = self.db.update(
                    args["record_id"],
                    args["data"],
                    written_by=agent.agent_id,
                    agent_id=agent.agent_id,
                    session_id=args.get("session_id"),
                    creation_reason=args.get("creation_reason"),
                    expected_hash=args["expected_hash"],
                )
            except ConcurrentModificationError as exc:
                return _err(json.dumps(exc.to_dict()))
            current = self.db.get(new_id)
            return _text({
                "record_id": new_id,
                "superseded_record_id": old_id,
                "content_hash": current.content_hash,
                "version": current.version,
            })

        if name == "ember_read":
            agent = self._auth(args)
            rec = self.db.get(args["record_id"], True, True)
            if rec is None:
                return _err("not found")
            self.db.require_namespace_access(
                rec.namespace, agent.agent_id, "read")
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
            agent = self._auth(args)
            namespace = args.get("namespace")
            if namespace is not None:
                self.db.require_namespace_access(
                    namespace, agent.agent_id, "read")
            results = self.db.search(
                args["query"], namespace, int(args.get("top_k", 10)),
            )
            if namespace is None:
                results = [
                    (record, score) for record, score in results
                    if self.db.check_namespace_access(
                        record.namespace, agent.agent_id, "read")
                ]
            return _text([{"id": r.id, "score": s, "data": r.data} for r, s in results])

        if name == "ember_query":
            agent = self._auth(args)
            namespace = args.get("namespace", self.protocol.namespace)
            self.db.require_namespace_access(
                namespace, agent.agent_id, "read")
            filters = dict(args.get("filters") or {})
            if args.get("session_id") is not None:
                if ("session_id" in filters
                        and filters["session_id"] != args["session_id"]):
                    return _err(
                        "session_id conflicts with filters.session_id")
                filters["session_id"] = args["session_id"]
            records = self.db.query(
                namespace=namespace,
                filters=filters or None,
                tags=args.get("tags"),
                limit=int(args.get("limit", 100)),
                include_deprecated=args.get("include_deprecated", False),
                include_superseded=args.get("include_superseded", False),
            )
            return _text({
                "count": len(records),
                "records": [record.to_dict() for record in records],
            })

        if name == "ember_orient":
            agent = self._auth(args)
            namespace = args.get("namespace") or self.protocol.namespace
            self.db.require_namespace_access(namespace, agent.agent_id, "read")
            return _text(self.protocol.orient(args["clues"], namespace,
                hints=args.get("hints"), limits=args.get("limits"), signals=args.get("signals")))

        if name == "ember_recall":
            agent = self._auth(args)
            namespace = args.get("namespace") or self.protocol.namespace
            self.db.require_namespace_access(
                namespace, agent.agent_id, "read")
            result = self.protocol.recall(
                args["query"],
                top_k=int(args.get("top_k", 10)),
                namespace=args.get("namespace"),
                format="structured",
                primary_context=args.get("primary_context"),
                inspect_context=args.get("inspect_context", False),
            )
            return _text(result)

        if name == "ember_get_history":
            agent = self._auth(args)
            root = self.db.get(
                args["record_id"], include_deprecated=True,
                include_superseded=True)
            if root is not None:
                self.db.require_namespace_access(
                    root.namespace, agent.agent_id, "read")
            hist = self.db.get_history(args["record_id"])
            return _text([{"id": r.id, "data": r.data} for r in hist])

        if name == "ember_get_graph":
            agent = self._auth(args)
            root = self.db.get(
                args["record_id"], include_deprecated=True,
                include_superseded=True)
            if root is not None:
                self.db.require_namespace_access(
                    root.namespace, agent.agent_id, "read")
            neighbors = self.db.neighbors(
                args["record_id"], depth=int(args.get("depth", 1)))
            neighbors = [
                record for record in neighbors
                if self.db.check_namespace_access(
                    record.namespace, agent.agent_id, "read")
            ]
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
            return _text(conflict_contract_map(
                self.db,
                memory_a=args["memory_a"],
                memory_b=args["memory_b"],
                actor_id=agent.agent_id,
                conflict_type=args.get("conflict_type", "semantic"),
                note=args.get("note", ""),
            ))

        if name == "ember_conflicts_for":
            agent = self._auth(args)
            return _text(conflict_contract_for(
                self.db,
                memory_id=args["memory_id"],
                include_closed=args.get("include_closed", False),
                actor_id=agent.agent_id,
            ))

        if name == "ember_open_conflicts":
            agent = self._auth(args)
            return _text(conflict_contract_open(
                self.db,
                namespace=args.get("namespace") or self.protocol.namespace,
                actor_id=agent.agent_id,
            ))

        if name == "ember_resolve_conflict":
            agent = self._auth(args)
            return _text(conflict_contract_transition(
                self.db,
                conflict_id=args["conflict_id"],
                actor_id=agent.agent_id,
                status=args.get("status") or "resolved",
                resolution=args.get("resolution") or "",
                winner_id=args.get("winner_id"),
            ))

        if name == "ember_reflect":
            agent = self._auth(args)
            ns = args.get("namespace") or self.protocol.namespace
            self.db.require_namespace_access(ns, agent.agent_id, "read")
            self.db.require_namespace_access(ns, agent.agent_id, "write")
            annotations = self.protocol.reflect(
                namespace=args.get("namespace"),
                limit=int(args.get("limit", 50)))
            open_conflicts = conflict_contract_open(
                self.db, namespace=ns, actor_id=agent.agent_id)
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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Ember MCP stdio server")
    parser.add_argument("--config", help="path to an Ember TOML configuration file")
    parser.add_argument("--store", help="override storage.path")
    args = parser.parse_args(argv)
    config = load_config(config_path=args.config, storage_path=args.store)
    configure_logging(config.logging)
    server = EmberMCP(config=config)
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
