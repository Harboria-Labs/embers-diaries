"""
Ember's Diaries — REST API Server

A FastAPI server that exposes the EmberDB interface over HTTP.
Run: uvicorn embers.api:app --port 9200
"""

import os
import asyncio
import logging
from typing import Optional
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager

from ..db import EmberDB
from ..core.record import EmberRecord
from ..core.annotation import Annotation
from ..core.types import RecordType, DeprecationReason
from ..identity.registry import AgentRegistry
from ..integration import MemoryProtocol
from ..maintenance import maintenance_loop

logger = logging.getLogger(__name__)


_db: Optional[EmberDB] = None
_protocol: Optional[MemoryProtocol] = None
_registry: Optional[AgentRegistry] = None


def _get_db() -> EmberDB:
    global _db
    if _db is None:
        store_path = os.environ.get("EMBER_STORE", "./ember_store")
        _db = EmberDB.connect(store_path)
    return _db


def _get_protocol() -> MemoryProtocol:
    global _protocol
    if _protocol is None:
        _protocol = MemoryProtocol(_get_db())
    return _protocol


def _get_registry() -> AgentRegistry:
    global _registry
    if _registry is None:
        _registry = AgentRegistry(_get_db())
    return _registry


def _legacy_agent(request: Request):
    """Return the authenticated agent stamped by the legacy API middleware."""
    agent = getattr(request.state, "ember_agent", None)
    if agent is None:
        # This should only be reachable when a route is called directly in
        # Python instead of through FastAPI's middleware stack.
        raise HTTPException(401, "X-Ember-Agent-Id and X-Ember-Token required")
    return agent


@asynccontextmanager
async def lifespan(app: FastAPI):
    _get_db()

    # Maintenance scheduler — OFF by default (see maintenance.py's module
    # docstring for why: it must be an explicit opt-in, not something that
    # silently starts acting on memories the moment the server boots).
    # Set EMBER_MAINTENANCE_INTERVAL_SECONDS to a positive integer to
    # enable; EMBER_MAINTENANCE_NAMESPACES is a comma-separated list
    # (defaults to just "memories", the same default MemoryProtocol itself
    # uses everywhere else).
    task = None
    interval = int(os.environ.get("EMBER_MAINTENANCE_INTERVAL_SECONDS", "0") or "0")
    if interval > 0:
        namespaces = [n.strip() for n in
                      os.environ.get("EMBER_MAINTENANCE_NAMESPACES", "memories").split(",")
                      if n.strip()]
        logger.info("maintenance scheduler enabled: every %ss, namespaces=%s",
                    interval, namespaces)
        task = asyncio.create_task(
            maintenance_loop(_get_protocol(), namespaces, interval))

    yield

    if task is not None:
        task.cancel()


app = FastAPI(
    title="Ember's Diaries API",
    version="0.2.0",
    description="Cognitive database engine for AI memory systems. Nothing is ever deleted.",
    lifespan=lifespan,
)

# The legacy routes predate the authenticated /v1 surface. Keep the public
# bootstrap/liveness endpoints useful, but put every stateful legacy route
# behind the same persisted agent identity and token used by /v1.
_LEGACY_PROTECTED_PREFIXES = (
    "/records", "/namespaces", "/search", "/query", "/graph",
    "/memory", "/timeline",
)
_PUBLIC_PATHS = {"/health", "/docs", "/redoc", "/openapi.json"}


def _is_legacy_protected(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/")
               for prefix in _LEGACY_PROTECTED_PREFIXES)


@app.middleware("http")
async def require_legacy_agent(request: Request, call_next):
    path = request.url.path.rstrip("/") or "/"
    # /v1 owns its route-level auth, while MCP authenticates each tool call.
    # Registration is intentionally public so a new client can bootstrap an
    # identity; health/docs expose no memory data.
    if (path in _PUBLIC_PATHS or path.startswith("/v1")
            or path == "/mcp"):
        return await call_next(request)
    if not _is_legacy_protected(path):
        return await call_next(request)

    agent_id = request.headers.get("x-ember-agent-id")
    token = request.headers.get("x-ember-token")
    if not agent_id or not token:
        return JSONResponse(
            {"detail": "X-Ember-Agent-Id and X-Ember-Token required"},
            status_code=401,
            headers={"WWW-Authenticate": "Ember"},
        )
    try:
        request.state.ember_agent = _get_registry().authenticate(agent_id, token)
    except PermissionError as exc:
        return JSONResponse(
            {"detail": str(exc)},
            status_code=401,
            headers={"WWW-Authenticate": "Ember"},
        )
    return await call_next(request)


