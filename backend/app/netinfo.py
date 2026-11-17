"""Where is this server reachable on your local network (for the phone link and alert links)."""
import socket
from typing import Optional

from . import config


def lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))        # no packet is sent; this just picks the right interface
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def base_url(port: Optional[int] = None) -> str:
    if config.BASE_URL:
        return config.BASE_URL.rstrip("/")
    return f"http://{lan_ip()}:{port or config.PORT}"
