from __future__ import annotations

from ipaddress import ip_address, ip_network

from starlette.requests import Request

from app.config import get_settings


def _trusted_proxy_networks() -> tuple:
    networks = []
    for value in get_settings().trusted_proxy_ips.split(","):
        value = value.strip()
        if not value:
            continue
        try:
            networks.append(ip_network(value, strict=False))
        except ValueError:
            continue
    return tuple(networks)


def client_ip(request: Request) -> str:
    """Return a forwarded address only when a configured proxy supplied it."""

    peer = request.client.host if request.client else "unknown"
    try:
        peer_address = ip_address(peer)
    except ValueError:
        return peer
    if any(peer_address in network for network in _trusted_proxy_networks()):
        forwarded = request.headers.get("x-real-ip", "").strip()
        if not forwarded:
            forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        try:
            return str(ip_address(forwarded))
        except ValueError:
            pass
    return str(peer_address)
