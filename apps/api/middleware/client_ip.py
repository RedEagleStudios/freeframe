"""The visitor's IP address, as the rate limits key it."""
from fastapi import Request

from ..config import settings


def client_ip(request: Request) -> str:
    """CLIENT_IP_HEADER when configured and present, else X-Real-Ip, else the peer.

    X-Real-Ip is set by Traefik from the connection it accepted. Behind a CDN that
    is the CDN's edge, so every visitor routed through one edge would share a
    rate-limit bucket; CLIENT_IP_HEADER names the header the CDN puts the real
    address in. X-Forwarded-For is never read: a client can prepend to it.
    """
    header = settings.client_ip_header.strip()
    if header:
        value = request.headers.get(header, "").strip()
        if value:
            return value
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")
