"""Private Docker lifecycle API for one persistent container per user."""

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from time import time
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, TypeAdapter

from app.contracts.sandbox import (
    Identity,
    PositiveSeconds,
    Revision,
    SandboxAttemptStatus,
    SandboxLease,
    SandboxMetrics,
    SandboxOperation,
    SandboxSummary,
)
from app.db.postgres import PostgresDatabase
from app.db.postgres_migrations import SCHEMA_VERSION
from app.domain.sandboxes import SandboxActivityStore, SandboxConflict

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
    postgres_dsn: str | None = None
    postgres_schema: str = "pskit"
    gateway_url: str = "http://sandbox-gateway:8080"
    idle_seconds: int = 1800
    nano_cpus: int = 1_000_000_000
    memory_bytes: int = 1_073_741_824
    pids_limit: int = 256

    @classmethod
    def from_env(cls) -> "SandboxManagerSettings":
        return cls(
            auth_token=os.environ["PSKIT_SANDBOX_MANAGER_TOKEN"],
            bridge_secret=os.environ["PSKIT_SANDBOX_BRIDGE_SECRET"],
            image=os.environ["PSKIT_SANDBOX_IMAGE"],
            network=os.environ["PSKIT_SANDBOX_NETWORK"],
            namespace=os.environ["PSKIT_SANDBOX_NAMESPACE"],
            postgres_dsn=os.environ["PSKIT_SANDBOX_POSTGRES_DSN"],
            postgres_schema=os.environ.get("PSKIT_SANDBOX_POSTGRES_SCHEMA", "pskit"),
            gateway_url=os.environ.get("PSKIT_SANDBOX_GATEWAY_URL", "http://sandbox-gateway:8080"),
            idle_seconds=int(os.environ.get("PSKIT_SANDBOX_IDLE_SECONDS", "1800")),
        )

    def validate(self) -> None:
        if min(self.idle_seconds, self.nano_cpus, self.memory_bytes, self.pids_limit) <= 0:
            raise ValueError("Sandbox lifecycle and resource limits must be positive")
        if len(self.auth_token) < 16 or len(self.bridge_secret) < 16:
            raise ValueError("Sandbox manager secrets must contain at least 16 characters")
        if self.postgres_dsn and not re.fullmatch(r"(?:[^\s]+@)?sha256:[a-f0-9]{64}", self.image):
            raise ValueError("Live sandbox image must use an immutable digest")
        if not self.image or self.image.endswith(":latest") or ":" not in self.image:
            raise ValueError("PSKIT_SANDBOX_IMAGE must use a fixed tag or digest")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", self.network):
            raise ValueError("PSKIT_SANDBOX_NETWORK must be a Docker network name")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,30}", self.namespace):
            raise ValueError("PSKIT_SANDBOX_NAMESPACE must be a short lowercase name")


class EnsureSandbox(BaseModel):
    user_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class AcquireActivity(BaseModel):
    owner_id: Identity
    session_id: Identity
    run_id: Identity
    lease_seconds: PositiveSeconds


class LeaseFence(BaseModel):
    lease_id: str
    fencing_token: PositiveSeconds


class DrainRequest(BaseModel):
    expected_revision: Revision


class ReplaceRequest(BaseModel):
    image_digest: str


