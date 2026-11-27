"""JSON-RPC MCP over HTTP. Same EmberMCP instance family as stdio.

One shared EmberMCP for every HTTP client. Auth is in the JSON-RPC
args: session_id after start_session, or agent_id+token to start.
This handler does not extract or remember a session from the socket.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from ..mcp.session_auth import install as install_session_auth
from ..mcp.room_wire import install as install_room_wire
from ..mcp.conflict_surface import install as install_conflict_surface
from ..mcp.lobby_surface import install as install_lobby
from ..mcp.session_collab import install as install_session_collab
from ..mcp.server import EmberMCP, SUPPORTED_PROTOCOLS

install_session_auth()
install_room_wire()
install_conflict_surface()
install_lobby()
install_session_collab()

router = APIRouter()
_mcp: EmberMCP | None = None


def _mcp_server() -> EmberMCP:
    global _mcp
    if _mcp is None:
        from . import _get_db
        _mcp = EmberMCP(db=_get_db())
    return _mcp


def _origin_allowed(request: Request) -> bool:
    origin = request.headers.get("origin")
    if origin is None:
        return True
    parsed = urlsplit(origin)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.path or parsed.query or parsed.fragment:
        return False
    if parsed.netloc.lower() == request.url.netloc.lower():
        return True
    from . import _get_config
    return origin in _get_config().api.cors_origins


@router.get("/mcp")
async def mcp_info(request: Request):
    if not _origin_allowed(request):
        return JSONResponse({"detail": "Invalid Origin"}, status_code=403)
    # This server has no server-initiated messages, so it has no GET SSE stream.
    return Response(status_code=405, headers={"Allow": "POST"})


@router.post("/mcp")
async def mcp_rpc(request: Request):
    if not _origin_allowed(request):
        return JSONResponse({"detail": "Invalid Origin"}, status_code=403)
    version = request.headers.get("mcp-protocol-version")
    if version is not None and version not in SUPPORTED_PROTOCOLS:
        return JSONResponse({"detail": "Unsupported MCP-Protocol-Version"}, status_code=400)
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        return JSONResponse({"detail": "Content-Type must be application/json"}, status_code=415)
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"detail": "Invalid JSON"}, status_code=400)
    if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":
        return JSONResponse({"detail": "Expected one JSON-RPC 2.0 message"}, status_code=400)
    if "method" not in body and ("result" in body or "error" in body):
        return Response(status_code=202)
    if not isinstance(body.get("method"), str):
        return JSONResponse({"detail": "JSON-RPC method required"}, status_code=400)
    reply = _mcp_server().handle(body)
    if reply is None:
        return Response(status_code=202)
    return JSONResponse(reply)
