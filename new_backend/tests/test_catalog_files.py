import asyncio

import httpx
import pytest

from app.config import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_catalog_files_and_artifacts_are_scoped_to_user():
    app = create_app(Settings(mock_af3_seconds=0.1))
    async with app.router.lifespan_context(app):  # noqa: SIM117 - Start scheduler first.
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            alice = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
            bob = (await client.post("/api/v1/auth/demo", json={"email": "bob@example.org"})).json()
            alice_headers = {"Authorization": f"Bearer {alice['access_token']}"}
            bob_headers = {"Authorization": f"Bearer {bob['access_token']}"}
            skills = await client.get("/api/v1/skills", headers=alice_headers)
            resources = await client.get("/api/v1/resources", headers=alice_headers)
            uploaded = await client.post(
                "/api/v1/files", headers=alice_headers,
                json={"name": "notes.txt", "size": 120, "content": "notes\n" * 20},
            )
            rejected_pdf = await client.post(
                "/api/v1/files", headers=alice_headers,
                json={"name": "paper.pdf", "size": 4, "content": "fake"},
            )
            rejected_size = await client.post(
                "/api/v1/files", headers=alice_headers,
                json={"name": "data.csv", "size": 100, "content": "x"},
            )
            rejected_large = await client.post(
                "/api/v1/files", headers=alice_headers,
                json={"name": "large.txt", "size": 1_048_577, "content": "x" * 1_048_577},
            )
            alice_files = await client.get("/api/v1/files", headers=alice_headers)
            bob_files = await client.get("/api/v1/files", headers=bob_headers)
            await client.post("/api/v1/af3/jobs", headers=alice_headers, json={})
            for _ in range(50):
                alice_artifacts = await client.get("/api/v1/artifacts", headers=alice_headers)
                if alice_artifacts.json():
                    break
                await asyncio.sleep(0.02)
            bob_artifacts = await client.get("/api/v1/artifacts", headers=bob_headers)

    assert skills.status_code == resources.status_code == uploaded.status_code == 200
    assert {item["id"] for item in skills.json()} == {"structure-review"}
    assert {item["id"] for item in resources.json()} == {"search_pdb", "fetch_uniprot"}
    assert skills.json()[0]["description"].startswith("Find and inspect protein structures")
    assert next(item for item in resources.json() if item["id"] == "search_pdb")["description"] == "检索 PDB 中的结构"
    assert uploaded.json()["status"] == "ready"
    assert uploaded.json()["id"].startswith("file-")
    assert uploaded.json() in alice_files.json()
    assert rejected_pdf.status_code == 415
    assert rejected_pdf.json()["detail"]["code"] == "UNSUPPORTED_FILE_TYPE"
    assert rejected_size.status_code == 422
    assert rejected_size.json()["detail"]["code"] == "FILE_SIZE_MISMATCH"
    assert rejected_large.status_code == 413
    assert rejected_large.json()["detail"]["code"] == "FILE_TOO_LARGE"
    assert len(alice_files.json()) == 1
    assert bob_files.json() == []
    assert alice_artifacts.json()[0]["name"] == "af3_prediction.cif"
    assert bob_artifacts.json() == []
