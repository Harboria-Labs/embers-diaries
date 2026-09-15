"""Provider-neutral /v1 HTTP surface. Calls EmberDB + AgentRegistry only."""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from ..core.evidence import Evidence
from ..core.errors import ConcurrentModificationError
from ..core.failure import Failure
from ..core.proposal import MemoryProposal
from ..core.types import SourceType
from ..identity.registry import AgentRegistry
from ..integration import MemoryProtocol
from ..integration.conflict_protocol import (
    conflicts_for as conflict_contract_for,
    map_conflict as conflict_contract_map,
    open_conflicts as conflict_contract_open,
    transition_conflict as conflict_contract_transition,
)

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


def require_namespace(db, namespace: str, agent_id: str,
                      operation: str = "read") -> None:
    """Apply the configured namespace ACL to an authenticated v1 caller."""
    try:
        db.require_namespace_access(namespace, agent_id, operation)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


def _lobby_context(db, agent, requested_session_id: str | None,
                   header_session_id: str | None):
    """Resolve and authorize the session that owns a lobby board."""
    session_id = requested_session_id or header_session_id
    if not session_id:
        raise HTTPException(400, "session_id required")
    session = db.get_session(session_id)
    if session is None:
        raise HTTPException(401, "unknown session")
    if session.status.value != "active":
        raise HTTPException(401, "session is not active")
    if session.agent_id != agent.agent_id:
        raise HTTPException(403, "session belongs to another agent")
    return session_id, session


