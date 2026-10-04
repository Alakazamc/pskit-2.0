"""The private lifecycle API gives each user one reusable, persistent sandbox."""

import hashlib
import json

import httpx
import pytest
from test_sandbox_lifecycle import bridge_idle

from app.domain.sandboxes import MemorySandboxActivityStore
from app.sandbox_manager import SandboxManagerSettings, create_manager_app


@pytest.mark.asyncio
async def test_manager_reuses_one_private_container_and_volume_per_user():
    volumes: dict[str, dict] = {}
    containers: dict[str, dict] = {}
    creates: list[dict] = []
    clock = [0.0]

    def docker(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/v1.45/networks/pskit-agent-staging_app":
            return httpx.Response(200, json={"Internal": True})
        if request.method == "GET" and path.startswith("/v1.45/volumes/"):
            return httpx.Response(
                200 if path.rsplit("/", 1)[-1] in volumes else 404,
                json=volumes.get(path.rsplit("/", 1)[-1], {}),
            )
        if request.method == "POST" and path == "/v1.45/volumes/create":
            volume = json.loads(request.content)["Name"]
            volumes[volume] = json.loads(request.content)
            return httpx.Response(201, json={"Name": volume})
        if request.method == "GET" and path.startswith("/v1.45/containers/"):
            name = path.split("/")[3]
            return (
                httpx.Response(200, json=containers[name])
                if name in containers
                else httpx.Response(404, json={"message": "not found"})
            )
        if request.method == "POST" and path == "/v1.45/containers/create":
            name = request.url.params["name"]
            config = json.loads(request.content)
            creates.append(config)
            containers[name] = {
                "Id": name,
                "State": {"Running": False},
                "Config": {"Image": config["Image"], "Labels": config["Labels"]},
            }
            return httpx.Response(201, json={"Id": name})
        if request.method == "POST" and path.endswith("/start"):
            name = path.split("/")[3]
            containers[name]["State"]["Running"] = True
            return httpx.Response(204)
        if request.method == "POST" and path.endswith("/stop"):
            name = path.split("/")[3]
            containers[name]["State"]["Running"] = False
            return httpx.Response(204)
        raise AssertionError(f"Unexpected Docker request: {request.method} {path}")

    settings = SandboxManagerSettings(
        auth_token="manager-secret-123456",
        bridge_secret="derived-token-secret",
        image="pskit-agent-backend:20261004",
        network="pskit-agent-staging_app",
        namespace="staging",
    )
    app = create_manager_app(
        settings,
        activity_store=MemorySandboxActivityStore(clock=lambda: clock[0]),
        bridge_transport=httpx.MockTransport(bridge_idle),
        docker_transport=httpx.MockTransport(docker),
        clock=lambda: clock[0],
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://manager"
    ) as client:
        unauthorized = await client.post("/v1/sandboxes/ensure", json={"user_id": "alice"})
        assert unauthorized.status_code == 401
        first = await client.post(
            "/v1/sandboxes/ensure",
            json={"user_id": "alice"},
            headers={"Authorization": "Bearer manager-secret-123456"},
        )
        second = await client.post(
            "/v1/sandboxes/ensure",
            json={"user_id": "alice"},
            headers={"Authorization": "Bearer manager-secret-123456"},
        )
        other = await client.post(
            "/v1/sandboxes/ensure",
            json={"user_id": "bob"},
            headers={"Authorization": "Bearer manager-secret-123456"},
        )
        clock[0] = 1801.0
        stopped = await client.post(
            "/v1/sandboxes/sweep", headers={"Authorization": "Bearer manager-secret-123456"}
        )
        resumed = await client.post(
            "/v1/sandboxes/ensure",
            json={"user_id": "alice"},
            headers={"Authorization": "Bearer manager-secret-123456"},
        )

    assert first.status_code == second.status_code == other.status_code == 200
    assert first.json() == second.json()
    assert stopped.json() == {"stopped": 2}
    assert resumed.json() == first.json()
    assert first.json()["base_url"].startswith("http://pskit-sbx-staging-")
    assert first.json()["base_url"] != other.json()["base_url"]
    assert first.json()["token"] != other.json()["token"]
    assert len(volumes) == len(containers) == len(creates) == 2
    config = creates[0]
    assert config["HostConfig"]["NetworkMode"] == "pskit-agent-staging_app"
    assert config["HostConfig"]["ReadonlyRootfs"] is True
    assert config["HostConfig"]["Mounts"][0]["Target"] == "/workspace"
    assert config["HostConfig"]["Mounts"][0]["Type"] == "volume"
    assert config["HostConfig"].get("PortBindings") is None
    assert config["HostConfig"]["CapDrop"] == ["ALL"]
    assert all("MANAGER" not in item for item in config["Env"])


@pytest.mark.asyncio
async def test_manager_rejects_a_non_private_docker_network():
    def docker(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1.45/networks/public_net":
            return httpx.Response(200, json={"Internal": False})
        raise AssertionError(f"Unexpected Docker request: {request.method} {request.url.path}")

    app = create_manager_app(
        SandboxManagerSettings(
            auth_token="manager-secret-123456",
            bridge_secret="derived-token-secret",
            image="pskit-agent-backend:20261004",
            network="public_net",
            namespace="staging",
        ),
        activity_store=MemorySandboxActivityStore(),
        docker_transport=httpx.MockTransport(docker),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://manager"
    ) as client:
        response = await client.post(
            "/v1/sandboxes/ensure",
            json={"user_id": "alice"},
            headers={"Authorization": "Bearer manager-secret-123456"},
        )
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_manager_restart_still_reaps_previous_idle_sandbox():
    identity = hashlib.sha256(b"staging:alice").hexdigest()[:24]
    name = f"pskit-sbx-staging-{identity}"
    stopped = []
    clock = [0.0]

    def docker(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/v1.45/containers/json":
            return httpx.Response(
                200,
                json=[
                    {
                        "Names": [f"/{name}"],
                        "State": "running",
                        "Labels": {"pskit.sandbox.namespace": "staging"},
                    }
                ],
            )
        if request.method == "POST" and request.url.path == f"/v1.45/containers/{name}/stop":
            stopped.append(name)
            return httpx.Response(204)
        raise AssertionError(str(request.url))

    state = MemorySandboxActivityStore(clock=lambda: clock[0])
    state.ensure_owner("alice", name, name + "-workspace", "pskit-agent-backend:20261004")
    state.set_runtime("alice", "running")
    app = create_manager_app(
        SandboxManagerSettings(
            auth_token="manager-secret-123456",
            bridge_secret="derived-token-secret",
            image="pskit-agent-backend:20261004",
            network="pskit-agent-staging_app",
            namespace="staging",
        ),
        activity_store=state,
        bridge_transport=httpx.MockTransport(bridge_idle),
        docker_transport=httpx.MockTransport(docker),
        clock=lambda: clock[0],
    )
    async with app.router.lifespan_context(app):
        clock[0] = 1801.0
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://manager"
        ) as client:
            response = await client.post(
                "/v1/sandboxes/sweep",
                headers={"Authorization": "Bearer manager-secret-123456"},
            )
    assert response.json() == {"stopped": 1}
    assert stopped == [name]
