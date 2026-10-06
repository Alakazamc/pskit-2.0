import pytest

from app.services.client_ip import TrustedClientIpResolver


def test_untrusted_peer_cannot_assert_client_ip():
    resolver = TrustedClientIpResolver(("127.0.0.1/32", "::1/128"))

    assert resolver.resolve("203.0.113.7", "198.51.100.4") == "203.0.113.7"


def test_trusted_proxy_can_assert_normalized_client_ip():
    resolver = TrustedClientIpResolver(("127.0.0.1/32",))

    assert resolver.resolve("127.0.0.1", "2001:0db8:0:0::1") == "2001:db8::1"


def test_invalid_asserted_ip_falls_back_to_trusted_peer():
    resolver = TrustedClientIpResolver(("127.0.0.1/32",))

    assert resolver.resolve("127.0.0.1", "forged, 198.51.100.2") == "127.0.0.1"


def test_ipv4_mapped_ipv6_is_canonicalized_as_ipv4():
    resolver = TrustedClientIpResolver(("::1/128",))

    assert resolver.resolve("::ffff:192.0.2.9", "198.51.100.2") == "192.0.2.9"
    assert resolver.resolve("::1", "::ffff:198.51.100.2") == "198.51.100.2"


@pytest.mark.parametrize("peer", [None, "", "not-an-ip"])
def test_missing_or_invalid_socket_peer_fails_closed(peer):
    resolver = TrustedClientIpResolver(("127.0.0.1/32",))

    with pytest.raises(ValueError, match="socket peer"):
        resolver.resolve(peer, "198.51.100.2")


def test_invalid_trusted_proxy_network_is_rejected():
    with pytest.raises(ValueError, match="trusted proxy"):
        TrustedClientIpResolver(("127.0.0.1/999",))
