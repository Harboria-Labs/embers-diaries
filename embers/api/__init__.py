"""
Ember's Diaries — REST API Server (Phase 5)

A FastAPI server that exposes the full EmberDB interface over HTTP.
Run: uvicorn embers.api.server:app --port 9200
"""

import os
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from ..db import EmberDB
from ..core.record import EmberRecord
from ..core.annotation import Annotation
from ..core.types import RecordType, DeprecationReason
from ..integration import MemoryProtocol


_db: Optional[EmberDB] = None
_protocol: Optional[MemoryProtocol] = None


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    _get_db()
    yield


app = FastAPI(
    title="Ember's Diaries API",
    version="0.2.0",
    description="Cognitive database engine for AI memory systems. Nothing is ever deleted.",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    db = _get_db()
    return {"status": "ok", "version": "0.2.0", "stats": db.stats()}


# ── Records ──────────────────────────────────────────────────────────────────

@app.post("/records")
async def write_record(body: dict):
    """Write a new record. Returns the record ID."""
    db = _get_db()
    record = EmberRecord(
        namespace=body.get("namespace", "default"),
        record_type=RecordType(body.get("record_type", "document")),
        data=body.get("data", {}),
        tags=body.get("tags", []),
        confidence=body.get("confidence", 1.0),
        decay_rate=body.get("decay_rate", 0.01),
        written_by=body.get("written_by", "api"),
    )
    record_id = db.write(record)
    return {"id": record_id, "namespace": record.namespace}


@app.get("/records/{record_id}")
async def get_record(record_id: str,
                     include_deprecated: bool = False,
                     include_superseded: bool = False):
    """Get a record by ID."""
    db = _get_db()
    record = db.get(record_id, include_deprecated, include_superseded)
    if record is None:
        raise HTTPException(404, "Record not found")
    return _serialize_record(record)


@app.put("/records/{record_id}")
async def update_record(record_id: str, body: dict):
    """Update a record (creates new version, preserves old)."""
    db = _get_db()
    if not db.exists(record_id):
        raise HTTPException(404, "Record not found")
    new_data = body.get("data", {})
    written_by = body.get("written_by", "api")
    new_id, old_id = db.update(record_id, new_data, written_by)
    return {"new_id": new_id, "old_id": old_id}


@app.delete("/records/{record_id}")
async def deprecate_record(record_id: str, body: dict = {}):
    """Deprecate a record (never deletes)."""
    db = _get_db()
    if not db.exists(record_id):
        raise HTTPException(404, "Record not found")
    reason = body.get("reason", "")
    db.deprecate(record_id, DeprecationReason.MANUAL, reason, body.get("written_by", "api"))
    return {"status": "deprecated", "id": record_id}


@app.get("/records/{record_id}/history")
async def get_history(record_id: str):
    """Full version history of a record."""
    db = _get_db()
    history = db.get_history(record_id)
    return {"history": [_serialize_record(r) for r in history]}


@app.get("/records/{record_id}/current")
async def get_current(record_id: str):
    """Get the latest version following supersession chain."""
    db = _get_db()
    record = db.get_current(record_id)
    if record is None:
        raise HTTPException(404, "Record not found")
    return _serialize_record(record)


# ── Annotations ──────────────────────────────────────────────────────────────

@app.post("/records/{record_id}/annotate")
async def annotate_record(record_id: str, body: dict):
    """Add an annotation to a record (never modifies the original)."""
    db = _get_db()
    if not db.exists(record_id):
        raise HTTPException(404, "Record not found")
    ann = Annotation(
        content=body.get("content", ""),
        context=body.get("context", ""),
        annotation_type=body.get("type", "note"),
        written_by=body.get("written_by", "api"),
        tags=body.get("tags", []),
    )
    ann_id = db.annotate(record_id, ann)
    return {"annotation_id": ann_id, "record_id": record_id}


# ── Namespaces ───────────────────────────────────────────────────────────────

@app.get("/namespaces")
async def list_namespaces():
    """List all namespaces."""
    db = _get_db()
    return {"namespaces": db.list_namespaces()}


@app.get("/namespaces/{namespace}")
async def get_namespace(namespace: str,
                        limit: int = Query(default=100, le=1000),
                        include_deprecated: bool = False):
    """Get all records in a namespace."""
    db = _get_db()
    records = db.get_namespace(namespace, include_deprecated, limit=limit)
    return {"namespace": namespace, "count": len(records),
            "records": [_serialize_record(r) for r in records]}


# ── Search & Query ───────────────────────────────────────────────────────────

@app.get("/search")
async def search(q: str = Query(..., min_length=1),
                 namespace: Optional[str] = None,
                 top_k: int = Query(default=10, le=100)):
    """Full-text BM25 search."""
    db = _get_db()
    results = db.search(q, namespace, top_k)
    return {"query": q, "results": [
        {"record": _serialize_record(r), "score": round(s, 4)}
        for r, s in results
    ]}


@app.post("/query")
async def query_records(body: dict):
    """Query records with filters, tags, namespace."""
    db = _get_db()
    namespace = body.get("namespace", "default")
    filters = body.get("filters")
    tags = body.get("tags")
    limit = body.get("limit", 100)
    records = db.query(namespace, filters, tags, limit=limit)
    return {"count": len(records), "records": [_serialize_record(r) for r in records]}


# ── Graph ────────────────────────────────────────────────────────────────────

@app.post("/graph/link")
async def link_records(body: dict):
    """Create a graph edge between two records."""
    db = _get_db()
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
    """Get graph neighbors."""
    db = _get_db()
    neighbors = db.neighbors(record_id, depth=depth)
    return {"record_id": record_id, "depth": depth,
            "neighbors": [_serialize_record(r) for r in neighbors]}


# ── Memory Protocol (LLM Integration) ────────────────────────────────────────

@app.post("/memory/remember")
async def remember(body: dict):
    """Store a new memory with auto-embedding and conflict detection."""
    protocol = _get_protocol()
    content = body.get("content", "")
    if not content:
        raise HTTPException(400, "content required")
    record_id = protocol.remember(
        content,
        tags=body.get("tags"),
        confidence=body.get("confidence", 1.0),
        namespace=body.get("namespace"),
    )
    return {"id": record_id, "status": "remembered"}


@app.post("/memory/recall")
async def recall(body: dict):
    """Retrieve relevant memories for a query."""
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
async def reflect(body: dict = {}):
    """Run a cognitive reflection cycle."""
    protocol = _get_protocol()
    annotations = protocol.reflect(namespace=body.get("namespace"))
    return {"reflections": len(annotations),
            "annotations": [{"content": a.content, "type": a.annotation_type} for a in annotations]}


@app.post("/memory/consolidate")
async def consolidate(body: dict = {}):
    """Run memory consolidation."""
    protocol = _get_protocol()
    new_ids = protocol.consolidate(namespace=body.get("namespace"))
    return {"consolidated": len(new_ids), "new_record_ids": new_ids}


@app.get("/memory/conflicts")
async def get_conflicts():
    """Get unresolved memory conflicts."""
    protocol = _get_protocol()
    return {"conflicts": protocol.get_unresolved_conflicts()}


@app.get("/memory/stats")
async def memory_stats():
    """Get memory system statistics."""
    protocol = _get_protocol()
    return protocol.stats()


# ── Timeline ─────────────────────────────────────────────────────────────────

@app.get("/timeline/{namespace}")
async def get_timeline(namespace: str, limit: int = Query(default=50, le=500)):
    """Get records in chronological order."""
    db = _get_db()
    records = db.timeline(namespace, limit=limit)
    return {"namespace": namespace, "count": len(records),
            "records": [_serialize_record(r) for r in records]}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _serialize_record(record: EmberRecord) -> dict:
    """Serialize an EmberRecord to a JSON-safe dict."""
    return {
        "id": record.id,
        "namespace": record.namespace,
        "record_type": record.record_type.value,
        "data": record.data,
        "tags": record.tags,
        "confidence": record.confidence,
        "decay_rate": record.decay_rate,
        "written_by": record.written_by,
        "created_at": record.created_at.isoformat(),
        "is_active": record.is_active,
        "is_current": record.is_current,
        "supersedes": record.supersedes,
        "superseded_by": record.superseded_by,
        "annotations_count": len(record.annotations),
    }
