"""Run the existing Pi turn contract inside a per-user Docker sandbox."""

import asyncio
import base64
import inspect
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.adapters.live.pi_rpc import PiRpcError


class SandboxPiRunner:
    """Adapt private manager and bridge streams to ``AgentService.runner.prompt``."""

    def __init__(
        self, *, manager_url: str, manager_token: str, model: str,
        manager_transport: httpx.AsyncBaseTransport | None = None,
        bridge_transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 240,
    ) -> None:
        self.manager_url = manager_url.rstrip("/")
        self.manager_token = manager_token
        self.model = model
        self.manager_transport = manager_transport
        self.bridge_transport = bridge_transport
        self.timeout_seconds = timeout_seconds

    async def prompt(
        self, session_id: str, message: str,
        on_event: Callable[[dict[str, Any]], Any], *,
        session_file: str | None = None,
        environment: dict[str, str] | None = None,
        allow_handled: bool = False,
        system_prompt_suffix: str = "",
        images: list[dict[str, str]] | None = None,
    ) -> dict[str, str]:
        """Ensure the user's sandbox, then relay one Pi turn without local Pi."""
        owner_id = (environment or {}).get("PSKIT_USER_ID")
        if not owner_id:
            raise ValueError("Sandbox Pi requires a backend-supplied owner")

        async def run() -> dict[str, str]:
            try:
                async with httpx.AsyncClient(
                    base_url=self.manager_url, transport=self.manager_transport,
                    timeout=20, trust_env=False,
                ) as manager:
                    ensured = await manager.post(
                        "/v1/sandboxes/ensure", json={"user_id": owner_id},
                        headers={"Authorization": f"Bearer {self.manager_token}"},
                    )
                    ensured.raise_for_status()
                    sandbox = ensured.json()
                base_url = sandbox["base_url"]
                parsed = urlparse(base_url)
                if (parsed.scheme != "http" or not (parsed.hostname or "").startswith(
                    "pskit-sbx-",
                ) or parsed.port != 8091 or parsed.path not in {"", "/"}):
                    raise PiRpcError("Sandbox manager returned an invalid endpoint")
                payload = {
                    "user_id": owner_id, "session_id": session_id, "message": message,
                    "model": (environment or {}).get("PSKIT_MODEL_ID") or self.model,
                    "session_file": session_file,
                    "environment": environment or {}, "allow_handled": allow_handled,
                    "system_prompt_suffix": system_prompt_suffix,
                    "images": images or [],
                }
                if session_file and not session_file.startswith("/workspace/sessions/"):
                    legacy_path = Path(session_file)
                    if not legacy_path.is_file():
                        raise PiRpcError("Committed Pi session file is missing")
                    if legacy_path.stat().st_size > 32 * 1024 * 1024:
                        raise PiRpcError("Committed Pi session exceeds 32 MiB")
                    payload["session_file"] = None
                    payload["legacy_session_b64"] = base64.b64encode(
                        legacy_path.read_bytes(),
                    ).decode("ascii")
                async with httpx.AsyncClient(
                    base_url=base_url, transport=self.bridge_transport,
                    timeout=None, trust_env=False,
                ) as bridge:
                    for _ in range(40):
                        try:
                            ready = await bridge.get("/health", timeout=2)
                            if ready.status_code == 200:
                                break
                        except httpx.RequestError:
                            pass
                        await asyncio.sleep(0.25)
                    else:
                        raise PiRpcError("Sandbox bridge did not become ready", retryable=True)
                    async with bridge.stream(
                        "POST", "/v1/pi/prompt", json=payload,
                        headers={"Authorization": f"Bearer {sandbox['token']}"},
                    ) as response:
                        response.raise_for_status()
                        pending = b""
                        result: dict[str, str] | None = None
                        async for chunk in response.aiter_bytes():
                            lines = (pending + chunk).split(b"\n")
                            pending = lines.pop()
                            if len(pending) > 4 * 1024 * 1024:
                                raise PiRpcError("Sandbox event exceeds size limit")
                            for line in lines:
                                if not line:
                                    continue
                                if len(line) > 4 * 1024 * 1024:
                                    raise PiRpcError("Sandbox event exceeds size limit")
                                record = json.loads(line)
                                if record.get("kind") == "event":
                                    projected = on_event(record["data"])
                                    if inspect.isawaitable(projected):
                                        await projected
                                elif record.get("kind") == "result":
                                    result = record["data"]
                                elif record.get("kind") == "error":
                                    raise PiRpcError(
                                        str(record.get("message") or "Sandbox Pi failed"),
                                        code=str(record.get("code") or "PI_RUN_FAILED"),
                                        retryable=record.get("retryable") is True,
                                    )
                        if result is None or not isinstance(result.get("session_file"), str):
                            raise PiRpcError("Sandbox Pi stream ended without a result", retryable=True)
                        return result
            except (httpx.HTTPError, OSError, json.JSONDecodeError, KeyError) as exc:
                raise PiRpcError("Sandbox service unavailable", retryable=True) from exc

        try:
            return await asyncio.wait_for(run(), timeout=self.timeout_seconds)
        except TimeoutError as exc:
            raise PiRpcError("Sandbox Pi deadline exceeded", retryable=True) from exc
