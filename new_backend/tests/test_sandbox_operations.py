"""Admin sandbox operations use typed HTTP ports and never delete user volumes."""

import httpx
import pytest
from test_sandbox_lifecycle import AUTH, DockerDouble, settings

from app.domain.sandboxes import MemorySandboxActivityStore
from app.sandbox_manager import create_manager_app
from app.services.sandbox_operations import SandboxOperations

DIGEST = "pskit-agent@sha256:" + "a" * 64


@pytest.mark.asyncio
async def test_image_replace_waits_for_active_attempts():
    docker = DockerDouble()
    store = MemorySandboxActivityStore()
    attempts = []

    def bridge(_):
        return httpx.Response(200, json={"boot_id": "boot", "attempts": attempts})

    app = create_manager_app(
        settings(),
        activity_store=store,
        docker_transport=httpx.MockTransport(docker),
        bridge_transport=httpx.MockTransport(bridge),
    )
    operations = SandboxOperations(
        "http://manager", "manager-secret-123456", transport=httpx.ASGITransport(app=app)
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
                    "run_id": "attempt",
                    "lease_seconds": 60,
                },
            )
        ).json()
        summary = (await operations.list())[0]
        volume = summary.volume_id
        assert (await operations.drain("alice", summary.revision)).state == "draining"
        assert (
            await client.post("/v1/sandboxes/ensure", json={"user_id": "alice"})
        ).status_code == 409
        assert (await operations.replace_keep_volume("alice", DIGEST)).state == "waiting"
        await client.post(
            "/v1/sandboxes/activity/release",
            json={"lease_id": lease["lease_id"], "fencing_token": lease["fencing_token"]},
        )
        assert (await operations.replace_keep_volume("alice", DIGEST)).state == "completed"
        assert (
            await client.post("/v1/sandboxes/ensure", json={"user_id": "alice"})
        ).status_code == 200
        replaced = (await operations.list())[0]
        assert replaced.volume_id == volume
        assert replaced.image_digest == DIGEST
        assert (await operations.read_usage("alice")).cpu_core_ms == 3
    assert docker.deleted_volumes == []
    assert (
        next(iter(docker.containers.values()))["Config"]["HostConfig"]["Mounts"][0]["Source"]
        == volume
    )


@pytest.mark.asyncio
async def test_owner_mismatch_never_reuses_volume():
    docker = DockerDouble()
    app = create_manager_app(
        settings(),
        activity_store=MemorySandboxActivityStore(),
        docker_transport=httpx.MockTransport(docker),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://manager", headers=AUTH
    ) as client:
        assert (
            await client.post("/v1/sandboxes/ensure", json={"user_id": "alice"})
        ).status_code == 200
        volume = next(iter(docker.volumes.values()))
        volume["Labels"]["pskit.sandbox.owner_hash"] = "other"
        assert (
            await client.post("/v1/sandboxes/ensure", json={"user_id": "alice"})
        ).status_code == 409
    assert docker.deleted_volumes == []


@pytest.mark.asyncio
async def test_sandbox_summary_has_no_credentials():
    docker = DockerDouble()
    app = create_manager_app(
        settings(),
        activity_store=MemorySandboxActivityStore(),
        docker_transport=httpx.MockTransport(docker),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://manager", headers=AUTH
    ) as client:
        await client.post("/v1/sandboxes/ensure", json={"user_id": "alice"})
        data = (await client.get("/v1/sandboxes")).json()[0]
        assert set(data) == {
            "owner_id",
            "instance_id",
            "volume_id",
            "image_digest",
            "state",
            "runtime_state",
            "active_sessions",
            "revision",
            "last_completed_at",
        }
        assert "secret" not in str(data)


@pytest.mark.asyncio
async def test_pending_replacement_completes_after_confirmed_exit():
    docker = DockerDouble()
    app = create_manager_app(
        settings(),
        activity_store=MemorySandboxActivityStore(),
        docker_transport=httpx.MockTransport(docker),
        bridge_transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"boot_id": "boot", "attempts": []})
        ),
    )
    operations = SandboxOperations(
        "http://manager", "manager-secret-123456", transport=httpx.ASGITransport(app=app)
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
                    "run_id": "attempt",
                    "lease_seconds": 60,
                },
            )
        ).json()
        await operations.drain("alice", (await operations.list())[0].revision)
        assert (await operations.replace_keep_volume("alice", DIGEST)).state == "waiting"
        await client.post(
            "/v1/sandboxes/activity/release",
            json={"lease_id": lease["lease_id"], "fencing_token": lease["fencing_token"]},
        )
        summary = (await operations.list())[0]
        assert summary.state == "ready"
        assert summary.image_digest == DIGEST
        assert summary.runtime_state == "stopped"


def test_replacement_claim_fences_old_manager_before_runtime_admission():
    from app.domain.sandboxes import SandboxConflict

    state = MemorySandboxActivityStore()
    state.ensure_owner("alice", "sandbox", "volume", "image:fixed")
    state.set_runtime("alice", "running")
    state.drain("alice", 0)
    operation = state.replacement("alice", DIGEST)
    claimed = state.claim_replacement(operation)
    with pytest.raises(SandboxConflict):
        state.claim_replacement(operation)
    with pytest.raises(SandboxConflict):
        state.acquire("alice", "one", "attempt", 60)
    state.complete_replacement(claimed)
    with pytest.raises(SandboxConflict):
        state.claim_replacement(claimed)


@pytest.mark.asyncio
async def test_inherited_image_labels_do_not_break_owned_container_reuse():
    docker = DockerDouble()
    app = create_manager_app(
        settings(),
        activity_store=MemorySandboxActivityStore(),
        docker_transport=httpx.MockTransport(docker),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://manager", headers=AUTH
    ) as client:
        assert (
            await client.post("/v1/sandboxes/ensure", json={"user_id": "alice"})
        ).status_code == 200
        next(iter(docker.containers.values()))["Config"]["Labels"]["com.docker.compose.project"] = (
            "image-build-metadata"
        )
        assert (
            await client.post("/v1/sandboxes/ensure", json={"user_id": "alice"})
        ).status_code == 200
