"""JSON-RPC MCP over HTTP. Same EmberMCP instance family as stdio.

One shared EmberMCP for every HTTP client. Auth is in the JSON-RPC
args: session_id after start_session, or agent_id+token to start.
This handler does not extract or remember a session from the socket.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..mcp.session_auth import install as install_session_auth
from ..mcp.room_wire import install as install_room_wire
from ..mcp.conflict_surface import install as install_conflict_surface
from ..mcp.lobby_surface import install as install_lobby
from ..mcp.server import EmberMCP

install_session_auth()
install_room_wire()
install_conflict_surface()
install_lobby()

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
