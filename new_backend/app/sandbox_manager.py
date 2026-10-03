"""Private Docker lifecycle API for one persistent container per user."""

import asyncio
import hashlib
import hmac
import os
import re
import secrets
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from time import monotonic
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

DOCKER_API = "/v1.45"


@dataclass(frozen=True)
class SandboxManagerSettings:
    """Immutable server-owned Docker and authentication configuration."""

    auth_token: str
    bridge_secret: str
    image: str
    network: str
    namespace: str
    docker_socket: str = "/var/run/docker.sock"

    @classmethod
    def from_env(cls) -> "SandboxManagerSettings":
        return cls(
            auth_token=os.environ["PSKIT_SANDBOX_MANAGER_TOKEN"],
            bridge_secret=os.environ["PSKIT_SANDBOX_BRIDGE_SECRET"],
            image=os.environ["PSKIT_SANDBOX_IMAGE"],
            network=os.environ["PSKIT_SANDBOX_NETWORK"],
            namespace=os.environ["PSKIT_SANDBOX_NAMESPACE"],
        )

    def validate(self) -> None:
        if len(self.auth_token) < 16 or len(self.bridge_secret) < 16:
            raise ValueError("Sandbox manager secrets must contain at least 16 characters")
        if not self.image or self.image.endswith(":latest") or ":" not in self.image:
            raise ValueError("PSKIT_SANDBOX_IMAGE must use a fixed tag or digest")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", self.network):
            raise ValueError("PSKIT_SANDBOX_NETWORK must be a Docker network name")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,30}", self.namespace):
            raise ValueError("PSKIT_SANDBOX_NAMESPACE must be a short lowercase name")


class EnsureSandbox(BaseModel):
    user_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


