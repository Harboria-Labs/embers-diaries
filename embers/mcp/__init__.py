from .server import EmberMCP, TOOLS, main
from .session_auth import install as install_session_auth
from .room_wire import install as install_room_wire
from .conflict_surface import install as install_conflict_surface
from .lobby_surface import install as install_lobby

install_session_auth()
install_room_wire()
install_conflict_surface()
install_lobby()

__all__ = ["EmberMCP", "TOOLS", "main"]
