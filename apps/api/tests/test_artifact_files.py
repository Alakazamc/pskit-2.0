import hashlib

import httpx
import pytest

from app.config import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_compute_artifact_upload_is_hashed_idempotent_and_owner_scoped(tmp_path):
    app = create_app(Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        af3_executor="callback", compute_callback_key="compute-secret",
    ), pi_runner=object())
    artifact_bytes = b"data_mock_structure\n# test fixture only\n"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        alice = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        bob = (await client.post("/api/v1/auth/demo", json={"email": "bob@example.org"})).json()
        a = {"Authorization": f"Bearer {alice['access_token']}"}
        b = {"Authorization": f"Bearer {bob['access_token']}"}
        job = (await client.post("/api/v1/af3/jobs", headers=a,
                                 json={"estimated_gpu_minutes": 20})).json()
        path = f"/internal/af3/jobs/{job['id']}/artifacts/artifact-1"
        params = {"name": "prediction.cif", "kind": "structure"}
        unauthorized = await client.put(path, params=params, content=artifact_bytes)
        uploaded = await client.put(path, params=params, content=artifact_bytes,
                                    headers={"X-Compute-Key": "compute-secret"})
        repeated = await client.put(path, params=params, content=artifact_bytes,
                                    headers={"X-Compute-Key": "compute-secret"})
        conflict = await client.put(path, params=params, content=b"changed",
                                    headers={"X-Compute-Key": "compute-secret"})
        mismatch = await client.post(f"/internal/af3/jobs/{job['id']}/result",
                                     headers={"X-Compute-Key": "compute-secret"}, json={
                                         "status": "completed", "actual_gpu_minutes": 18,
                                         "artifacts": [{"id": "artifact-1", "name": "different.cif",
                                                        "kind": "structure"}],
                                     })
        callback = await client.post(f"/internal/af3/jobs/{job['id']}/result",
                                     headers={"X-Compute-Key": "compute-secret"}, json={
                                         "status": "completed", "actual_gpu_minutes": 18,
                                         "artifacts": [{"id": "artifact-1", "name": "prediction.cif",
                                                        "kind": "structure"}],
                                     })
        repeated_after_completion = await client.put(
            path, params=params, content=artifact_bytes,
            headers={"X-Compute-Key": "compute-secret"},
        )
        listed = await client.get("/api/v1/artifacts", headers=a)
        foreign = await client.get("/api/v1/artifacts/artifact-1/download", headers=b)
        download = await client.get("/api/v1/artifacts/artifact-1/download", headers=a)
        foreign_preview = await client.get("/api/v1/artifacts/artifact-1/preview", headers=b)
        preview = await client.get("/api/v1/artifacts/artifact-1/preview", headers=a)

    assert unauthorized.status_code == 404
    assert uploaded.status_code == repeated.status_code == 200
    assert uploaded.json()["sha256"] == hashlib.sha256(artifact_bytes).hexdigest()
    assert uploaded.json()["size"] == len(artifact_bytes)
    assert conflict.status_code == 409
    assert mismatch.status_code == 409
    assert callback.status_code == 200
    assert repeated_after_completion.status_code == 200
    assert listed.json() == [{
        "id": "artifact-1", "name": "prediction.cif", "kind": "structure",
        "available": True, "size": len(artifact_bytes),
        "sha256": hashlib.sha256(artifact_bytes).hexdigest(),
    }]
    assert foreign.status_code == 404
    assert download.status_code == 200 and download.content == artifact_bytes
    assert "attachment" in download.headers["content-disposition"]
    assert foreign_preview.status_code == 404
    assert preview.status_code == 200
    assert preview.json() == {
        "id": "artifact-1", "name": "prediction.cif", "kind": "structure",
        "text": artifact_bytes.decode("utf-8"),
    }


@pytest.mark.asyncio
async def test_artifact_preview_rejects_large_and_binary_files(tmp_path):
    app = create_app(Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        af3_executor="callback", compute_callback_key="compute-secret",
    ), pi_runner=object())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        login = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        user = {"Authorization": f"Bearer {login['access_token']}"}
        worker = {"X-Compute-Key": "compute-secret"}
        job = (await client.post("/api/v1/af3/jobs", headers=user,
                                 json={"estimated_gpu_minutes": 20})).json()
        artifacts = [
            ("large", "report.txt", b"x" * (256 * 1024 + 1)),
            ("binary", "figure.png", b"\x89PNG\x00"),
            ("invalid", "broken.txt", b"\xff"),
        ]
        for artifact_id, name, content in artifacts:
            uploaded = await client.put(
                f"/internal/af3/jobs/{job['id']}/artifacts/{artifact_id}",
                params={"name": name, "kind": "output"}, content=content, headers=worker,
            )
            assert uploaded.status_code == 200, uploaded.text
        completed = await client.post(
            f"/internal/af3/jobs/{job['id']}/result", headers=worker,
            json={"status": "completed", "actual_gpu_minutes": 1, "artifacts": [
                {"id": artifact_id, "name": name, "kind": "output"}
                for artifact_id, name, _ in artifacts
            ]},
        )
        assert completed.status_code == 200, completed.text
        large = await client.get("/api/v1/artifacts/large/preview", headers=user)
        binary = await client.get("/api/v1/artifacts/binary/preview", headers=user)
        invalid = await client.get("/api/v1/artifacts/invalid/preview", headers=user)
        downloaded = await client.get("/api/v1/artifacts/large/download", headers=user)
    assert large.status_code == 413
    assert large.json()["detail"]["code"] == "ARTIFACT_PREVIEW_TOO_LARGE"
    assert binary.status_code == invalid.status_code == 415
    assert binary.json()["detail"]["code"] == "ARTIFACT_PREVIEW_UNSUPPORTED"
    assert invalid.json()["detail"]["code"] == "ARTIFACT_PREVIEW_UNSUPPORTED"
    assert downloaded.status_code == 200
