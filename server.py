"""
Chat Server with Exclusive Voice
=================================
A multithreaded TCP chat server that relays text messages and voice data
between clients, while enforcing that only one client may "talk" (transmit
voice) at any given time.

Run:
    python server.py
"""

import os
import socket
import threading
import logging
from datetime import datetime

# --------------------------------------------------------------------------- #
# Configuration (override via environment variables if needed)
# --------------------------------------------------------------------------- #
HOST = os.environ.get("CHAT_HOST", "0.0.0.0")
PORT = int(os.environ.get("CHAT_PORT", 5050))

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("chat-server")

# --------------------------------------------------------------------------- #
# Shared state
# --------------------------------------------------------------------------- #
clients: dict[socket.socket, str] = {}
clients_lock = threading.Lock()

talking_user: str | None = None
talking_lock = threading.Lock()


def broadcast(message: str, exclude_client: socket.socket | None = None) -> None:
    """Send a newline-terminated message to all connected clients."""
    dead_clients = []
    with clients_lock:
        targets = list(clients.items())

    for client, _nickname in targets:
        if client is exclude_client:
            continue
        try:
            client.sendall((message + "\n").encode("utf-8"))
        except OSError:
            dead_clients.append(client)

    for client in dead_clients:
        remove_client(client)


def remove_client(client: socket.socket) -> None:
    """Remove a client, notify others, and release the mic if they held it."""
    global talking_user

    with clients_lock:
        nickname = clients.pop(client, None)

    if nickname is None:
        return  # already removed

    log.info("%s disconnected.", nickname)
    broadcast(f"SERVER: {nickname} has left the chatroom.")
    update_user_list()

    with talking_lock:
        if talking_user == nickname:
            talking_user = None
            broadcast(f"STATUS:STOP:{nickname}")

    try:
        client.close()
    except OSError:
        pass


def update_user_list() -> None:
    """Broadcast the current list of connected nicknames."""
    with clients_lock:
        user_list = ",".join(clients.values())
    broadcast(f"USERLIST:{user_list}")


def read_line(client_socket: socket.socket, buffer: str) -> tuple[str, str]:
    """Block until a full newline-terminated line is available."""
    while "\n" not in buffer:
        data = client_socket.recv(1024)
        if not data:
            raise ConnectionError("client closed the connection")
        buffer += data.decode("utf-8", errors="ignore")
    line, buffer = buffer.split("\n", 1)
    return line.strip(), buffer


def handle_status_message(client_socket: socket.socket, nickname: str, parts: list[str]) -> None:
    """Process a STATUS:<START|STOP>:<user> control message."""
    global talking_user

    if len(parts) < 3:
        return
    _, action, user = parts

    if action == "START":
        with talking_lock:
            if talking_user is None:
                talking_user = user
                broadcast(f"STATUS:START:{user}")
            elif user != talking_user:
                try:
                    client_socket.sendall(b"STATUS:BUSY\n")
                except OSError:
                    remove_client(client_socket)

    elif action == "STOP":
        with talking_lock:
            if talking_user == user:
                talking_user = None
                broadcast(f"STATUS:STOP:{user}")


def handle_client(client_socket: socket.socket, address: tuple[str, int]) -> None:
    """Main per-client loop: registers the nickname, then relays messages."""
    buffer = ""
    nickname = None

    try:
        nickname, buffer = read_line(client_socket, buffer)
        if not nickname:
            client_socket.close()
            return

        with clients_lock:
            clients[client_socket] = nickname

        log.info("%s connected from %s.", nickname, address)
        broadcast(f"SERVER: {nickname} has joined the chatroom.", exclude_client=client_socket)
        update_user_list()

        while True:
            data = client_socket.recv(4096)
            if not data:
                break
            buffer += data.decode("utf-8", errors="ignore")

            while "\n" in buffer:
                message, buffer = buffer.split("\n", 1)
                message = message.strip()
                if not message:
                    continue

                if message.startswith("STATUS:"):
                    handle_status_message(client_socket, nickname, message.split(":", 2))
                elif message.startswith("MSG:"):
                    content = message[len("MSG:"):].strip()
                    broadcast(f"MSG:{nickname}: {content}")
                elif message.startswith("VOICE:"):
                    # Don't echo the speaker's own voice back to them.
                    broadcast(message, exclude_client=client_socket)
                else:
                    broadcast(message)

    except (ConnectionError, OSError) as exc:
        log.info("Connection with %s ended: %s", address, exc)
    except Exception:
        log.exception("Unexpected error handling client %s", address)
    finally:
        remove_client(client_socket)


def start_server() -> None:
    """Bind, listen, and accept incoming client connections forever."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    try:
        server.bind((HOST, PORT))
    except OSError as exc:
        log.error("Failed to bind server on %s:%s -> %s", HOST, PORT, exc)
        return

    server.listen()
    log.info("Chat server started on %s:%s at %s", HOST, PORT, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    try:
        while True:
            client_socket, address = server.accept()
            thread = threading.Thread(target=handle_client, args=(client_socket, address), daemon=True)
            thread.start()
    except KeyboardInterrupt:
        log.info("Shutting down the server.")
    finally:
        server.close()


if __name__ == "__main__":
    start_server()