def create_manager_app(
    settings: SandboxManagerSettings, *, docker_transport: httpx.AsyncBaseTransport | None = None,
    clock: Callable[[], float] = monotonic,
) -> FastAPI:
    """Expose only authenticated, deterministic sandbox creation to the backend."""
    settings.validate()
    locks: dict[str, asyncio.Lock] = {}
    last_used: dict[str, float] = {}

    async def sweep_idle() -> int:
        """Stop idle containers while leaving their named volumes untouched."""
        stopped = 0
        for name, last_seen in list(last_used.items()):
            if clock() - last_seen < 1800:
                continue
            async with locks.setdefault(name, asyncio.Lock()):
                if clock() - last_used.get(name, clock()) < 1800:
                    continue
                transport = docker_transport or httpx.AsyncHTTPTransport(
                    uds=settings.docker_socket,
                )
                try:
                    async with httpx.AsyncClient(
                        transport=transport, base_url="http://docker", timeout=20,
                        trust_env=False,
                    ) as docker:
                        response = await docker.post(f"{DOCKER_API}/containers/{name}/stop",
                                                     params={"t": 5})
                        if response.status_code not in {204, 304}:
                            response.raise_for_status()
                except httpx.HTTPError as exc:
                    raise HTTPException(status_code=503, detail="Docker sandbox unavailable") from exc
                last_used.pop(name, None)
                stopped += 1
        return stopped

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        transport = docker_transport or httpx.AsyncHTTPTransport(uds=settings.docker_socket)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://docker", timeout=20, trust_env=False,
        ) as docker:
            existing = await docker.get(f"{DOCKER_API}/containers/json", params={
                "filters": '{"label":["pskit.sandbox.namespace=' + settings.namespace + '"]}',
            })
            existing.raise_for_status()
            for container in existing.json():
                if container.get("Labels", {}).get("pskit.sandbox.namespace") != settings.namespace:
                    continue
                for raw_name in container.get("Names", []):
                    name = raw_name.lstrip("/")
                    if name.startswith(f"pskit-sbx-{settings.namespace}-"):
                        last_used.setdefault(name, clock())

        async def reaper() -> None:
            while True:
                await asyncio.sleep(60)
                try:
                    await sweep_idle()
                except HTTPException:
                    continue

        task = asyncio.create_task(reaper())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="PSKit Sandbox Manager", docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/sandboxes/ensure")
    async def ensure(
        request: EnsureSandbox, authorization: str | None = Header(default=None),
    ) -> dict[str, str]:
        if not secrets.compare_digest(
            authorization or "", f"Bearer {settings.auth_token}",
        ):
            raise HTTPException(status_code=401, detail="Unauthorized")
        identity = hashlib.sha256(
            f"{settings.namespace}:{request.user_id}".encode(),
        ).hexdigest()[:24]
        name = f"pskit-sbx-{settings.namespace}-{identity}"
        volume = f"{name}-workspace"
        bridge_token = hmac.new(
            settings.bridge_secret.encode(), request.user_id.encode(), hashlib.sha256,
        ).hexdigest()
        async with locks.setdefault(name, asyncio.Lock()):
            transport = docker_transport or httpx.AsyncHTTPTransport(uds=settings.docker_socket)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://docker", timeout=20,
                trust_env=False,
            ) as docker:
                try:
                    network = await docker.get(
                        f"{DOCKER_API}/networks/{quote(settings.network)}",
                    )
                    network.raise_for_status()
                    if network.json().get("Internal") is not True:
                        raise HTTPException(status_code=503,
                                            detail="Sandbox network must be private")
                    volume_info = await docker.get(f"{DOCKER_API}/volumes/{quote(volume)}")
                    if volume_info.status_code == 404:
                        created = await docker.post(f"{DOCKER_API}/volumes/create", json={
                            "Name": volume,
                            "Labels": {"pskit.sandbox.namespace": settings.namespace,
                                       "pskit.sandbox.owner_hash": identity},
                        })
                        created.raise_for_status()
                    else:
                        volume_info.raise_for_status()

                    inspected = await docker.get(f"{DOCKER_API}/containers/{name}/json")
                    if inspected.status_code == 404:
                        config = {
                            "Image": settings.image,
                            "Cmd": ["python", "-m", "uvicorn", "app.sandbox_bridge:create_app",
                                    "--factory", "--host", "0.0.0.0", "--port", "8091"],
                            "User": "10001:10001",
                            "WorkingDir": "/workspace",
                            "Env": [
                                f"PSKIT_SANDBOX_USER_ID={request.user_id}",
                                f"PSKIT_SANDBOX_BRIDGE_TOKEN={bridge_token}",
                                "RESEARCH_AGENT_PI_SESSION_DIR=/workspace/sessions",
                                "RESEARCH_AGENT_INTERNAL_API_URL=http://backend:8000",
                            ],
                            "Labels": {"pskit.sandbox.namespace": settings.namespace,
                                       "pskit.sandbox.owner_hash": identity},
                            "HostConfig": {
                                "NetworkMode": settings.network,
                                "Mounts": [{"Type": "volume", "Source": volume,
                                            "Target": "/workspace"}],
                                "ReadonlyRootfs": True,
                                "Tmpfs": {"/tmp": "rw,noexec,nosuid,size=64m"},
                                "Memory": 1_073_741_824,
                                "NanoCpus": 1_000_000_000,
                                "PidsLimit": 256,
                                "CapDrop": ["ALL"],
                                "SecurityOpt": ["no-new-privileges"],
                            },
                        }
                        created = await docker.post(
                            f"{DOCKER_API}/containers/create", params={"name": name}, json=config,
                        )
                        created.raise_for_status()
                        container_id = created.json()["Id"]
                        running = False
                    else:
                        inspected.raise_for_status()
                        data = inspected.json()
                        if (data.get("Config", {}).get("Image") != settings.image
                                or data.get("Config", {}).get("Labels", {}).get(
                                    "pskit.sandbox.owner_hash") != identity):
                            raise HTTPException(status_code=409, detail="Sandbox image or owner mismatch")
                        container_id = data["Id"]
                        running = data.get("State", {}).get("Running") is True
                    if not running:
                        started = await docker.post(
                            f"{DOCKER_API}/containers/{container_id}/start",
                        )
                        started.raise_for_status()
                except httpx.HTTPError as exc:
                    raise HTTPException(status_code=503, detail="Docker sandbox unavailable") from exc
        last_used[name] = clock()
        return {"base_url": f"http://{name}:8091", "token": bridge_token}

    @app.post("/v1/sandboxes/sweep")
    async def sweep(authorization: str | None = Header(default=None)) -> dict[str, int]:
        if not secrets.compare_digest(
            authorization or "", f"Bearer {settings.auth_token}",
        ):
            raise HTTPException(status_code=401, detail="Unauthorized")
        return {"stopped": await sweep_idle()}

    return app


def create_app() -> FastAPI:
    """Load private Compose configuration for the manager service."""
    return create_manager_app(SandboxManagerSettings.from_env())
