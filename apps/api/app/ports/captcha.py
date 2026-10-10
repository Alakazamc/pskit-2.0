"""Provider-neutral CAPTCHA verification boundary."""

from typing import Protocol


class CaptchaRequired(Exception):
    """A protected operation did not include a challenge proof."""


class CaptchaInvalid(Exception):
    """A challenge proof was rejected or did not match its operation."""


class CaptchaUnavailable(Exception):
    """The challenge provider could not make a trustworthy decision."""


class CaptchaVerifier(Protocol):
    """Verify a browser challenge without exposing a vendor to API routes."""

    async def verify(self, token: str, *, remote_ip: str, action: str) -> None:
        """Accept a valid proof or raise a classified CAPTCHA exception."""
        ...
