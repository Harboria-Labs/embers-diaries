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
)
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
                "subject": {"type": "string",
                    "description": ("Optional. Names the entity/claim this "
                        "memory is about (e.g. 'server-1', "
                        "'user:alice@example.com'). When two memories in the "
                        "same namespace share a subject and disagree on some "
                        "other field, it's mapped as a conflict via the "
                        "persisted conflict engine for later triage "
                        "(ember_conflicts_for / ember_resolve_conflict). "
                        "Omit it and no conflict check runs at all -- this "
                        "is opt-in specifically so two unrelated memories "
                        "are never flagged just for having different text.")},
                "namespace": {"type": "string"},
                "session_id": {"type": "string"},
                "creation_reason": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
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
            "properties": {
                "record_id": {"type": "string"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
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
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
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
            "properties": {
                "record_id": {"type": "string"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
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
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
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
    # ── Proposal → durable memory (§4/§5/§12) ─────────────────────────────────
    # Without these, ember_propose_memory dead-ends: an agent can attach sealed
    # evidence to a proposal and nothing can ever admit it to durable memory.
    {
        "name": "ember_submit",
        "description": ("Route a pending proposal through the Promotion Engine. "
                        "The engine decides (per configured mode + policy) whether "
                        "it enters durable memory. On a hold nothing is written and "
                        "the proposal stays pending."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "proposal_id": {"type": "string"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["proposal_id"],
        },
    },
    {
        "name": "ember_promotion_route",
        "description": ("Dry run: what would the Promotion Engine decide for this "
                        "proposal? Writes nothing."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "proposal_id": {"type": "string"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["proposal_id"],
        },
    },
    {
        "name": "ember_promote",
        "description": ("Explicitly promote a pending proposal into durable memory "
                        "(an authenticated caller's own decision, recorded as "
                        "promotion_method=human). Promotion means it met the "
                        "criteria to become durable memory, NOT that it is true — "
                        "the memory carries its own status."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "proposal_id": {"type": "string"},
                "status": {"type": "string",
                            "description": "verified / provisional / disputed"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["proposal_id"],
        },
    },
    {
        "name": "ember_reject",
        "description": ("Reject a pending proposal. Append-only: it stays "
                        "permanently queryable as rejected and never becomes a "
                        "memory."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "proposal_id": {"type": "string"},
                "reason": {"type": "string"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["proposal_id"],
        },
    },
    {
        "name": "ember_list_proposals",
        "description": ("Proposals in a namespace, optionally filtered by status "
                        "(pending / promoted / rejected) — find what awaits a "
                        "promotion decision."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "status": {"type": "string"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["namespace"],
        },
    },
    {
        "name": "ember_attach_evidence",
        "description": ("Attach independent evidence to an EXISTING durable "
                        "memory. Append-only — the memory is not modified, so its "
                        "hash is untouched and its confirmation trail only grows."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string"},
                "source": {"type": "string"},
                "source_type": {"type": "string"},
                "reference": {"type": "string"},
                "description": {"type": "string"},
                "session_id": {"type": "string"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["memory_id", "source"],
        },
    },
    {
        "name": "ember_evidence_for",
        "description": ("Every evidence record supporting a memory. An empty list "
                        "means the memory rests on a bare assertion, not evidence."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["memory_id"],
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
    {
        "name": "ember_map_conflict",
        "description": ("Map a SEMANTIC (or STORAGE) contradiction between two "
                        "existing memories (spec §7). Neither memory is modified "
                        "or destroyed — this records a CONFLICT record (status "
                        "OPEN) and draws a symmetric contradicts edge between "
                        "them, so the contradiction is visible both as a "
                        "queryable object and via graph traversal. Idempotent: "
                        "mapping the same live pair again returns the existing "
                        "conflict id rather than duplicating it."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_a": {"type": "string"},
                "memory_b": {"type": "string"},
                "conflict_type": {"type": "string",
                    "description": "semantic (default) or storage"},
                "note": {"type": "string"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["memory_a", "memory_b"],
        },
    },
    {
        "name": "ember_conflicts_for",
        "description": ("Every mapped Conflict involving a given memory, on "
                        "either side. By default only live (not resolved/"
                        "superseded) conflicts; pass include_closed for the "
                        "full triage history."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string"},
                "include_closed": {"type": "boolean"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["memory_id"],
        },
    },
    {
        "name": "ember_resolve_conflict",
        "description": ("Mark a mapped conflict RESOLVED with a resolution "
                        "note. Append-only, like everything else here — this "
                        "records a new version of the conflict with the "
                        "decision; neither contradicting memory is deleted or "
                        "changed. Use ember_conflicts_for first to find the "
                        "conflict_id."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "conflict_id": {"type": "string"},
                "resolution": {"type": "string"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["conflict_id", "resolution"],
        },
    },
    {
        "name": "ember_reflect",
        "description": ("Run a reflection cycle over a namespace: examines "
                        "memories for confidence decay and any custom "
                        "reflection triggers, and PERSISTS the resulting "
                        "reflective annotations (db.annotate) -- it does not "
                        "modify or create memories, only comments on them. "
                        "Nothing calls this automatically; there is no "
                        "scheduler. Run it yourself periodically, or have an "
                        "agent call it at the end of a session."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "limit": {"type": "integer"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": [],
        },
    },
    {
        "name": "ember_consolidate",
        "description": ("Run memory consolidation over a namespace: groups "
                        "memories by shared tags or temporal proximity and "
                        "writes a new, higher-confidence consolidated "
                        "record linking back to every source (sources are "
                        "never deprecated or deleted). The consolidated "
                        "record lands in the SAME namespace it read from, "
                        "so it's findable via a plain ember_recall "
                        "afterward. Nothing calls this automatically -- run "
                        "it yourself periodically."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": [],
        },
    },
    {
        "name": "ember_segment_episodes",
        "description": ("Group a namespace's memories into episodes using "
                        "temporal gaps, tag-overlap shifts, and a surprise "
                        "score -- returns the groupings directly; nothing "
                        "is written to the store (episodes aren't persisted "
                        "as their own record type). Purely a read-side "
                        "computation for the caller to use or discard."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "agent_id": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": [],
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
            return _text({"id": rid, "agent_id": agent.agent_id})

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

        # ── Proposal → durable memory ─────────────────────────────────────────
        # These complete the pipeline the propose tool starts. Without them a
        # sealed-evidence proposal can never become a memory.

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

        if name == "ember_resolve_conflict":
            agent = self._auth(args)
            new_id, old_id = self.db.resolve_conflict(
                args["conflict_id"], args["resolution"],
                changed_by=agent.agent_id)
            return _text({"conflict_id": new_id, "superseded": old_id})

        if name == "ember_reflect":
            self._auth(args)
            annotations = self.protocol.reflect(
                namespace=args.get("namespace"),
                limit=int(args.get("limit", 50)))
            return _text({
                "reflections": len(annotations),
                "annotations": [
                    {"content": a.content, "type": a.annotation_type,
                     "target_record_id": a.target_record_id}
                    for a in annotations
                ],
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
    """Write one MCP stdio message as one newline-delimited JSON document."""
    sys.stdout.write(json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _read() -> dict | None:
    """Read newline-delimited JSON, accepting legacy Content-Length input."""
    line = sys.stdin.readline()
    if line == "":
        return None

    stripped = line.strip()
    if stripped.startswith("{"):
        return json.loads(stripped)
    if not stripped:
        # Ignore harmless blank lines between newline-delimited messages.
        return _read()
    if ":" not in stripped:
        raise json.JSONDecodeError("Expected a JSON-RPC message", stripped, 0)

    # Tolerate clients which still send LSP-style Content-Length framing.  We
    # never emit it: MCP stdio responses are newline-delimited JSON.
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
    # On Windows, a TextIOWrapper sender can turn an already-CRLF-delimited
    # frame into CRCRLF.  Universal newline decoding then leaves one LF after
    # the header terminator.  Discard only that delimiter residue and replace
    # it so the declared byte count still governs the JSON body.
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
            # Keep one bad request from taking down a long-lived stdio server.
            # Notifications deliberately receive no reply.
            reply = None if isinstance(message, dict) and "id" not in message else {
                "jsonrpc": "2.0",
                "id": message.get("id") if isinstance(message, dict) else None,
                "error": {"code": -32603, "message": "Internal error"},
            }
        if reply is not None:
            _write(reply)


if __name__ == "__main__":
    main()
