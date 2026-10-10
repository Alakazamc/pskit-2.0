import asyncio
from time import monotonic
from typing import Literal

import httpx
from fastapi import FastAPI
from pydantic import BaseModel

from app.adapters.live.multi_remote_mcp import MultiRemoteMcp

DependencyStatus = Literal[
    "available", "unavailable", "unauthorized", "rate_limited",
    "unknown", "partial", "mock", "disabled", "inbound_only",
]
PROBE_TIMEOUT_SECONDS = 3.0


class DependencyReport(BaseModel):
    identity: DependencyStatus
    model_gateway: DependencyStatus
    mcp: DependencyStatus
    af3: DependencyStatus
    mcp_servers: dict[str, DependencyStatus]


def make_http_client() -> httpx.AsyncClient:
    """Create a short-lived client with redirects disabled for probes."""
    return httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS, follow_redirects=False)


async def _probe_http(client: httpx.AsyncClient, url: str, headers: dict[str, str]) -> DependencyStatus:
    """Map one authenticated HTTP health response to a safe status label."""
    try:
        response = await asyncio.wait_for(
            client.get(url, headers=headers), timeout=PROBE_TIMEOUT_SECONDS,
        )
    except (httpx.HTTPError, TimeoutError):
        return "unavailable"
    if 200 <= response.status_code < 300:
        return "available"
    if response.status_code in {401, 403}:
        return "unauthorized"
    if response.status_code == 429:
        return "rate_limited"
    return "unavailable"


def _mcp_status(app: FastAPI) -> tuple[DependencyStatus, dict[str, DependencyStatus]]:
    """Summarize recent MCP discovery for one or multiple remote servers."""
    executor = app.state.mcp_executor
    if executor != "remote":
        return executor, {}
    last_checked = app.state.mcp_last_checked_at
    max_age = max(10.0, 2 * app.state.settings.mcp_refresh_seconds)
    if not app.state.mcp_checked or last_checked is None or monotonic() - last_checked > max_age:
        return "unknown", {}
    provider = app.state.mcp.provider
    if isinstance(provider, MultiRemoteMcp):
        servers: dict[str, DependencyStatus] = {
            server_id: "available" if available else "unavailable"
            for server_id, available in provider.server_statuses().items()
        }
        if not servers or all(status == "unavailable" for status in servers.values()):
            return "unavailable", servers
        if any(status == "unavailable" for status in servers.values()):
            return "partial", servers
        return "available", servers
    return ("unavailable" if app.state.mcp_unavailable else "available"), {}


async def inspect_dependencies(app: FastAPI) -> DependencyReport:
    """Probe configured identity and model services, then summarize MCP/AF3.

    The report omits service URLs, credentials, and upstream error bodies.

    Args:
        app: Running FastAPI app with initialized adapter state.

    Returns:
        Sanitized dependency status for the admin endpoint.
    """
    settings = app.state.settings
    identity: DependencyStatus = "mock" if settings.mode == "mock" else "unknown"
    gateway: DependencyStatus = "disabled" if settings.mode != "live" or settings.agent_runtime != "pi" else "unknown"
    if identity == "unknown" or gateway == "unknown":
        async with make_http_client() as client:
            probes = []
            if identity == "unknown":
                probes.append(_probe_http(
                    client, f"{settings.supabase_url.rstrip('/')}/auth/v1/health",
                    {"apikey": settings.supabase_publishable_key},
                ))
            if gateway == "unknown":
                base = (settings.model_gateway_base_url or settings.new_api_base_url).rstrip("/")
                probes.append(_probe_http(
                    client, f"{base.removesuffix('/v1')}/v1/models",
                    ({"Authorization": f"Bearer {settings.model_gateway_api_key}"}
                     if settings.model_gateway_api_key else {}),
                ))
            results = await asyncio.gather(*probes)
            if identity == "unknown":
                identity = results.pop(0)
            if gateway == "unknown":
                gateway = results.pop(0)
    mcp, mcp_servers = _mcp_status(app)
    af3: DependencyStatus = (
        "inbound_only" if app.state.af3_executor == "callback" else app.state.af3_executor
    )
    return DependencyReport(
        identity=identity, model_gateway=gateway, mcp=mcp, af3=af3,
        mcp_servers=mcp_servers,
    )
