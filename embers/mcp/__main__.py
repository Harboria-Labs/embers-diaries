from .session_auth import install as install_session_auth
from .room_wire import install as install_room_wire
from .server import main

install_session_auth()
install_room_wire()

if __name__ == "__main__":
    main()
