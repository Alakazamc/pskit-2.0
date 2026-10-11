"""HTTP resource ownership and sanitized errors at the existing model boundary.

Authorization and token reservations remain with the caller. This module has no
credentials, provider registry, endpoint selection or automatic retry policy.
"""

import json
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

import httpx

from app.contracts.model_runtime import ModelRuntimeProfile

MAX_ERROR_BYTES = 64 * 1024


def prepare_model_parameters(payload: dict, profile: ModelRuntimeProfile | None) -> dict:
    """Apply only the trusted profile's opt-in OpenAI parameter policy.

    Ordinary Pi calls and qualification use this same wire preparation. Legacy
    profiles retain their provider's existing mapping and parameter filtering.
    Caller overrides must be rejected before entering this boundary.
    """
    prepared = dict(payload)
    if profile is not None and profile.wire_policy.preserve_openai_parameters is True:
        prepared["drop_params"] = False
        prepared["allowed_openai_params"] = [
            name for name in ("tools", "tool_choice", "reasoning_effort") if name in prepared
        ]
        prepared["additional_drop_params"] = []
    return prepared


def create_gateway_client(transport=None) -> httpx.AsyncClient:
    """Create the application connection pool without ambient proxy inheritance."""
    return httpx.AsyncClient(
        transport=transport,
        trust_env=False,
        timeout=httpx.Timeout(180, connect=10, pool=10),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20,
                            keepalive_expiry=30),
    )


@dataclass
class GatewayClientLease:
    client: httpx.AsyncClient
    owned: bool

    async def aclose(self) -> None:
        """Release a fallback pool, leaving lifespan and injected pools untouched."""
        if self.owned:
            await self.client.aclose()


def acquire_gateway_client(state=None, *, client=None, transport=None) -> GatewayClientLease:
    """Use the current shared/injected pool; standalone calls own a bounded fallback."""
    shared = client if client is not None else getattr(state, "model_gateway_http_client", None)
    return GatewayClientLease(shared, False) if shared is not None else GatewayClientLease(
        create_gateway_client(transport), True,
    )


@asynccontextmanager
async def gateway_client_lifespan(state):
    """Create and close the owned pool, including failures before application yield."""
    if getattr(state, "model_gateway_http_client", None) is not None:
        yield
        return
    catalog = getattr(state, "model_catalog", None)
    client = create_gateway_client(getattr(catalog, "transport", None))
    state.model_gateway_http_client = client
    try:
        yield
    finally:
        try:
            await client.aclose()
        finally:
            if getattr(state, "model_gateway_http_client", None) is client:
                del state.model_gateway_http_client


@dataclass(frozen=True)
class GatewayError:
    status_code: int
    code: str
    headers: dict[str, str]

    @property
    def definite_rejection(self) -> bool:
        """Only recognized pre-execution failures may release a conservative hold."""
        return self.code in {
            "MODEL_GATEWAY_AUTH_FAILED", "MODEL_GATEWAY_RATE_LIMITED",
            "MODEL_GATEWAY_QUOTA_EXHAUSTED", "MODEL_GATEWAY_INVALID_REQUEST",
        }

    def body(self) -> dict:
        """Keep the existing OpenAI error shape, with a stable secret-free message."""
        return {"error": {"code": self.code, "message": self.code}}


def _retry_after(value: str | None) -> dict[str, str]:
    if not value or len(value) > 128 or any(ord(char) < 32 for char in value):
        return {}
    if re.fullmatch(r"[0-9]{1,10}", value):
        return {"Retry-After": value}
    try:
        if parsedate_to_datetime(value).tzinfo is not None:
            return {"Retry-After": value}
    except (ValueError, TypeError, OverflowError):
        pass
    return {}


def normalize_gateway_error(response: httpx.Response, *, body: bytes | None = None) -> GatewayError:
    """Classify only structured codes and HTTP status, never supplier message text."""
    try:
        data = json.loads(body) if body is not None else response.json()
    except (ValueError, httpx.ResponseNotRead):
        data = {}
    error = data.get("error", {}) if isinstance(data, dict) else {}
    codes = {
        str(error.get(field, "")).lower() for field in ("code", "type")
    } if isinstance(error, dict) else set()
    quota = {"insufficient_quota", "quota_exceeded", "budget_exceeded",
             "insufficient_balance", "billing_hard_limit_reached"}
    invalid = {"invalid_request_error", "invalid_request", "invalid_parameter",
               "unsupported_parameter", "model_not_found"}
    status = response.status_code
    if status >= 500:
        code = "MODEL_GATEWAY_UNAVAILABLE"
    elif status in {401, 403}:
        code = "MODEL_GATEWAY_AUTH_FAILED"
    elif status == 402 or (status in {400, 429} and codes & quota):
        code = "MODEL_GATEWAY_QUOTA_EXHAUSTED"
    elif status == 429:
        code = "MODEL_GATEWAY_RATE_LIMITED"
    elif status in {400, 404, 422} and codes & invalid:
        code = "MODEL_GATEWAY_INVALID_REQUEST"
    else:
        code = "UNKNOWN_UPSTREAM_ERROR"
    return GatewayError(status, code, _retry_after(response.headers.get("retry-after")))


async def read_gateway_error(response: httpx.Response) -> GatewayError:
    """Bound private error reads; truncated or broken bodies retain unknown usage."""
    body = bytearray()
    readable = True
    try:
        async for chunk in response.aiter_bytes():
            if len(body) + len(chunk) > MAX_ERROR_BYTES:
                body.clear()
                readable = False
                break
            body.extend(chunk)
    except httpx.HTTPError:
        body.clear()
        readable = False
    if not readable:
        return GatewayError(response.status_code, "UNKNOWN_UPSTREAM_ERROR",
                            _retry_after(response.headers.get("retry-after")))
    return normalize_gateway_error(response, body=bytes(body))