def create_manager_app(
    settings: SandboxManagerSettings,
    *,
    docker_transport: httpx.AsyncBaseTransport | None = None,
    bridge_transport: httpx.AsyncBaseTransport | None = None,
    activity_store: SandboxActivityStore | None = None,
    clock: Callable[[], float] = time,
) -> FastAPI:
    """Expose private lifecycle control backed by explicit persistent activity state."""
    settings.validate()
    database = None
    if activity_store is None:
        if not settings.postgres_dsn:
            raise ValueError("Sandbox manager requires a private PostgreSQL DSN")
        database = PostgresDatabase(settings.postgres_dsn, schema=settings.postgres_schema)
        database.check_schema_version(SCHEMA_VERSION)
        activity_store = SandboxActivityStore(database, clock=clock)
    state = activity_store
    locks: dict[str, asyncio.Lock] = {}

    def authorize(authorization):
        if not secrets.compare_digest(authorization or "", f"Bearer {settings.auth_token}"):
            raise HTTPException(status_code=401, detail="Unauthorized")

    def identity(owner):
        digest = hashlib.sha256(f"{settings.namespace}:{owner}".encode()).hexdigest()[:24]
        name = f"pskit-sbx-{settings.namespace}-{digest}"
        return digest, name, name + "-workspace"

    def token(owner):
        return hmac.new(settings.bridge_secret.encode(), owner.encode(), hashlib.sha256).hexdigest()

    def docker_client():
        return httpx.AsyncClient(
            transport=docker_transport or httpx.AsyncHTTPTransport(uds=settings.docker_socket),
            base_url="http://docker",
            timeout=20,
            trust_env=False,
        )

    async def bridge_activity(owner):
        async with httpx.AsyncClient(
            transport=bridge_transport, timeout=3, trust_env=False
        ) as client:
            response = await client.get(
                f"http://{owner.instance_id}:8091/v1/pi/activity",
                headers={"Authorization": f"Bearer {token(owner.owner_id)}"},
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data.get("attempts"), list) or not data.get("boot_id"):
                raise ValueError("Invalid bridge activity")
            attempts = [SandboxAttemptStatus.model_validate(item) for item in data["attempts"]]
            for attempt in attempts:
                terminal = attempt.state in {"cancelled", "completed", "failed"}
                if attempt.exited != terminal:
                    raise ValueError("Bridge exit status contradicts attempt state")
            return [attempt.model_dump() for attempt in attempts]

    async def idle_confirmed(owner):
        try:
            attempts = await bridge_activity(owner)
        except (httpx.HTTPError, ValueError):
            state.set_runtime(owner.owner_id, "unknown")
            return False
        if any(not attempt.get("exited") for attempt in attempts):
            return False
        # Leases stay unresolved unless the bridge has an explicit terminal record.
        by_id = {attempt["attempt_id"]: attempt for attempt in attempts if attempt.get("exited")}
        for lease in state.activity(owner.owner_id).leases:
            if lease.state == "unknown" and lease.run_id in by_id:
                state.release(lease.lease_id, lease.fencing_token)
        return not state.activity(owner.owner_id).leases

    async def replace(operation):
        if operation.state == "completed":
            return operation
        owner = state.owner(operation.owner_id)
        async with docker_client() as docker:
            inspected = await docker.get(f"{DOCKER_API}/containers/{owner.instance_id}/json")
            if inspected.status_code != 404:
                inspected.raise_for_status()
                data = inspected.json()
                owner_hash, _, _ = identity(owner.owner_id)
                labels = data.get("Config", {}).get("Labels", {})
                if labels.get("pskit.sandbox.owner_hash") != owner_hash or (
                    labels.get("pskit.sandbox.namespace") != settings.namespace
                ):
                    raise HTTPException(status_code=409, detail="Sandbox owner mismatch")
                running = data.get("State", {}).get("Running") is True
                if running and not await idle_confirmed(owner):
                    return operation
                if not running:
                    for lease in state.activity(owner.owner_id).leases:
                        state.release(lease.lease_id, lease.fencing_token)
                claimed = state.claim_replacement(operation)
                if running:
                    stopped = await docker.post(
                        f"{DOCKER_API}/containers/{owner.instance_id}/stop", params={"t": 5}
                    )
                    if stopped.status_code not in {204, 304}:
                        stopped.raise_for_status()
                removed = await docker.delete(
                    f"{DOCKER_API}/containers/{owner.instance_id}", params={"v": "false"}
                )
                removed.raise_for_status()
            else:
                for lease in state.activity(owner.owner_id).leases:
                    state.release(lease.lease_id, lease.fencing_token)
                claimed = state.claim_replacement(operation)
        return state.complete_replacement(claimed)

    async def sweep_idle():
        stopped = 0
        for operation in state.pending_replacements():
            async with locks.setdefault(operation.owner_id, asyncio.Lock()):
                try:
                    await replace(operation)
                except (httpx.HTTPError, HTTPException, SandboxConflict):
                    state.set_runtime(operation.owner_id, "unknown")
        for owner in state.list():
            async with locks.setdefault(owner.owner_id, asyncio.Lock()):
                if owner.runtime_state == "stopped":
                    continue
                if clock() - owner.last_completed_at < settings.idle_seconds:
                    continue
                if not await idle_confirmed(owner):
                    continue
                state.set_runtime(owner.owner_id, "running")
                if not state.claim_stop(owner.owner_id, settings.idle_seconds):
                    continue
                try:
                    async with docker_client() as docker:
                        response = await docker.post(
                            f"{DOCKER_API}/containers/{owner.instance_id}/stop", params={"t": 5}
                        )
                        if response.status_code not in {204, 304}:
                            response.raise_for_status()
                    state.set_runtime(owner.owner_id, "stopped")
                    stopped += 1
                except httpx.HTTPError:
                    state.set_runtime(owner.owner_id, "unknown")
        return stopped

    @asynccontextmanager
    async def lifespan(_app):
        # Persisted leases survive restart; no PID or elapsed heartbeat is exit evidence.
        for owner in state.list():
            if owner.runtime_state != "stopped":
                state.set_runtime(owner.owner_id, "unknown")

        async def reaper():
            while True:
                await asyncio.sleep(60)
                try:
                    await sweep_idle()
                except (httpx.HTTPError, SandboxConflict):
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
            if database:
                database.close()

    app = FastAPI(
        title="PSKit Sandbox Manager",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.exception_handler(SandboxConflict)
    async def conflict(_request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/v1/sandboxes/ensure")
    async def ensure(request: EnsureSandbox, authorization: str | None = Header(default=None)):
        authorize(authorization)
        digest, name, volume = identity(request.user_id)
        async with locks.setdefault(request.user_id, asyncio.Lock()):
            owner = state.ensure_owner(request.user_id, name, volume, settings.image)
            if owner.instance_id != name or owner.volume_id != volume:
                raise HTTPException(status_code=409, detail="Sandbox namespace or owner mismatch")
            if owner.state != "ready" or owner.runtime_state == "stopping":
                raise HTTPException(status_code=409, detail="Sandbox is draining")
            async with docker_client() as docker:
                try:
                    network = await docker.get(f"{DOCKER_API}/networks/{quote(settings.network)}")
                    network.raise_for_status()
                    if network.json().get("Internal") is not True:
                        raise HTTPException(
                            status_code=503, detail="Sandbox network must be private"
                        )
                    labels = {
                        "pskit.sandbox.namespace": settings.namespace,
                        "pskit.sandbox.owner_hash": digest,
                    }
                    existing = await docker.get(f"{DOCKER_API}/volumes/{quote(volume)}")
                    if existing.status_code == 404:
                        created = await docker.post(
                            f"{DOCKER_API}/volumes/create", json={"Name": volume, "Labels": labels}
                        )
                        created.raise_for_status()
                    else:
                        existing.raise_for_status()
                        volume_labels = existing.json().get("Labels") or {}
                        if any(volume_labels.get(key) != value for key, value in labels.items()):
                            raise HTTPException(
                                status_code=409, detail="Sandbox volume owner mismatch"
                            )
                    inspected = await docker.get(f"{DOCKER_API}/containers/{name}/json")
                    if inspected.status_code == 404:
                        config = {
                            "Image": owner.image_digest,
                            "Cmd": [
                                "python",
                                "-m",
                                "uvicorn",
                                "app.sandbox_bridge:create_app",
                                "--factory",
                                "--host",
                                "0.0.0.0",
                                "--port",
                                "8091",
                            ],
                            "User": "10001:10001",
                            "WorkingDir": "/workspace",
                            "Env": [
                                f"PSKIT_SANDBOX_USER_ID={request.user_id}",
                                f"PSKIT_SANDBOX_BRIDGE_TOKEN={token(request.user_id)}",
                                "RESEARCH_AGENT_PI_SESSION_DIR=/workspace/sessions",
                                "PYTHONPATH=/app",
                                f"RESEARCH_AGENT_INTERNAL_API_URL={settings.gateway_url}",
                            ],
                            "Labels": labels,
                            "HostConfig": {
                                "NetworkMode": settings.network,
                                "Mounts": [
                                    {"Type": "volume", "Source": volume, "Target": "/workspace"}
                                ],
                                "ReadonlyRootfs": True,
                                "Tmpfs": {"/tmp": "rw,noexec,nosuid,size=64m"},
                                "Memory": settings.memory_bytes,
                                "NanoCpus": settings.nano_cpus,
                                "PidsLimit": settings.pids_limit,
                                "CapDrop": ["ALL"],
                                "SecurityOpt": ["no-new-privileges"],
                            },
                        }
                        created = await docker.post(
                            f"{DOCKER_API}/containers/create", params={"name": name}, json=config
                        )
                        created.raise_for_status()
                        container_id, running = created.json()["Id"], False
                    else:
                        inspected.raise_for_status()
                        data = inspected.json()
                        container_labels = data.get("Config", {}).get("Labels") or {}
                        if data.get("Config", {}).get("Image") != owner.image_digest or any(
                            container_labels.get(key) != value for key, value in labels.items()
                        ):
                            raise HTTPException(
                                status_code=409, detail="Sandbox image or owner mismatch"
                            )
                        container_id = data["Id"]
                        running = data.get("State", {}).get("Running") is True
                    if not running:
                        response = await docker.post(
                            f"{DOCKER_API}/containers/{container_id}/start"
                        )
                        response.raise_for_status()
                    state.mark_ensured(request.user_id, started=not running)
                except httpx.HTTPError as exc:
                    raise HTTPException(
                        status_code=503, detail="Docker sandbox unavailable"
                    ) from exc
        return {"base_url": f"http://{name}:8091", "token": token(request.user_id)}

    @app.api_route("/v1/sandboxes/{owner_id}/bridge/{path:path}", methods=["GET", "POST"])
    async def bridge_proxy(
        owner_id: str, path: str, request: Request, authorization: str | None = Header(default=None)
    ):
        authorize(authorization)
        owner = state.owner(owner_id)
        if not owner:
            raise HTTPException(status_code=404, detail="Sandbox not found")
        allowed = (request.method, path) in {
            ("GET", "health"),
            ("GET", "v1/pi/activity"),
            ("POST", "v1/pi/prompt"),
            ("POST", "v1/workspace/files"),
            ("GET", "v1/workspace/artifacts"),
        }
        allowed = (
            allowed
            or re.fullmatch(r"v1/pi/attempts/[A-Za-z0-9_-]{1,128}(?:/cancel)?", path) is not None
        )
        if not allowed:
            raise HTTPException(status_code=404, detail="Bridge route not found")
        chunks = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > 128 * 1024 * 1024:
                raise HTTPException(status_code=413, detail="Bridge request exceeds limit")
            chunks.append(chunk)
        content = b"".join(chunks)
        transfer_lease = None
        heartbeat = None
        if path in {"v1/workspace/files", "v1/workspace/artifacts"}:
            try:
                session_id = (
                    json.loads(content)["session_id"]
                    if request.method == "POST"
                    else request.query_params["session_id"]
                )
                session_id = TypeAdapter(Identity).validate_python(session_id)
            except (ValueError, KeyError, TypeError) as exc:
                raise HTTPException(status_code=422, detail="Invalid transfer session") from exc
            transfer_lease = state.acquire(
                owner_id,
                session_id,
                "transfer-" + uuid.uuid4().hex,
                60,
                purpose="transfer",
                continuation_run_id=request.headers.get("X-PSKit-Continuation-Attempt"),
            )

            async def renew_transfer():
                while True:
                    await asyncio.sleep(20)
                    state.renew(transfer_lease.lease_id, transfer_lease.fencing_token)

            heartbeat = asyncio.create_task(renew_transfer())
        client = httpx.AsyncClient(
            base_url=f"http://{owner.instance_id}:8091",
            transport=bridge_transport,
            timeout=None,
            trust_env=False,
        )
        try:
            headers = {
                "Authorization": f"Bearer {token(owner_id)}",
                "Content-Type": request.headers.get("Content-Type", "application/json"),
            }
            if transfer_lease:
                headers["X-PSKit-Transfer-Attempt"] = transfer_lease.run_id
            forwarded = client.build_request(
                request.method,
                "/" + path,
                params=request.query_params,
                content=content,
                headers=headers,
            )
            response = await client.send(forwarded, stream=True)
        except BaseException as exc:
            await client.aclose()
            if heartbeat:
                heartbeat.cancel()
                try:
                    await heartbeat
                except asyncio.CancelledError:
                    pass
            if transfer_lease and isinstance(exc, httpx.ConnectError):
                state.release(transfer_lease.lease_id, transfer_lease.fencing_token)
            if not isinstance(exc,httpx.HTTPError):
                raise
            raise HTTPException(status_code=503, detail="Sandbox bridge unavailable") from exc

        async def body():
            completed = False
            try:
                async for chunk in response.aiter_raw():
                    yield chunk
                completed = True
            finally:
                await response.aclose()
                await client.aclose()
                if heartbeat:
                    heartbeat.cancel()
                    try:
                        await heartbeat
                    except asyncio.CancelledError:
                        pass
                if transfer_lease and completed:
                    state.release(transfer_lease.lease_id, transfer_lease.fencing_token)
                    for operation in state.pending_replacements():
                        async with locks.setdefault(operation.owner_id, asyncio.Lock()):
                            try:
                                await replace(operation)
                            except (httpx.HTTPError, HTTPException, SandboxConflict):
                                state.set_runtime(operation.owner_id, "unknown")

        return StreamingResponse(
            body(),
            status_code=response.status_code,
            media_type=response.headers.get("content-type", "application/json"),
        )

    @app.post("/v1/sandboxes/activity/acquire", response_model=SandboxLease)
    async def acquire(payload: AcquireActivity, authorization: str | None = Header(default=None)):
        authorize(authorization)
        return state.acquire(
            payload.owner_id, payload.session_id, payload.run_id, payload.lease_seconds
        )

    @app.post("/v1/sandboxes/activity/renew", response_model=SandboxLease)
    async def renew(payload: LeaseFence, authorization: str | None = Header(default=None)):
        authorize(authorization)
        return state.renew(payload.lease_id, payload.fencing_token)

    @app.post("/v1/sandboxes/activity/release")
    async def release(payload: LeaseFence, authorization: str | None = Header(default=None)):
        authorize(authorization)
        state.release(payload.lease_id, payload.fencing_token)
        for operation in state.pending_replacements():
            async with locks.setdefault(operation.owner_id, asyncio.Lock()):
                try:
                    await replace(operation)
                except (httpx.HTTPError, HTTPException, SandboxConflict):
                    state.set_runtime(operation.owner_id, "unknown")
        return {"released": True}

    @app.post("/v1/sandboxes/sweep")
    async def sweep(authorization: str | None = Header(default=None)):
        authorize(authorization)
        return {"stopped": await sweep_idle()}

    @app.get("/v1/sandboxes", response_model=list[SandboxSummary])
    async def listing(authorization: str | None = Header(default=None)):
        authorize(authorization)
        return state.list()

    @app.post("/v1/sandboxes/{owner_id}/drain", response_model=SandboxOperation)
    async def drain(
        owner_id: str, payload: DrainRequest, authorization: str | None = Header(default=None)
    ):
        authorize(authorization)
        return state.drain(owner_id, payload.expected_revision)

    @app.post("/v1/sandboxes/{owner_id}/replace", response_model=SandboxOperation)
    async def replacing(
        owner_id: str, payload: ReplaceRequest, authorization: str | None = Header(default=None)
    ):
        authorize(authorization)
        if not re.fullmatch(r"(?:[^\s]+@)?sha256:[a-f0-9]{64}", payload.image_digest):
            raise HTTPException(status_code=422, detail="Replacement requires a fixed image digest")
        async with locks.setdefault(owner_id, asyncio.Lock()):
            operation = state.replacement(owner_id, payload.image_digest)
            try:
                return await replace(operation)
            except httpx.HTTPError as exc:
                state.set_runtime(owner_id, "unknown")
                raise HTTPException(
                    status_code=503, detail="Replacement incomplete; volume preserved"
                ) from exc

    @app.get("/v1/sandboxes/{owner_id}/usage", response_model=SandboxMetrics)
    async def usage(owner_id: str, authorization: str | None = Header(default=None)):
        authorize(authorization)
        owner = state.owner(owner_id)
        if not owner:
            raise HTTPException(status_code=404, detail="Sandbox not found")
        async with docker_client() as docker:
            response = await docker.get(
                f"{DOCKER_API}/containers/{owner.instance_id}/stats", params={"stream": "false"}
            )
            response.raise_for_status()
            data = response.json()
        total = data.get("cpu_stats", {}).get("cpu_usage", {}).get("total_usage")
        memory = data.get("memory_stats", {}).get("usage")
        return SandboxMetrics(
            owner_id=owner_id,
            cpu_core_ms=total // 1000000 if total is not None else None,
            memory_bytes=memory,
            sampled_at=clock(),
        )

    return app


def create_app() -> FastAPI:
    """Load private Compose configuration for the manager service."""
    return create_manager_app(SandboxManagerSettings.from_env())
