from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from app.config import Settings
from app.contracts.catalog import FileUploadRequest
from app.domain.catalog import InvalidFileUpload
from app.main import create_app


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime", ["mock", "pi"])
async def test_guest_saved_bytes_appear_in_usage_and_delete_releases_them(tmp_path, runtime):
    app = create_app(
        Settings(agent_runtime=runtime, agent_db_path=str(tmp_path / "agent.sqlite3")),
        pi_runner=object() if runtime == "pi" else None,
    )
    token, _guest = app.state.demo_store.issue_anonymous_token()
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        uploaded = await client.post("/api/v1/files", headers=headers,
                                     json={"name": "note.md", "size": 5,
                                           "content": "hello"})
        before = await client.get("/api/v1/usage", headers=headers)
        deleted = await client.delete(f"/api/v1/files/{uploaded.json()['id']}",
                                      headers=headers)
        after = await client.get("/api/v1/usage", headers=headers)

    assert uploaded.status_code == 200
    assert before.json()["storage"] == {
        "limit": 10 * 1024 * 1024, "used": 5,
        "remaining": 10 * 1024 * 1024 - 5, "unit": "bytes",
    }
    assert deleted.status_code == 204
    assert after.json()["storage"]["used"] == 0


@pytest.mark.asyncio
async def test_guest_json_upload_obeys_total_saved_bytes(tmp_path):
    app = create_app(Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        guest_storage_limit_bytes=10,
    ), pi_runner=object())
    token, _guest = app.state.demo_store.issue_anonymous_token()
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        first = await client.post("/api/v1/files", headers=headers,
                                  json={"name": "first.md", "size": 6,
                                        "content": "123456"})
        rejected = await client.post("/api/v1/files", headers=headers,
                                     json={"name": "second.md", "size": 5,
                                           "content": "12345"})
        files = await client.get("/api/v1/files", headers=headers)
        usage = await client.get("/api/v1/usage", headers=headers)
        await client.delete(f"/api/v1/files/{first.json()['id']}", headers=headers)
        retried = await client.post("/api/v1/files", headers=headers,
                                    json={"name": "second.md", "size": 5,
                                          "content": "12345"})

    assert first.status_code == 200
    assert rejected.status_code == 413
    assert rejected.json()["detail"]["code"] == "STORAGE_QUOTA_EXCEEDED"
    assert len(files.json()) == 1
    assert usage.json()["storage"]["used"] == 6
    assert retried.status_code == 200


@pytest.mark.asyncio
async def test_guest_streamed_upload_obeys_total_saved_bytes(tmp_path):
    app = create_app(Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        guest_storage_limit_bytes=10,
    ), pi_runner=object())
    token, _guest = app.state.demo_store.issue_anonymous_token()
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        first = await client.put("/api/v1/files/content", headers=headers,
                                 params={"name": "first.txt"}, content=b"123456")
        rejected = await client.put("/api/v1/files/content", headers=headers,
                                    params={"name": "second.txt"}, content=b"12345")
        files = await client.get("/api/v1/files", headers=headers)

    assert first.status_code == 200
    assert rejected.status_code == 413
    assert rejected.json()["detail"]["code"] == "STORAGE_QUOTA_EXCEEDED"
    assert len(files.json()) == 1


@pytest.mark.asyncio
async def test_guest_streamed_pdf_is_rejected_before_parser_when_over_two_mib(tmp_path):
    app = create_app(Settings(agent_db_path=str(tmp_path / "agent.sqlite3")))
    token, _guest = app.state.demo_store.issue_anonymous_token()
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        rejected = await client.put(
            "/api/v1/files/content", headers=headers,
            params={"name": "large.pdf"}, content=b"%PDF-" + b"x" * (2 * 1024 * 1024),
        )
        malformed = await client.put(
            "/api/v1/files/content", headers=headers,
            params={"name": "broken.pdf"}, content=b"%PDF-not-a-valid-document",
        )
        usage = await client.get("/api/v1/usage", headers=headers)

    assert rejected.status_code == 413
    assert rejected.json()["detail"]["code"] == "FILE_TOO_LARGE"
    assert malformed.status_code == 422
    assert usage.json()["storage"]["used"] == 0


def test_two_app_instances_cannot_exceed_guest_total_storage(tmp_path):
    settings = Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
                        guest_storage_limit_bytes=10)
    apps = [create_app(settings, pi_runner=object()) for _ in range(2)]
    user_id = "guest-1"
    for app in apps:
        app.state.identity_policy.observe_verified_user(user_id, True)

    def upload(index):
        try:
            apps[index].state.catalog.add_file(
                user_id, FileUploadRequest(name=f"file-{index}.md", size=6,
                                           content="123456"),
            )
            return "accepted"
        except InvalidFileUpload as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(upload, range(2)))

    assert sorted(results) == ["STORAGE_QUOTA_EXCEEDED", "accepted"]
    assert sum(file.size for file in apps[0].state.catalog.files_for(user_id)) == 6


def test_member_retains_existing_pdf_maximum(tmp_path):
    app = create_app(Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")),
                     pi_runner=object())
    user_id = "member-1"
    app.state.identity_policy.observe_verified_user(user_id, False)
    raw = b"%PDF-" + b"x" * (3 * 1024 * 1024)

    saved = app.state.catalog.add_parsed_pdf(user_id, "paper.pdf", raw, "paper content")

    assert saved.size == len(raw)