# Browser access is opt-in. A wildcard origin would allow any website to make
# authenticated requests with a caller's Ember token. Configure a comma-
# separated allow-list only when browser clients are explicitly required.
_cors_origins = [origin.strip() for origin in
                 os.environ.get("EMBER_CORS_ORIGINS", "").split(",")
                 if origin.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Ember-Agent-Id", "X-Ember-Token"],
    )

from .v1 import router as v1_router
from .mcp_http import router as mcp_router
app.include_router(v1_router)
app.include_router(mcp_router)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.2.0"}


@app.post("/records")
async def write_record(request: Request, body: dict):
    db = _get_db()
    agent = _legacy_agent(request)
    record = EmberRecord(
        namespace=body.get("namespace", "default"),
        record_type=RecordType(body.get("record_type", "document")),
        data=body.get("data", {}),
        tags=body.get("tags", []),
        confidence=body.get("confidence", 1.0),
        decay_rate=body.get("decay_rate", 0.01),
        written_by=agent.agent_id,
        agent_id=agent.agent_id,
    )
    record_id = db.write(record)
    return {"id": record_id, "namespace": record.namespace}


@app.get("/records/{record_id}")
async def get_record(record_id: str,
                     include_deprecated: bool = False,
                     include_superseded: bool = False):
    db = _get_db()
    record = db.get(record_id, include_deprecated, include_superseded)
    if record is None:
        raise HTTPException(404, "Record not found")
    return _serialize_record(record)


@app.put("/records/{record_id}")
async def update_record(request: Request, record_id: str, body: dict):
    db = _get_db()
    agent = _legacy_agent(request)
    if not db.exists(record_id):
        raise HTTPException(404, "Record not found")
    new_data = body.get("data", {})
    new_id, old_id = db.update(record_id, new_data, agent.agent_id,
                                agent_id=agent.agent_id)
    return {"new_id": new_id, "old_id": old_id}


@app.delete("/records/{record_id}")
async def deprecate_record(request: Request, record_id: str, body: dict = {}):
    db = _get_db()
    agent = _legacy_agent(request)
    if not db.exists(record_id):
        raise HTTPException(404, "Record not found")
    reason = body.get("reason", "")
    db.deprecate(record_id, DeprecationReason.MANUAL, reason, agent.agent_id)
    return {"status": "deprecated", "id": record_id}


@app.get("/records/{record_id}/history")
async def get_history(record_id: str):
    db = _get_db()
    history = db.get_history(record_id)
    return {"history": [_serialize_record(r) for r in history]}


@app.get("/records/{record_id}/current")
async def get_current(record_id: str):
    db = _get_db()
    record = db.get_current(record_id)
    if record is None:
        raise HTTPException(404, "Record not found")
    return _serialize_record(record)


@app.post("/records/{record_id}/annotate")
async def annotate_record(request: Request, record_id: str, body: dict):
    db = _get_db()
    agent = _legacy_agent(request)
    if not db.exists(record_id):
        raise HTTPException(404, "Record not found")
    ann = Annotation(
        content=body.get("content", ""),
        context=body.get("context", ""),
        annotation_type=body.get("type", "note"),
        written_by=agent.agent_id,
        tags=body.get("tags", []),
    )
    ann_id = db.annotate(record_id, ann)
    return {"annotation_id": ann_id, "record_id": record_id}


@app.get("/namespaces")
async def list_namespaces():
    db = _get_db()
    return {"namespaces": db.list_namespaces()}


@app.get("/namespaces/{namespace}")
async def get_namespace(namespace: str,
                        limit: int = Query(default=100, le=1000),
                        include_deprecated: bool = False):
    db = _get_db()
    records = db.get_namespace(namespace, include_deprecated, limit=limit)
    return {"namespace": namespace, "count": len(records),
            "records": [_serialize_record(r) for r in records]}


