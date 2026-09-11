"""Provider-neutral /v1 HTTP surface. Calls EmberDB + AgentRegistry only."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from ..core.evidence import Evidence
from ..core.failure import Failure
from ..core.proposal import MemoryProposal
from ..core.types import SourceType
from ..identity.registry import AgentRegistry
from ..integration import MemoryProtocol

router = APIRouter(prefix="/v1")

_registry = None
_protocol = None


def _reg(db):
    global _registry
    if _registry is None:
        _registry = AgentRegistry(db)
    return _registry


def _proto(db):
    global _protocol
    if _protocol is None:
        _protocol = MemoryProtocol(db)
    return _protocol


def require_agent(db, agent_id: str | None, token: str | None):
    if not agent_id or not token:
        raise HTTPException(401, "X-Ember-Agent-Id and X-Ember-Token required")
    try:
        return _reg(db).authenticate(agent_id, token)
    except PermissionError as e:
        raise HTTPException(401, str(e)) from e


@router.post("/agents/register")
async def register_agent(body: dict):
    from . import _get_db
    db = _get_db()
    name = body.get("name", "")
    try:
        ident, token = _reg(db).register(
            name=name,
            provider=body.get("provider", "unknown"),
            model=body.get("model", "unknown"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {
        "agent_id": ident.agent_id,
        "name": ident.name,
        "provider": ident.provider,
        "model": ident.model,
        "token": token,
        "note": "Store the token. Ember only keeps a hash.",
    }


@router.post("/memory/write")
async def memory_write(
    body: dict,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
):
    from . import _get_db
    db = _get_db()
    agent = require_agent(db, x_ember_agent_id, x_ember_token)
    content = body.get("content")
    if content is None:
        raise HTTPException(400, "content required")
    rid = _proto(db).remember(
        content,
        tags=body.get("tags"),
        confidence=body.get("confidence", 1.0),
        namespace=body.get("namespace"),
        written_by=agent.agent_id,
        agent_id=agent.agent_id,
        session_id=body.get("session_id"),
        creation_reason=body.get("creation_reason"),
        derived_from=body.get("derived_from"),
        memory_type=body.get("memory_type", "unscoped"),
        room=body.get("room", "unscoped"),
    )
    if body.get("session_id") and db.get_session(body["session_id"]):
        db.record_memory_write(body["session_id"], rid, changed_by=agent.agent_id)
    return {"id": rid, "agent_id": agent.agent_id}


@router.post("/memory/recall")
async def memory_recall(
    body: dict,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
):
    from . import _get_db
    db = _get_db()
    require_agent(db, x_ember_agent_id, x_ember_token)
    query = body.get("query", "")
    if not query:
        raise HTTPException(400, "query required")
    result = _proto(db).recall(
        query,
        top_k=body.get("top_k", 10),
        namespace=body.get("namespace"),
        room=body.get("room"),
        format=body.get("format", "structured"),
    )
    return {"query": query, "memories": result}


@router.post("/sessions")
async def start_session(
    body: dict,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
):
    from . import _get_db
    db = _get_db()
    agent = require_agent(db, x_ember_agent_id, x_ember_token)
    sid = db.start_session(
        agent_id=agent.agent_id,
        task=body.get("task", ""),
        namespace=body.get("namespace", "default"),
    )
    return {"session_id": sid, "agent_id": agent.agent_id}


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
):
    from . import _get_db
    db = _get_db()
    require_agent(db, x_ember_agent_id, x_ember_token)
    session = db.get_session(session_id)
    if session is None:
        raise HTTPException(404, "session not found")
    return session.to_dict()


@router.post("/memory/propose")
async def propose_memory(
    body: dict,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
):
    from . import _get_db
    db = _get_db()
    agent = require_agent(db, x_ember_agent_id, x_ember_token)
    evidence = []
    for item in body.get("evidence", []):
        ev = Evidence(
            source=item.get("source", ""),
            source_type=SourceType(item.get("source_type", "directly_observed")),
            reference=item.get("reference", ""),
            description=item.get("description", ""),
            agent_id=agent.agent_id,
            session_id=body.get("session_id"),
        )
        ev.seal()
        evidence.append(ev)
    proposal = MemoryProposal(
        namespace=body.get("namespace", "default"),
        discovery=body.get("discovery") or body.get("claim"),
        reason=body.get("reason", ""),
        evidence=evidence,
        confidence=float(body.get("confidence", 0.5)),
        derivation=body.get("derived_from", []),
        written_by=agent.agent_id,
        agent_id=agent.agent_id,
        session_id=body.get("session_id"),
    )
    pid = db.propose(proposal)
    if body.get("session_id") and db.get_session(body["session_id"]):
        db.record_discovery(body["session_id"], pid, changed_by=agent.agent_id)
    return {"proposal_id": pid, "agent_id": agent.agent_id}


# ── Proposal → durable memory (§4/§5/§12) ─────────────────────
# /memory/propose alone dead-ends: an agent can attach sealed evidence to a
# proposal and nothing can admit it to durable memory. These complete the
# pipeline, mirroring the MCP surface so both transports expose the same thing.


@router.post("/memory/submit")
async def submit_proposal(
    body: dict,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
):
    """Route a pending proposal through the Promotion Engine."""
    from . import _get_db
    db = _get_db()
    agent = require_agent(db, x_ember_agent_id, x_ember_token)
    pid = body.get("proposal_id")
    if not pid:
        raise HTTPException(400, "proposal_id required")
    try:
        result = db.submit(pid, validated_by=agent.agent_id)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return {"promoted": result.promoted, "memory_id": result.memory_id,
            **result.decision.to_dict()}


@router.post("/memory/promotion_route")
async def promotion_route(
    body: dict,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
):
    """Dry run — what would the engine decide? Writes nothing."""
    from . import _get_db
    db = _get_db()
    require_agent(db, x_ember_agent_id, x_ember_token)
    pid = body.get("proposal_id")
    if not pid:
        raise HTTPException(400, "proposal_id required")
    try:
        return db.promotion_route(pid).to_dict()
    except KeyError as e:
        raise HTTPException(404, str(e)) from e


@router.post("/memory/promote")
async def promote_proposal(
    body: dict,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
):
    """Explicit caller decision — recorded as promotion_method=human.

    Promotion means the proposal met the criteria to become durable memory,
    NOT that it is true; the memory carries its own epistemic status."""
    from . import _get_db
    from ..core.types import MemoryStatus, PromotionMethod
    db = _get_db()
    agent = require_agent(db, x_ember_agent_id, x_ember_token)
    pid = body.get("proposal_id")
    if not pid:
        raise HTTPException(400, "proposal_id required")
    status = body.get("status")
    try:
        memory_id, proposal_id = db.promote(
            pid, validated_by=agent.agent_id,
            status=MemoryStatus(status) if status else None,
            promotion_method=PromotionMethod.HUMAN,
        )
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return {"memory_id": memory_id, "proposal_id": proposal_id,
            "method": PromotionMethod.HUMAN.value,
            "status": db.memory_status(memory_id).value}


@router.post("/memory/reject")
async def reject_proposal(
    body: dict,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
):
    """Append-only rejection: stays queryable as rejected, never a memory."""
    from . import _get_db
    db = _get_db()
    agent = require_agent(db, x_ember_agent_id, x_ember_token)
    pid = body.get("proposal_id")
    if not pid:
        raise HTTPException(400, "proposal_id required")
    try:
        rejected_id = db.reject(pid, reason=body.get("reason", ""),
                                rejected_by=agent.agent_id)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return {"proposal_id": rejected_id, "status": "rejected"}


@router.get("/memory/proposals")
async def list_proposals(
    namespace: str,
    status: str | None = None,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
):
    """Proposals in a namespace — find what awaits a promotion decision."""
    from . import _get_db
    from ..core.types import ProposalStatus
    db = _get_db()
    require_agent(db, x_ember_agent_id, x_ember_token)
    found = db.proposals(namespace,
                         status=ProposalStatus(status) if status else None)
    return {"proposals": [{
        "proposal_id": p.proposal_id,
        "discovery": p.discovery,
        "reason": p.reason,
        "confidence": p.confidence,
        "status": p.status.value,
        "agent_id": p.agent_id,
        "evidence_count": len(p.evidence),
    } for p in found]}


@router.post("/memory/{memory_id}/evidence")
async def attach_evidence(
    memory_id: str,
    body: dict,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
):
    """Attach independent evidence to an existing memory (append-only, so the
    memory's hash is untouched and its confirmation trail only grows)."""
    from . import _get_db
    db = _get_db()
    agent = require_agent(db, x_ember_agent_id, x_ember_token)
    source = body.get("source")
    if not source:
        raise HTTPException(400, "source required")
    ev = Evidence(
        source=source,
        source_type=SourceType(body.get("source_type", "directly_observed")),
        reference=body.get("reference", ""),
        description=body.get("description", ""),
        agent_id=agent.agent_id,
        session_id=body.get("session_id"),
    )
    ev.seal()
    try:
        eid = db.attach_evidence(memory_id, ev)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    return {"evidence_id": eid, "memory_id": memory_id}


@router.get("/memory/{memory_id}/evidence")
async def evidence_for(
    memory_id: str,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
):
    """Evidence supporting a memory. Empty means it rests on a bare assertion."""
    from . import _get_db
    db = _get_db()
    require_agent(db, x_ember_agent_id, x_ember_token)
    records = db.evidence_for(memory_id)
    return {"memory_id": memory_id, "evidence": [{
        "id": r.id,
        "source": (r.data or {}).get("source"),
        "source_type": (r.data or {}).get("source_type"),
        "description": (r.data or {}).get("description"),
        "agent_id": r.agent_id,
        "content_hash": r.content_hash,
    } for r in records]}


@router.post("/failures")
async def report_failure(
    body: dict,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
):
    from . import _get_db
    db = _get_db()
    agent = require_agent(db, x_ember_agent_id, x_ember_token)
    approach = body.get("approach", "")
    if not approach:
        raise HTTPException(400, "approach required")
    failure = Failure(
        namespace=body.get("namespace", "default"),
        approach=approach,
        failed=body.get("failed", approach),
        cause=body.get("cause", ""),
        agent_id=agent.agent_id,
        session_id=body.get("session_id"),
    )
    fid = db.report_failure(failure)
    return {"failure_id": fid, "agent_id": agent.agent_id}


@router.get("/failures")
async def list_failures(
    approach: str | None = None,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
):
    from . import _get_db
    db = _get_db()
    require_agent(db, x_ember_agent_id, x_ember_token)
    if approach:
        found = db.failures_for_approach(approach)
    else:
        found = db.failures()
    return {"failures": [f.to_dict() for f in found]}