def _lobby_store():
    # Import lazily: the API module is imported before the MCP adapter during
    # application construction, but both surfaces must share this one store.
    from ..mcp.lobby_surface import STORE
    return STORE


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
    protocol = _proto(db)
    namespace = body.get("namespace") or protocol.namespace
    require_namespace(db, namespace, agent.agent_id, "write")
    rid = protocol.remember(
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
    agent = require_agent(db, x_ember_agent_id, x_ember_token)
    query = body.get("query", "")
    if not query:
        raise HTTPException(400, "query required")
    protocol = _proto(db)
    namespace = body.get("namespace") or protocol.namespace
    require_namespace(db, namespace, agent.agent_id)
    result = protocol.recall(
        query,
        top_k=body.get("top_k", 10),
        namespace=body.get("namespace"),
        room=body.get("room"),
        format=body.get("format", "structured"),
    )
    return {"query": query, "memories": result}


@router.get("/memory/read/{record_id}")
async def memory_read(
    record_id: str,
    include_deprecated: bool = False,
    include_superseded: bool = False,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
):
    """Read one record through the same visibility rules as ``EmberDB.get``."""
    from . import _get_db, _serialize_record
    db = _get_db()
    agent = require_agent(db, x_ember_agent_id, x_ember_token)
    record = db.get(record_id, include_deprecated, include_superseded)
    if record is None:
        raise HTTPException(404, "Record not found")
    require_namespace(db, record.namespace, agent.agent_id)
    return _serialize_record(record)


@router.put("/memory/{record_id}")
async def memory_update(
    record_id: str,
    body: dict,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
):
    """CAS update; stale expected_hash returns a structured storage conflict."""
    from . import _get_db
    db = _get_db()
    agent = require_agent(db, x_ember_agent_id, x_ember_token)
    expected_hash = body.get("expected_hash")
    if not expected_hash:
        raise HTTPException(400, "expected_hash required")
    data = body.get("data")
    if not isinstance(data, dict):
        raise HTTPException(400, "data must be an object")
    record = db.get(
        record_id, include_deprecated=True, include_superseded=True)
    if record is None:
        raise HTTPException(404, "Record not found")
    try:
        db.require_namespace_access(record.namespace, agent.agent_id, "read")
        db.require_namespace_access(record.namespace, agent.agent_id, "write")
        new_id, old_id = db.update(
            record_id,
            data,
            written_by=agent.agent_id,
            agent_id=agent.agent_id,
            session_id=body.get("session_id"),
            creation_reason=body.get("creation_reason"),
            expected_hash=expected_hash,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ConcurrentModificationError as exc:
        return JSONResponse(status_code=409, content=exc.to_dict())
    current = db.get(new_id)
    return {
        "record_id": new_id,
        "superseded_record_id": old_id,
        "content_hash": current.content_hash,
        "version": current.version,
    }


@router.post("/memory/query")
async def memory_query(
    body: dict,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
):
    """Run an indexed document query without duplicating query behavior."""
    from . import _get_db, _serialize_record
    db = _get_db()
    agent = require_agent(db, x_ember_agent_id, x_ember_token)
    namespace = body.get("namespace", "default")
    require_namespace(db, namespace, agent.agent_id)
    records = db.query(
        namespace,
        body.get("filters"),
        body.get("tags"),
        limit=body.get("limit", 100),
        include_deprecated=body.get("include_deprecated", False),
        include_superseded=body.get("include_superseded", False),
    )
    return {
        "count": len(records),
        "records": [_serialize_record(record) for record in records],
    }


@router.get("/memory/search")
async def memory_search(
    q: str = Query(..., min_length=1),
    namespace: str | None = None,
    top_k: int = Query(default=10, ge=1, le=100),
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
):
    """Run the core full-text search and preserve its relevance ordering."""
    from . import _get_db, _serialize_record
    db = _get_db()
    agent = require_agent(db, x_ember_agent_id, x_ember_token)
    if namespace is not None:
        require_namespace(db, namespace, agent.agent_id)
    results = db.search(q, namespace, top_k)
    if namespace is None:
        results = [
            (record, score) for record, score in results
            if db.check_namespace_access(
                record.namespace, agent.agent_id, "read")
        ]
    return {
        "query": q,
        "results": [
            {"record": _serialize_record(record), "score": round(score, 4)}
            for record, score in results
        ],
    }


@router.get("/memory/history/{record_id}")
async def memory_history(
    record_id: str,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
):
    """Return the complete supersession chain, oldest version first."""
    from . import _get_db, _serialize_record
    db = _get_db()
    agent = require_agent(db, x_ember_agent_id, x_ember_token)
    root = db.get(
        record_id, include_deprecated=True, include_superseded=True)
    if root is not None:
        require_namespace(db, root.namespace, agent.agent_id)
    return {
        "history": [
            _serialize_record(record) for record in db.get_history(record_id)
        ],
    }


@router.get("/memory/graph/{record_id}")
async def memory_graph(
    record_id: str,
    depth: int = Query(default=1, ge=1),
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
):
    """Return graph neighbors up to ``depth``, matching ``ember_get_graph``."""
    from . import _get_db, _serialize_record
    db = _get_db()
    agent = require_agent(db, x_ember_agent_id, x_ember_token)
    root = db.get(
        record_id, include_deprecated=True, include_superseded=True)
    if root is not None:
        require_namespace(db, root.namespace, agent.agent_id)
    neighbors = db.neighbors(record_id, depth=depth)
    neighbors = [
        record for record in neighbors
        if db.check_namespace_access(record.namespace, agent.agent_id, "read")
    ]
    return {
        "record_id": record_id,
        "depth": depth,
        "neighbors": [_serialize_record(record) for record in neighbors],
    }


@router.post("/conflicts/map")
async def map_memory_conflict(
    body: dict,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
):
    """Map an authorized same-namespace semantic conflict."""
    from . import _get_db
    db = _get_db()
    agent = require_agent(db, x_ember_agent_id, x_ember_token)
    memory_a = body.get("memory_a")
    memory_b = body.get("memory_b")
    if not memory_a or not memory_b:
        raise HTTPException(400, "memory_a and memory_b required")
    try:
        return conflict_contract_map(
            db,
            memory_a=memory_a,
            memory_b=memory_b,
            actor_id=agent.agent_id,
            conflict_type=body.get("conflict_type", "semantic"),
            note=body.get("note", ""),
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/conflicts/open")
async def open_memory_conflicts(
    namespace: str = "memories",
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
):
    """Open and investigating conflicts visible in one namespace."""
    from . import _get_db
    db = _get_db()
    agent = require_agent(db, x_ember_agent_id, x_ember_token)
    try:
        conflicts = conflict_contract_open(
            db, namespace=namespace, actor_id=agent.agent_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return {
        "namespace": namespace,
        "count": len(conflicts),
        "conflicts": conflicts,
    }


@router.get("/memory/{memory_id}/conflicts")
async def conflicts_for_memory(
    memory_id: str,
    include_closed: bool = False,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
):
    """Mapped conflicts involving a memory, symmetrically on either side."""
    from . import _get_db
    db = _get_db()
    agent = require_agent(db, x_ember_agent_id, x_ember_token)
    try:
        conflicts = conflict_contract_for(
            db,
            memory_id=memory_id,
            include_closed=include_closed,
            actor_id=agent.agent_id,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {
        "memory_id": memory_id,
        "count": len(conflicts),
        "conflicts": conflicts,
    }


@router.post("/conflicts/{conflict_id}/resolve")
async def resolve_memory_conflict(
    conflict_id: str,
    body: dict,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
):
    """Append an authenticated conflict lifecycle decision."""
    from . import _get_db
    db = _get_db()
    agent = require_agent(db, x_ember_agent_id, x_ember_token)
    try:
        return conflict_contract_transition(
            db,
            conflict_id=conflict_id,
            actor_id=agent.agent_id,
            status=body.get("status", "resolved"),
            resolution=body.get("resolution", ""),
            winner_id=body.get("winner_id"),
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


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


@router.post("/lobby/publish")
async def lobby_publish(
    body: dict,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
    x_ember_session_id: str | None = Header(default=None),
):
    """Open a board on first publish, then add one ephemeral post."""
    from ..lobby.store import LobbyError
    from . import _get_db
    db = _get_db()
    agent = require_agent(db, x_ember_agent_id, x_ember_token)
    session_id, session = _lobby_context(
        db, agent, body.get("session_id"), x_ember_session_id)
    store = _lobby_store()
    try:
        if store.board_for_session(session_id) is None:
            store.open(
                session_id=session_id,
                agent_id=agent.agent_id,
                task=body.get("task", ""),
                namespace=body.get("namespace") or getattr(session, "namespace", "default"),
                room=body.get("room", ""),
            )
        return store.publish(
            session_id=session_id,
            agent_id=agent.agent_id,
            room=body.get("room", ""),
            post_type=body.get("type", ""),
            body=body.get("body", ""),
            approach=body.get("approach"),
        )
    except LobbyError as e:
        raise HTTPException(400, str(e)) from e


@router.get("/lobby/updates")
async def lobby_updates(
    session_id: str | None = None,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
    x_ember_session_id: str | None = Header(default=None),
):
    """Return the current board snapshot as poll-based lobby updates."""
    from . import _get_db
    db = _get_db()
    agent = require_agent(db, x_ember_agent_id, x_ember_token)
    sid, _session = _lobby_context(db, agent, session_id, x_ember_session_id)
    store = _lobby_store()
    board = store.board_for_session(sid)
    if board is None:
        return {"session_id": sid, "board": None, "updates": [], "posts": []}
    snapshot = store.snapshot(session_id=sid)
    return {
        "session_id": sid,
        "board": snapshot,
        "updates": snapshot["posts"],
        "posts": snapshot["posts"],
    }


@router.get("/lobby/status")
async def lobby_status(
    session_id: str | None = None,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
    x_ember_session_id: str | None = Header(default=None),
):
    """Return whether the authenticated session has an open board."""
    from . import _get_db
    db = _get_db()
    agent = require_agent(db, x_ember_agent_id, x_ember_token)
    sid, _session = _lobby_context(db, agent, session_id, x_ember_session_id)
    summary = _lobby_store().summary_for_session(sid)
    if summary is None:
        return {"session_id": sid, "status": "idle", "board": None}
    return {"session_id": sid, "board": summary, **summary}


@router.post("/lobby/promote")
async def lobby_promote(
    body: dict,
    x_ember_agent_id: str | None = Header(default=None),
    x_ember_token: str | None = Header(default=None),
    x_ember_session_id: str | None = Header(default=None),
):
    """Promote an ephemeral post through the existing MCP/core promotion path."""
    from ..lobby.store import LobbyError
    from ..mcp.lobby_surface import _promote
    from types import SimpleNamespace
    from . import _get_db
    db = _get_db()
    agent = require_agent(db, x_ember_agent_id, x_ember_token)
    session_id, _session = _lobby_context(
        db, agent, body.get("session_id"), x_ember_session_id)
    post_id = body.get("post_id")
    if not post_id:
        raise HTTPException(400, "post_id required")
    try:
        # _promote contains the single lobby-to-core implementation used by
        # ember_lobby; the facade supplies the same EmberDB instance.
        result = _promote(SimpleNamespace(db=db), agent, session_id, post_id)
        # Failure promotion is linked by EmberDB.report_failure itself. A
        # proposal is deliberately only staged by EmberDB.propose, so mirror
        # the MCP session-collaboration adapter and link it explicitly.
        proposal_id = result.get("proposal_id")
        if proposal_id:
            db.record_discovery(
                session_id, proposal_id, changed_by=agent.agent_id)
        return result
    except LobbyError as e:
        raise HTTPException(400, str(e)) from e


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
