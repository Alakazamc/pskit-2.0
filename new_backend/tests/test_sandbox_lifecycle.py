"""Sandbox lifecycle through the authenticated manager consumer boundary."""

import json

import httpx
import pytest

from app.domain.sandboxes import MemorySandboxActivityStore
from app.sandbox_manager import SandboxManagerSettings, create_manager_app

AUTH = {"Authorization": "Bearer manager-secret-123456"}


class DockerDouble:
    def __init__(self):
        self.volumes = {}
        self.containers = {}
        self.deleted_volumes = []

    def __call__(self, request):
        path, method = request.url.path, request.method
        if "/networks/" in path:
            return httpx.Response(200, json={"Internal": True})
        if path.endswith("/volumes/create"):
            config = json.loads(request.content)
            self.volumes[config["Name"]] = config
            return httpx.Response(201, json=config)
        if "/volumes/" in path:
            name = path.rsplit("/", 1)[1]
            if method == "DELETE":
                self.deleted_volumes.append(name)
            return httpx.Response(
                200 if name in self.volumes else 404, json=self.volumes.get(name, {})
            )
        if path.endswith("/containers/json"):
            return httpx.Response(
                200,
                json=[
                    {"Names": ["/" + name], "Labels": data["Config"]["Labels"]}
                    for name, data in self.containers.items()
                ],
            )
        if path.endswith("/containers/create"):
            config = json.loads(request.content)
            name = request.url.params["name"]
            self.containers[name] = {"Id": name, "Config": config, "State": {"Running": False}}
            return httpx.Response(201, json={"Id": name})
        name = path.split("/")[3]
        if path.endswith("/json"):
            return httpx.Response(
                200 if name in self.containers else 404, json=self.containers.get(name, {})
            )
        if method == "DELETE":
            del self.containers[name]
            return httpx.Response(204)
        if path.endswith("/stats"):
            return httpx.Response(
                200,
                json={
                    "cpu_stats": {"cpu_usage": {"total_usage": 3000000}},
                    "memory_stats": {"usage": 1024},
                },
            )
        if path.endswith(("/start", "/stop")):
            self.containers[name]["State"]["Running"] = path.endswith("/start")
            return httpx.Response(204)
        raise AssertionError(str(request.url))


def settings():
    return SandboxManagerSettings(
        auth_token="manager-secret-123456",
        bridge_secret="derived-token-secret",
        image="pskit-agent:fixed",
        network="sandbox_private",
        namespace="test",
    )


def bridge_idle(request):
    return httpx.Response(200, json={"boot_id": "boot", "attempts": []})


@pytest.mark.asyncio
async def test_active_turn_survives_idle_sweep():
    clock = [0.0]
    docker = DockerDouble()
    state = MemorySandboxActivityStore(clock=lambda: clock[0])
    app = create_manager_app(
        settings(),
        activity_store=state,
        docker_transport=httpx.MockTransport(docker),
        bridge_transport=httpx.MockTransport(bridge_idle),
        clock=lambda: clock[0],
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://manager", headers=AUTH
    ) as client:
        await client.post("/v1/sandboxes/ensure", json={"user_id": "alice"})
        lease = (
            await client.post(
                "/v1/sandboxes/activity/acquire",
                json={
                    "owner_id": "alice",
                    "session_id": "one",
                    "run_id": "run",
                    "lease_seconds": 60,
                },
            )
        ).json()
        clock[0] = 1801
        assert (await client.post("/v1/sandboxes/sweep")).json() == {"stopped": 0}
        assert (
            await client.post(
                "/v1/sandboxes/activity/release",
                json={
                    "lease_id": lease["lease_id"],
                    "fencing_token": lease["fencing_token"],
                },
            )
        ).status_code == 200
        clock[0] += 1801
        assert (await client.post("/v1/sandboxes/sweep")).json() == {"stopped": 1}
    assert docker.deleted_volumes == []


@pytest.mark.asyncio
async def test_same_user_sessions_reuse_volume():
    docker = DockerDouble()
    app = create_manager_app(
        settings(),
        activity_store=MemorySandboxActivityStore(),
        docker_transport=httpx.MockTransport(docker),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://manager", headers=AUTH
    ) as client:
        for user in ["alice", "alice", "bob"]:
            assert (
                await client.post("/v1/sandboxes/ensure", json={"user_id": user})
            ).status_code == 200
    assert len(docker.containers) == len(docker.volumes) == 2
