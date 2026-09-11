from .session_auth import install as install_session_auth
from .room_wire import install as install_room_wire
from .conflict_surface import install as install_conflict_hints
from .server import main

install_session_auth()
install_room_wire()
install_conflict_hints()

if __name__ == "__main__":
    main()
