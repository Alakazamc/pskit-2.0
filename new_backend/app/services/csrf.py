"""Short-lived CSRF tokens bound to the server-only refresh credential."""

import base64
import hashlib
import hmac
import secrets
from time import time


class CsrfRejected(ValueError):
    """Raised when a cookie-authenticated request fails CSRF validation."""


class CsrfProtector:
    """Issue and validate stateless tokens for one trusted browser origin."""

    def __init__(
        self,
        secret: str,
        trusted_origin: str,
        *,
        lifetime_seconds: int = 900,
    ) -> None:
        self._secret = secret.encode()
        self._trusted_origin = trusted_origin.rstrip("/")
        self._lifetime_seconds = lifetime_seconds

    def issue(self, refresh_token: str) -> str:
        """Create a short-lived token bound to the current refresh token."""
        payload = f"{int(time())}.{secrets.token_urlsafe(24)}"
        return f"{payload}.{self._sign(refresh_token, payload)}"

    def verify(self, refresh_token: str, token: str, origin: str) -> None:
        """Require the trusted origin and a current, correctly signed token."""
        if origin != self._trusted_origin:
            raise CsrfRejected("untrusted origin")
        try:
            timestamp, nonce, signature = token.split(".", 2)
            issued_at = int(timestamp)
        except (TypeError, ValueError) as exc:
            raise CsrfRejected("malformed token") from exc
        now = int(time())
        if issued_at > now + 60 or now - issued_at > self._lifetime_seconds or not nonce:
            raise CsrfRejected("expired token")
        expected = self._sign(refresh_token, f"{timestamp}.{nonce}")
        if not hmac.compare_digest(signature, expected):
            raise CsrfRejected("invalid token")

    def _sign(self, refresh_token: str, payload: str) -> str:
        digest = hmac.new(
            self._secret,
            b"pskit-csrf-v1\0" + refresh_token.encode() + b"\0" + payload.encode(),
            hashlib.sha256,
        ).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
