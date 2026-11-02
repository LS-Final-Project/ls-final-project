"""Real loopback boundary: reuse closed connections, never an active listener."""

import socket

import pytest

from scripts.local_business.stack import check_web_port


def listener():
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen()
    return server


def test_closed_connection_time_wait_does_not_block_restart():
    with listener() as server:
        port = server.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port)) as client:
            conn, _ = server.accept()
            with conn:
                conn.shutdown(socket.SHUT_WR)  # Server is the active closer.
                assert client.recv(1) == b""
    check_web_port(port)


def test_existing_listener_is_still_rejected():
    with listener() as server:
        with pytest.raises(ValueError, match="LOCAL_WEB_PORT_UNAVAILABLE"):
            check_web_port(server.getsockname()[1])
