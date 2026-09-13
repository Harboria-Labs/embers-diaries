from .server import EmberMCP, TOOLS, main
from .session_auth import install as install_session_auth
from .room_wire import install as install_room_wire
from .conflict_surface import install as install_conflict_surface
from .lobby_surface import install as install_lobby
from .session_collab import install as install_session_collab
from ..db import EmberDB
from ..db_feedback import bind as bind_feedback

bind_feedback(EmberDB)
install_session_auth()
install_room_wire()
install_conflict_surface()
install_lobby()
install_session_collab()

__all__ = ["EmberMCP", "TOOLS", "main"]
