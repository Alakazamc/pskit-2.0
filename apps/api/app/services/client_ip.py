"""Resolve a client address only across explicitly trusted reverse proxies."""

from ipaddress import IPv6Address, ip_address, ip_network


def _canonical_ip(value: str) -> str:
    address = ip_address(value.strip())
    if isinstance(address, IPv6Address) and address.ipv4_mapped is not None:
        return str(address.ipv4_mapped)
    return str(address)


class TrustedClientIpResolver:
    """Accept the private client-IP header only from exact configured networks."""

    def __init__(
        self,
        trusted_proxy_cidrs: tuple[str, ...],
        header: str = "X-PSKit-Client-IP",
    ) -> None:
        try:
            self._trusted = tuple(ip_network(value, strict=False) for value in trusted_proxy_cidrs)
        except ValueError as exc:
            raise ValueError("Invalid trusted proxy CIDR") from exc
        if not header or any(character.isspace() for character in header):
            raise ValueError("Trusted client IP header is invalid")
        self.header = header

    def resolve(self, peer: str | None, asserted: str | None) -> str:
        """Return a canonical address, ignoring untrusted or malformed assertions."""
        try:
            if not peer:
                raise ValueError
            peer_address = ip_address(peer.strip())
        except ValueError as exc:
            raise ValueError("A valid socket peer IP is required") from exc
        canonical_peer = _canonical_ip(str(peer_address))
        trusted = any(peer_address in network for network in self._trusted)
        if not trusted or not asserted:
            return canonical_peer
        try:
            return _canonical_ip(asserted)
        except ValueError:
            return canonical_peer
