"""Minimal systemd sd_notify via Unix datagram socket. No external dependencies."""
import logging
import os
import socket

logger = logging.getLogger(__name__)


def sd_notify(msg: str) -> bool:
    """Send notification to systemd. Returns True if sent, False if no socket."""
    sock_path = os.environ.get("NOTIFY_SOCKET")
    if not sock_path:
        return False
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            sock.sendto(msg.encode(), sock_path)
        finally:
            sock.close()
        return True
    except Exception:
        logger.warning("sd_notify failed: %s", msg, exc_info=True)
        return False


def notify_ready() -> None:
    sd_notify("READY=1")


def notify_watchdog() -> None:
    sd_notify("WATCHDOG=1")
