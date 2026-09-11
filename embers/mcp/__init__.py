from .server import EmberMCP, TOOLS, main
from .room_wire import install

install()

__all__ = ["EmberMCP", "TOOLS", "main"]
