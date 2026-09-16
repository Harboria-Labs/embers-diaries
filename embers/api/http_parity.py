"""Close HTTP/MCP gaps: lobby consensus, proposal evidence, namespace default."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException

from ..core.evidence import Evidence
from ..core.failure import Failure
from ..core.proposal import MemoryProposal
from ..core.proposal_listing import proposal_listing
from ..core.types import SourceType

router = APIRouter(prefix="/v1")

_DROP_PATHS = {
    "/v1/memory/proposals",
    "/v1/memory/propose",
    "/v1/failures",
    "/query",
}


def install(app) -> None:
    app.router.routes[:] = [
        route for route in app.router.routes
        if getattr(route, "path", None) not in _DROP_PATHS
    ]
    app.include_router(router)
    app.include_router(legacy)


def _db():
    from . import _get_db
    return _get_db()


def _agent(db, agent_id, token):
    from . import v1
    return v1.require_agent(db, agent_id, token)


@router.post("/lobby/corroborate")
async def lobby_corroborate(
    body: dict,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
    x_ember_session_id: str | None = Header(default=None),
):
    from ..lobby.store import LobbyError
    from . import v1
    db = _db()
    agent = _agent(db, x_ember_agent_id, x_ember_token)
    session_id, _session = v1._lobby_context(
        db, agent, body.get("session_id"), x_ember_session_id)
    post_id = body.get("post_id")
    if not post_id:
        raise HTTPException(400, "post_id required")
    try:
        return v1._lobby_store().corroborate(
            session_id=session_id,
            agent_id=agent.agent_id,
            post_id=post_id,
        )
    except LobbyError as e:
        raise HTTPException(400, str(e)) from e


@router.post("/lobby/close")
async def lobby_close(
    body: dict | None = None,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
    x_ember_session_id: str | None = Header(default=None),
):
    from types import SimpleNamespace
    from ..lobby.store import LobbyError
    from ..mcp.lobby_surface import _close
    from . import v1
    db = _db()
    agent = _agent(db, x_ember_agent_id, x_ember_token)
    body = body or {}
    session_id, _session = v1._lobby_context(
        db, agent, body.get("session_id"), x_ember_session_id)
    try:
        result = _close(SimpleNamespace(db=db), session_id)
    except LobbyError as e:
        raise HTTPException(400, str(e)) from e
    for row in result.get("flushed_failures") or []:
        fid = row.get("failure_id")
        if fid:
            try:
                db.record_failure(session_id, fid, changed_by=agent.agent_id)
            except TypeError:
                db.record_failure(session_id, fid)
    return result


@router.get("/memory/proposals")
async def list_proposals(
    namespace: str | None = None,
    status: str | None = None,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
):
    from . import v1
    from ..core.types import ProposalStatus
    db = _db()
    _agent(db, x_ember_agent_id, x_ember_token)
    ns = namespace or v1._proto(db).namespace
    found = db.proposals(ns, status=ProposalStatus(status) if status else None)
    return {"proposals": [proposal_listing(p) for p in found]}


@router.post("/memory/propose")
async def propose_memory(
    body: dict,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
):
    from . import v1
    db = _db()
    agent = _agent(db, x_ember_agent_id, x_ember_token)
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
        namespace=body.get("namespace") or v1._proto(db).namespace,
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


@router.post("/failures")
async def report_failure(
    body: dict,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
):
    from . import v1
    db = _db()
    agent = _agent(db, x_ember_agent_id, x_ember_token)
    approach = body.get("approach", "")
    if not approach:
        raise HTTPException(400, "approach required")
    failure = Failure(
        namespace=body.get("namespace") or v1._proto(db).namespace,
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
    db = _db()
    _agent(db, x_ember_agent_id, x_ember_token)
    if approach:
        found = db.failures_for_approach(approach)
    else:
        found = db.failures()
    return {"failures": [f.to_dict() for f in found]}


legacy = APIRouter()


@legacy.post("/query")
async def query_records(body: dict):
    from . import _get_db, _get_protocol
    from . import _serialize_record
    records = _get_db().query(
        body.get("namespace") or _get_protocol().namespace,
        body.get("filters"), body.get("tags"), limit=body.get("limit", 100))
    return {"count": len(records), "records": [_serialize_record(r) for r in records]}
