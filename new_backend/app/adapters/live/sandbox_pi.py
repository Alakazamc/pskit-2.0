"""Run the existing Pi turn contract inside a per-user Docker sandbox."""

import asyncio
import base64
import inspect
import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.adapters.live.pi_rpc import PiRpcError


class SandboxPiRunner:
    """Adapt private manager and bridge streams to ``AgentService.runner.prompt``."""

    def __init__(
        self,
        *,
        manager_url: str,
        manager_token: str,
        model: str,
        manager_transport: httpx.AsyncBaseTransport | None = None,
        bridge_transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 240,
        lease_seconds: int = 60,
    ) -> None:
        self.manager_url = manager_url.rstrip("/")
        self.manager_token = manager_token
        self.model = model
        self.manager_transport = manager_transport
        self.bridge_transport = bridge_transport
        self.timeout_seconds = timeout_seconds
        self.lease_seconds = lease_seconds

    def _bridge_connection(self, sandbox, owner_id):
        if self.bridge_transport is not None:
            return sandbox["base_url"], self.bridge_transport, sandbox["token"]
        return (
            f"{self.manager_url}/v1/sandboxes/{owner_id}/bridge",
            self.manager_transport,
            self.manager_token,
        )

    async def workspace_request(
        self, owner_id, method, path, *, continuation_attempt_id=None, **kwargs
    ):
        """Transfer scoped session bytes using the same backend-owned manager binding."""
        async with httpx.AsyncClient(
            base_url=self.manager_url, transport=self.manager_transport, timeout=20, trust_env=False
        ) as manager:
            response = await manager.post(
                "/v1/sandboxes/ensure",
                json={"user_id": owner_id},
                headers={"Authorization": f"Bearer {self.manager_token}"},
            )
            if (
                response.status_code == 409
                and continuation_attempt_id
                and self.bridge_transport is None
            ):
                bridge_url = f"{self.manager_url}/v1/sandboxes/{owner_id}/bridge"
                bridge_transport = self.manager_transport
                bridge_token = self.manager_token
            else:
                response.raise_for_status()
                sandbox = response.json()
                parsed = urlparse(sandbox["base_url"])
                if (
                    parsed.scheme != "http"
                    or not (parsed.hostname or "").startswith("pskit-sbx-")
                    or (parsed.port != 8091 or parsed.path not in {"", "/"})
                ):
                    raise PiRpcError("Sandbox manager returned an invalid endpoint")
                bridge_url, bridge_transport, bridge_token = self._bridge_connection(
                    sandbox, owner_id
                )
        async with httpx.AsyncClient(
            base_url=bridge_url, transport=bridge_transport, timeout=20, trust_env=False
        ) as bridge:
            for _ in range(40):
                try:
                    ready = await bridge.get(
                        "/health", timeout=2, headers={"Authorization": f"Bearer {bridge_token}"}
                    )
                    if ready.status_code == 200:
                        break
                except httpx.RequestError:
                    pass
                await asyncio.sleep(0.25)
            else:
                raise PiRpcError("Sandbox bridge did not become ready", retryable=True)
            headers = {"Authorization": f"Bearer {bridge_token}"}
            if continuation_attempt_id:
                headers["X-PSKit-Continuation-Attempt"] = continuation_attempt_id
            response = await bridge.request(method, path, headers=headers, **kwargs)
            response.raise_for_status()
            return response.json()

    async def prompt(
        self,
        session_id: str,
        message: str,
        on_event: Callable[[dict[str, Any]], Any],
        *,
        session_file: str | None = None,
        environment: dict[str, str] | None = None,
        allow_handled: bool = False,
        system_prompt_suffix: str = "",
        images: list[dict[str, str]] | None = None,
        include_attempt_id: bool = False,
        after_attempt: Callable[[dict[str, str]], Any] | None = None,
    ) -> dict[str, str]:
        """Ensure the user's sandbox, then relay one Pi turn without local Pi."""
        owner_id = (environment or {}).get("PSKIT_USER_ID")
        if not owner_id:
            raise ValueError("Sandbox Pi requires a backend-supplied owner")

        attempt_id = "attempt-" + uuid.uuid4().hex
        lease = None
        sandbox = None
        heartbeat = None
        dispatched = False
        confirmed_exit = False

        async def manager_post(path, payload):
            async with httpx.AsyncClient(
                base_url=self.manager_url,
                transport=self.manager_transport,
                timeout=20,
                trust_env=False,
            ) as client:
                response = await client.post(
                    path, json=payload, headers={"Authorization": f"Bearer {self.manager_token}"}
                )
                response.raise_for_status()
                return response.json()

        async def renew():
            while True:
                await asyncio.sleep(max(1, self.lease_seconds // 3))
                await manager_post(
                    "/v1/sandboxes/activity/renew",
                    {"lease_id": lease["lease_id"], "fencing_token": lease["fencing_token"]},
                )

        async def run() -> dict[str, str]:
            nonlocal lease, sandbox, heartbeat, dispatched, confirmed_exit
            try:
                async with httpx.AsyncClient(
                    base_url=self.manager_url,
                    transport=self.manager_transport,
                    timeout=20,
                    trust_env=False,
                ) as manager:
                    ensured = await manager.post(
                        "/v1/sandboxes/ensure",
                        json={"user_id": owner_id},
                        headers={"Authorization": f"Bearer {self.manager_token}"},
                    )
                    ensured.raise_for_status()
                    sandbox = ensured.json()
                lease = await manager_post(
                    "/v1/sandboxes/activity/acquire",
                    {
                        "owner_id": owner_id,
                        "session_id": session_id,
                        "run_id": attempt_id,
                        "lease_seconds": self.lease_seconds,
                    },
                )
                heartbeat = asyncio.create_task(renew())
                base_url = sandbox["base_url"]
                parsed = urlparse(base_url)
                if (
                    parsed.scheme != "http"
                    or not (parsed.hostname or "").startswith(
                        "pskit-sbx-",
                    )
                    or parsed.port != 8091
                    or parsed.path not in {"", "/"}
                ):
                    raise PiRpcError("Sandbox manager returned an invalid endpoint")
                payload = {
                    "user_id": owner_id,
                    "session_id": session_id,
                    "attempt_id": attempt_id,
                    "message": message,
                    "model": (environment or {}).get("PSKIT_MODEL_ID") or self.model,
                    "session_file": session_file,
                    "environment": environment or {},
                    "allow_handled": allow_handled,
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
                bridge_url, bridge_transport, bridge_token = self._bridge_connection(
                    sandbox, owner_id
                )
                async with httpx.AsyncClient(
                    base_url=bridge_url,
                    transport=bridge_transport,
                    timeout=None,
                    trust_env=False,
                ) as bridge:
                    for _ in range(40):
                        try:
                            ready = await bridge.get(
                                "/health",
                                timeout=2,
                                headers={"Authorization": f"Bearer {bridge_token}"},
                            )
                            if ready.status_code == 200:
                                break
                        except httpx.RequestError:
                            pass
                        await asyncio.sleep(0.25)
                    else:
                        raise PiRpcError("Sandbox bridge did not become ready", retryable=True)
                    dispatched = True
                    async with bridge.stream(
                        "POST",
                        "/v1/pi/prompt",
                        json=payload,
                        headers={"Authorization": f"Bearer {bridge_token}"},
                    ) as response:
                        # These trusted HTTP rejections occur before an attempt is created.
                        # Conflicts and server/transport failures remain unresolved.
                        if response.status_code in {400, 401, 403, 404, 413, 415, 422}:
                            dispatched = False
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
                            raise PiRpcError(
                                "Sandbox Pi stream ended without a result", retryable=True
                            )
                        confirmed_exit = True
                        completed = {**result, "attempt_id": attempt_id}
                        if after_attempt is not None:
                            completion = after_attempt(completed)
                            if inspect.isawaitable(completion):
                                await completion
                        return completed if include_attempt_id else result
            except (httpx.HTTPError, OSError, json.JSONDecodeError, KeyError) as exc:
                raise PiRpcError("Sandbox service unavailable", retryable=True) from exc

            finally:
                if heartbeat:
                    heartbeat.cancel()
                    try:
                        await heartbeat
                    except (asyncio.CancelledError, httpx.HTTPError):
                        pass
                if lease:
                    if dispatched and not confirmed_exit and sandbox:
                        # A closed TCP stream or cancel request does not prove subprocess exit.
                        try:
                            bridge_url, bridge_transport, bridge_token = self._bridge_connection(
                                sandbox, owner_id
                            )
                            async with httpx.AsyncClient(
                                base_url=bridge_url,
                                transport=bridge_transport,
                                timeout=3,
                                trust_env=False,
                            ) as bridge:
                                headers = {"Authorization": f"Bearer {bridge_token}"}
                                response = await bridge.post(
                                    f"/v1/pi/attempts/{attempt_id}/cancel", headers=headers
                                )
                                response.raise_for_status()
                                for _ in range(30):
                                    status = response.json()
                                    if status.get("exited") is True:
                                        confirmed_exit = True
                                        break
                                    await asyncio.sleep(0.1)
                                    response = await bridge.get(
                                        f"/v1/pi/attempts/{attempt_id}", headers=headers
                                    )
                                    response.raise_for_status()
                        except (httpx.HTTPError, ValueError):
                            pass
                    if confirmed_exit or not dispatched:
                        try:
                            await manager_post(
                                "/v1/sandboxes/activity/release",
                                {
                                    "lease_id": lease["lease_id"],
                                    "fencing_token": lease["fencing_token"],
                                },
                            )
                        except httpx.HTTPError:
                            pass  # Persisted unresolved lease is reconciled by the manager.

        try:
            return await asyncio.wait_for(run(), timeout=self.timeout_seconds)
        except TimeoutError as exc:
            raise PiRpcError("Sandbox Pi deadline exceeded", retryable=True) from exc
