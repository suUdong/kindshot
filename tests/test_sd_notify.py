import os
import sys
import socket
import pytest
from kindshot.sd_notify import sd_notify, notify_ready, notify_watchdog

_skip_no_unix = pytest.mark.skipif(
    sys.platform == "win32", reason="Unix sockets not available on Windows"
)


def _bind_notify_server(sock_path: str) -> socket.socket:
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        server.bind(sock_path)
    except PermissionError:
        server.close()
        pytest.skip("Unix datagram socket bind not permitted in this environment")
    return server

@_skip_no_unix
def test_sd_notify_sends_to_socket(tmp_path):
    sock_path = str(tmp_path / "notify.sock")
    server = _bind_notify_server(sock_path)
    os.environ["NOTIFY_SOCKET"] = sock_path
    try:
        assert sd_notify("READY=1") is True
        data = server.recv(256)
        assert data == b"READY=1"
    finally:
        server.close()
        os.environ.pop("NOTIFY_SOCKET", None)

def test_sd_notify_no_socket():
    os.environ.pop("NOTIFY_SOCKET", None)
    assert sd_notify("READY=1") is False

@_skip_no_unix
def test_notify_ready(tmp_path):
    sock_path = str(tmp_path / "notify.sock")
    server = _bind_notify_server(sock_path)
    os.environ["NOTIFY_SOCKET"] = sock_path
    try:
        notify_ready()
        data = server.recv(256)
        assert data == b"READY=1"
    finally:
        server.close()
        os.environ.pop("NOTIFY_SOCKET", None)

@_skip_no_unix
def test_notify_watchdog(tmp_path):
    sock_path = str(tmp_path / "notify.sock")
    server = _bind_notify_server(sock_path)
    os.environ["NOTIFY_SOCKET"] = sock_path
    try:
        notify_watchdog()
        data = server.recv(256)
        assert data == b"WATCHDOG=1"
    finally:
        server.close()
        os.environ.pop("NOTIFY_SOCKET", None)
