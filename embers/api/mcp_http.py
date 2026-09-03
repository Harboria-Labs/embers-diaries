"""JSON-RPC MCP over HTTP. Same EmberMCP instance family as stdio."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..mcp.server import EmberMCP

router = APIRouter()
_mcp: EmberMCP | None = None


def _mcp_server() -> EmberMCP:
    global _mcp
    if _mcp is None:
        from . import _get_db
        _mcp = EmberMCP(db=_get_db())
    return _mcp


@router.get("/mcp")
async def mcp_info():
    return {
        "transport": "http",
        "protocol": "2024-11-05",
        "endpoint": "POST /mcp",
        "tools": "tools/list after initialize",
    }


@router.post("/mcp")
async def mcp_rpc(request: Request):
    body = await request.json()
    if isinstance(body, list):
        replies = []
        for item in body:
            reply = _mcp_server().handle(item)
            if reply is not None:
                replies.append(reply)
        return JSONResponse(replies)
    reply = _mcp_server().handle(body)
    if reply is None:
        return JSONResponse({"ok": True})
    return JSONResponse(reply)
