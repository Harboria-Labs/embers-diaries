from .server import EmberMCP, TOOLS, main
from .session_auth import install as install_session_auth
from .room_wire import install as install_room_wire

install_session_auth()
install_room_wire()

__all__ = ["EmberMCP", "TOOLS", "main"]
