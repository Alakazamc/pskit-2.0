"""Cloudflare Turnstile Siteverify adapter."""

import httpx

from app.ports.captcha import CaptchaInvalid, CaptchaUnavailable

_SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


class TurnstileVerifier:
    """Validate a proof once and bind it to the expected action and hostname."""

    def __init__(
        self,
        secret: str,
        allowed_hostnames: tuple[str, ...],
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not secret:
            raise ValueError("Turnstile secret is required")
        if not allowed_hostnames:
            raise ValueError("At least one Turnstile hostname is required")
        self._secret = secret
        self._hostnames = frozenset(hostname.lower() for hostname in allowed_hostnames)
        self._client = client

    async def verify(self, token: str, *, remote_ip: str, action: str) -> None:
        """Perform one Siteverify request; uncertain responses fail closed."""
        if not token:
            raise CaptchaInvalid
        data = {
            "secret": self._secret,
            "response": token,
            "remoteip": remote_ip,
        }
        try:
            if self._client is None:
                async with httpx.AsyncClient(timeout=5) as client:
                    response = await client.post(_SITEVERIFY_URL, data=data)
            else:
                response = await self._client.post(_SITEVERIFY_URL, data=data)
        except httpx.HTTPError as exc:
            raise CaptchaUnavailable from exc
        if response.status_code != 200:
            raise CaptchaUnavailable
        try:
            payload = response.json()
        except ValueError as exc:
            raise CaptchaUnavailable from exc
        if not isinstance(payload, dict):
            raise CaptchaUnavailable
        if payload.get("success") is not True:
            raise CaptchaInvalid
        returned_action = payload.get("action")
        hostname = payload.get("hostname")
        if returned_action != action or not isinstance(hostname, str):
            raise CaptchaInvalid
        if hostname.lower() not in self._hostnames:
            raise CaptchaInvalid
