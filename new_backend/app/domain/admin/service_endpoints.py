"""Resolve administrator-supplied MCP endpoints under a fail-closed network policy."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlsplit, urlunsplit

from app.contracts.tool_products import EndpointSnapshot

Resolver = Callable[..., list[tuple]]


class ApprovedEndpointPolicy:
    def __init__(
        self,
        network_zones: dict[str, list[str]],
        *,
        resolver: Resolver = socket.getaddrinfo,
    ) -> None:
        self.resolver = resolver
        self.network_zones: dict[str, tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]] = {}
        for name, cidrs in network_zones.items():
            if not isinstance(name, str) or not name or name == "public":
                raise ValueError("INVALID_MCP_NETWORK_ZONE")
            if not isinstance(cidrs, list) or not cidrs:
                raise ValueError("INVALID_MCP_NETWORK_ZONE")
            try:
                self.network_zones[name] = tuple(
                    ipaddress.ip_network(value, strict=True) for value in cidrs
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("INVALID_MCP_NETWORK_ZONE") from exc

    @staticmethod
    def _forbidden(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        return (
            address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_unspecified
            or address.is_reserved
        )

    def resolve(self, uri: str, network_zone: str) -> EndpointSnapshot:
        try:
            parsed = urlsplit(uri)
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except (TypeError, ValueError) as exc:
            raise ValueError("ENDPOINT_URI_INVALID") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or not parsed.path.startswith("/")
            or parsed.path.startswith("//")
        ):
            raise ValueError("ENDPOINT_URI_INVALID")
        normalized = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))
        try:
            resolved = self.resolver(
                parsed.hostname,
                port,
                type=socket.SOCK_STREAM,
            )
            addresses = sorted({ipaddress.ip_address(item[4][0]) for item in resolved}, key=str)
        except (OSError, TypeError, ValueError) as exc:
            raise ValueError("ENDPOINT_DNS_FAILED") from exc
        if not addresses or any(self._forbidden(address) for address in addresses):
            raise ValueError("ENDPOINT_ADDRESS_FORBIDDEN")

        private = [address for address in addresses if address.is_private]
        if private:
            networks = self.network_zones.get(network_zone)
            if networks is None or any(
                not any(address in network for network in networks) for address in addresses
            ):
                raise ValueError("ENDPOINT_PRIVATE_ADDRESS_NOT_APPROVED")
        elif network_zone != "public":
            raise ValueError("ENDPOINT_NETWORK_ZONE_MISMATCH")
        elif parsed.scheme != "https":
            raise ValueError("ENDPOINT_PUBLIC_HTTPS_REQUIRED")

        return EndpointSnapshot(
            uri=normalized,
            hostname=parsed.hostname,
            port=port,
            scheme=parsed.scheme,
            network_zone=network_zone,
            addresses=[str(address) for address in addresses],
        )
