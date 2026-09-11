from .server import EmberMCP, TOOLS, main
from .session_auth import install as install_session_auth
from .room_wire import install as install_room_wire
from ..integration.conflict_policy import install_mcp as install_conflict_hints

install_session_auth()
install_room_wire()
install_conflict_hints()

__all__ = ["EmberMCP", "TOOLS", "main"]
