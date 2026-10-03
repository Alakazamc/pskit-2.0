"""Uploaded images must reach only image-capable Pi models for their owner."""

import asyncio
import base64

import httpx
import pytest

from app.config import Settings
from app.main import create_app
from app.services.model_catalog import ModelCatalog

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
)


@pytest.mark.asyncio
async def test_image_upload_requires_valid_bytes_and_is_user_scoped():
    app = create_app(Settings())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://backend") as client:
        alice = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        bob = (await client.post("/api/v1/auth/demo", json={"email": "bob@example.org"})).json()
        alice_headers = {"Authorization": f"Bearer {alice['access_token']}"}
        bob_headers = {"Authorization": f"Bearer {bob['access_token']}"}
        invalid = await client.put("/api/v1/files/content?name=image.png",
                                   content=b"not an image", headers=alice_headers)
        uploaded = await client.put("/api/v1/files/content?name=image.png",
                                    content=PNG, headers=alice_headers)
        file_id = uploaded.json()["id"]
        own = await client.get(f"/api/v1/files/{file_id}/download", headers=alice_headers)
        other = await client.get(f"/api/v1/files/{file_id}/download", headers=bob_headers)
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "INVALID_IMAGE"
    assert uploaded.status_code == 200
    assert own.content == PNG
    assert other.status_code == 404


@pytest.mark.asyncio
async def test_image_is_rejected_for_text_model_and_forwarded_to_pi_for_vision(tmp_path):
    class RecordingPi:
        images = None

        async def prompt(self, session_id, message, on_event, **kwargs):
            self.images = kwargs.get("images")
            return {"session_file": f"/tmp/{session_id}.jsonl", "text": "看到了图片"}

    def gateway(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [
                {"id": "text-model"}, {"id": "vision-model"},
            ]})
        return httpx.Response(403)

    runner = RecordingPi()
    app = create_app(Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
                              model_gateway_model="text-model"), pi_runner=runner)
    app.state.model_catalog = ModelCatalog(
        base_url="http://gateway:4000", api_key="server-secret",
        default_model="text-model", image_model_ids={"vision-model"},
        transport=httpx.MockTransport(gateway),
    )
    async with app.router.lifespan_context(app):  # noqa: SIM117 - Keep lifespan separate.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://backend") as client:
            login = await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            project = (await client.get("/api/v1/g", headers=headers)).json()[0]
            session_id = project["id"].replace("project-", "session-")
            uploaded = await client.put("/api/v1/files/content?name=image.png",
                                        content=PNG, headers=headers)
            attachment = {"id": uploaded.json()["id"], "name": "image.png"}
            path = f"/api/v1/c/{session_id}/messages"
            text_response = await client.post(path, json={
                "content": "Describe", "model": "text-model", "attachments": [attachment],
            }, headers=headers)
            vision_response = await client.post(path, json={
                "content": "Describe", "model": "vision-model", "attachments": [attachment],
            }, headers=headers)
            for _ in range(100):
                if runner.images is not None:
                    break
                await asyncio.sleep(0.01)
    assert text_response.status_code == 422
    assert text_response.json()["detail"]["code"] == "MODEL_DOES_NOT_SUPPORT_IMAGES"
    assert vision_response.status_code == 200
    assert runner.images == [{"type": "image", "data": base64.b64encode(PNG).decode(),
                              "mimeType": "image/png"}]
