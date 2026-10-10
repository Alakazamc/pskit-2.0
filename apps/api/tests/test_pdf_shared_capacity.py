import asyncio
import sqlite3
import threading
import time

import httpx
import pytest

from app.config import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_two_python_instances_share_pdf_parsing_capacity(tmp_path, monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def controlled_parser(raw: bytes, timeout_seconds: float) -> str:
        if raw == b"%PDF-first":
            started.set()
            assert release.wait(timeout=5)
        return "Extracted scientific text"

    monkeypatch.setattr("app.services.pdf_processing._run_pdf_worker", controlled_parser)
    settings = Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "shared.sqlite3"),
        pdf_max_concurrent_parses=1, pdf_queue_timeout_seconds=0.03,
    )
    first_app = create_app(settings, pi_runner=object())
    second_app = create_app(settings, pi_runner=object())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=first_app), base_url="http://first",
    ) as first_client, httpx.AsyncClient(
        transport=httpx.ASGITransport(app=second_app), base_url="http://second",
    ) as second_client:
        first_login = (await first_client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        second_login = (await second_client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        first_headers = {"Authorization": f"Bearer {first_login['access_token']}"}
        second_headers = {"Authorization": f"Bearer {second_login['access_token']}"}
        first = asyncio.create_task(first_client.put(
            "/api/v1/files/content", headers=first_headers,
            params={"name": "first.pdf"}, content=b"%PDF-first",
        ))
        try:
            assert await asyncio.to_thread(started.wait, 1)
            busy = await second_client.put(
                "/api/v1/files/content", headers=second_headers,
                params={"name": "second.pdf"}, content=b"%PDF-second",
            )
        finally:
            release.set()
        completed = await asyncio.wait_for(first, timeout=2)
        later = await second_client.put(
            "/api/v1/files/content", headers=second_headers,
            params={"name": "second.pdf"}, content=b"%PDF-second",
        )

    assert busy.status_code == 429
    assert busy.json()["detail"]["code"] == "FILE_PROCESSING_BUSY"
    assert completed.status_code == 200
    assert later.status_code == 200


@pytest.mark.asyncio
async def test_cancelled_pdf_upload_holds_shared_slot_until_parser_exits(tmp_path, monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def controlled_parser(raw: bytes, timeout_seconds: float) -> str:
        if raw == b"%PDF-first":
            started.set()
            assert release.wait(timeout=5)
        return "Extracted scientific text"

    monkeypatch.setattr("app.services.pdf_processing._run_pdf_worker", controlled_parser)
    settings = Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "shared.sqlite3"),
        pdf_max_concurrent_parses=1, pdf_queue_timeout_seconds=0.03,
    )
    first_app = create_app(settings, pi_runner=object())
    second_app = create_app(settings, pi_runner=object())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=first_app), base_url="http://first",
    ) as first_client, httpx.AsyncClient(
        transport=httpx.ASGITransport(app=second_app), base_url="http://second",
    ) as second_client:
        first_login = (await first_client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        second_login = (await second_client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        first_headers = {"Authorization": f"Bearer {first_login['access_token']}"}
        second_headers = {"Authorization": f"Bearer {second_login['access_token']}"}
        first = asyncio.create_task(first_client.put(
            "/api/v1/files/content", headers=first_headers,
            params={"name": "first.pdf"}, content=b"%PDF-first",
        ))
        assert await asyncio.to_thread(started.wait, 1)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        try:
            busy = await second_client.put(
                "/api/v1/files/content", headers=second_headers,
                params={"name": "second.pdf"}, content=b"%PDF-second",
            )
        finally:
            release.set()
        await asyncio.sleep(0.05)
        later = await second_client.put(
            "/api/v1/files/content", headers=second_headers,
            params={"name": "second.pdf"}, content=b"%PDF-second",
        )

    assert busy.status_code == 429
    assert later.status_code == 200


@pytest.mark.asyncio
async def test_expired_pdf_parsing_lease_is_reclaimed_after_worker_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.pdf_processing._run_pdf_worker",
        lambda raw, timeout_seconds: "Recovered parser result",
    )
    path = str(tmp_path / "shared.sqlite3")
    app = create_app(Settings(agent_runtime="pi", agent_db_path=path), pi_runner=object())
    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO pdf_execution_leases VALUES (?,?)",
            ("crashed-worker", time.time() - 1),
        )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        login = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        response = await client.put(
            "/api/v1/files/content",
            headers={"Authorization": f"Bearer {login['access_token']}"},
            params={"name": "recovered.pdf"}, content=b"%PDF-recovered",
        )
    assert response.status_code == 200


def test_pdf_capacity_mismatch_is_rejected_at_startup(tmp_path):
    path = str(tmp_path / "shared.sqlite3")
    create_app(Settings(agent_runtime="pi", agent_db_path=path, pdf_max_concurrent_parses=1),
               pi_runner=object())
    with pytest.raises(ValueError, match="PDF capacity must match"):
        create_app(Settings(agent_runtime="pi", agent_db_path=path, pdf_max_concurrent_parses=2),
                   pi_runner=object())