@app.get("/search")
async def search(q: str = Query(..., min_length=1),
                 namespace: Optional[str] = None,
                 top_k: int = Query(default=10, le=100)):
    db = _get_db()
    results = db.search(q, namespace, top_k)
    return {"query": q, "results": [
        {"record": _serialize_record(r), "score": round(s, 4)}
        for r, s in results
    ]}


@app.post("/query")
async def query_records(body: dict):
    db = _get_db()
    namespace = body.get("namespace", "default")
    filters = body.get("filters")
    tags = body.get("tags")
    limit = body.get("limit", 100)
    records = db.query(namespace, filters, tags, limit=limit)
    return {"count": len(records), "records": [_serialize_record(r) for r in records]}


@app.post("/graph/link")
async def link_records(request: Request, body: dict):
    db = _get_db()
    _legacy_agent(request)
    from_id = body.get("from_id", "")
    to_id = body.get("to_id", "")
    edge_type = body.get("edge_type", "relates_to")
    weight = body.get("weight", 1.0)
    if not from_id or not to_id:
        raise HTTPException(400, "from_id and to_id required")
    ok = db.link(from_id, to_id, edge_type, weight)
    if not ok:
        raise HTTPException(404, "One or both records not found")
    return {"status": "linked", "from": from_id, "to": to_id, "edge_type": edge_type}


@app.get("/graph/neighbors/{record_id}")
async def get_neighbors(record_id: str, depth: int = 1):
    db = _get_db()
    neighbors = db.neighbors(record_id, depth=depth)
    return {"record_id": record_id, "depth": depth,
            "neighbors": [_serialize_record(r) for r in neighbors]}


@app.post("/memory/remember")
async def remember(request: Request, body: dict):
    protocol = _get_protocol()
    agent = _legacy_agent(request)
    content = body.get("content", "")
    if not content:
        raise HTTPException(400, "content required")
    record_id = protocol.remember(
        content,
        tags=body.get("tags"),
        confidence=body.get("confidence", 1.0),
        namespace=body.get("namespace"),
        written_by=agent.agent_id,
        agent_id=agent.agent_id,
    )
    return {"id": record_id, "status": "remembered"}


@app.post("/memory/recall")
async def recall(body: dict):
    protocol = _get_protocol()
    query = body.get("query", "")
    if not query:
        raise HTTPException(400, "query required")
    result = protocol.recall(
        query,
        top_k=body.get("top_k", 10),
        namespace=body.get("namespace"),
        format=body.get("format", "structured"),
    )
    return {"query": query, "memories": result}


@app.post("/memory/reflect")
async def reflect(request: Request, body: dict = {}):
    protocol = _get_protocol()
    _legacy_agent(request)
    annotations = protocol.reflect(namespace=body.get("namespace"))
    return {"reflections": len(annotations),
            "annotations": [{"content": a.content, "type": a.annotation_type} for a in annotations]}


@app.post("/memory/consolidate")
async def consolidate(request: Request, body: dict = {}):
    protocol = _get_protocol()
    _legacy_agent(request)
    new_ids = protocol.consolidate(namespace=body.get("namespace"))
    return {"consolidated": len(new_ids), "new_record_ids": new_ids}


@app.get("/memory/conflicts")
async def get_conflicts():
    protocol = _get_protocol()
    return {"conflicts": protocol.get_unresolved_conflicts()}


@app.get("/memory/stats")
async def memory_stats():
    protocol = _get_protocol()
    return protocol.stats()


@app.get("/timeline/{namespace}")
async def get_timeline(namespace: str, limit: int = Query(default=50, le=500)):
    db = _get_db()
    records = db.timeline(namespace, limit=limit)
    return {"namespace": namespace, "count": len(records),
            "records": [_serialize_record(r) for r in records]}


def _serialize_record(record: EmberRecord) -> dict:
    return {
        "id": record.id,
        "namespace": record.namespace,
        "record_type": record.record_type.value,
        "data": record.data,
        "tags": record.tags,
        "confidence": record.confidence,
        "decay_rate": record.decay_rate,
        "written_by": record.written_by,
        "agent_id": record.agent_id,
        "session_id": record.session_id,
        "created_at": record.created_at.isoformat(),
        "is_active": record.is_active,
        "is_current": record.is_current,
        "supersedes": record.supersedes,
        "superseded_by": record.superseded_by,
        "annotations_count": len(record.annotations),
    }
