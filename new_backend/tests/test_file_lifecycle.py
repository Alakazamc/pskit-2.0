import asyncio

import httpx
import pytest
from fpdf import FPDF

from app.config import Settings
from app.main import create_app


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime", ["mock", "pi"])
async def test_file_download_and_delete_enforce_owner(tmp_path, runtime):
    app = create_app(
        Settings(agent_runtime=runtime, agent_db_path=str(tmp_path / "agent.sqlite3")),
        pi_runner=object() if runtime == "pi" else None,
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        alice = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        bob = (await client.post("/api/v1/auth/demo", json={"email": "bob@example.org"})).json()
        a = {"Authorization": f"Bearer {alice['access_token']}"}
        b = {"Authorization": f"Bearer {bob['access_token']}"}
        uploaded = await client.post("/api/v1/files", headers=a, json={
            "name": "notes.md", "size": 5, "content": "hello",
        })
        file_id = uploaded.json()["id"]
        foreign_read = await client.get(f"/api/v1/files/{file_id}/download", headers=b)
        foreign_delete = await client.delete(f"/api/v1/files/{file_id}", headers=b)
        download = await client.get(f"/api/v1/files/{file_id}/download", headers=a)
        deleted = await client.delete(f"/api/v1/files/{file_id}", headers=a)
        missing = await client.get(f"/api/v1/files/{file_id}/download", headers=a)
        listed = (await client.get("/api/v1/files", headers=a)).json()

    assert uploaded.status_code == 200
    assert foreign_read.status_code == foreign_delete.status_code == 404
    assert download.status_code == 200 and download.text == "hello"
    assert "attachment" in download.headers["content-disposition"]
    assert deleted.status_code == 204 and missing.status_code == 404
    assert listed == []


@pytest.mark.asyncio
async def test_streamed_pdf_preserves_original_bytes_and_extracts_context(tmp_path):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(text="Protein RNA binding evidence")
    original = bytes(pdf.output())
    app = create_app(Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")),
                     pi_runner=object())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        identity = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()
        token = identity["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        uploaded = await client.put("/api/v1/files/content", headers=headers,
                                    params={"name": "paper.pdf"}, content=original)
        file_id = uploaded.json()["id"]
        download = await client.get(f"/api/v1/files/{file_id}/download", headers=headers)

    assert uploaded.status_code == 200
    assert uploaded.json()["size"] == len(original)
    assert download.content == original
    assert "Protein RNA binding evidence" in app.state.catalog.file_content_for(
        identity["user"]["id"], file_id,
    )


@pytest.mark.asyncio
async def test_streamed_upload_rejects_invalid_or_large_files_without_persisting(tmp_path):
    app = create_app(Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")),
                     pi_runner=object())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        token = (await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        invalid = await client.put("/api/v1/files/content", headers=headers,
                                   params={"name": "binary.exe"}, content=b"not a supported file")
        too_large = await client.put("/api/v1/files/content", headers=headers,
                                     params={"name": "huge.txt"}, content=b"x" * (1024 * 1024 + 1))
        malformed_pdf = await client.put("/api/v1/files/content", headers=headers,
                                         params={"name": "broken.pdf"}, content=b"%PDF-not-a-valid-document")
        files = (await client.get("/api/v1/files", headers=headers)).json()

    assert invalid.status_code == 415
    assert too_large.status_code == 413
    assert malformed_pdf.status_code == 422
    assert malformed_pdf.json()["detail"]["code"] == "INVALID_PDF"
    assert files == []


@pytest.mark.asyncio
async def test_pdf_processing_deadline_returns_stable_error_without_saving_file(tmp_path):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(text="Protein RNA binding evidence")
    app = create_app(Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        pdf_parse_timeout_seconds=0.001,
    ), pi_runner=object())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        identity = (await client.post("/api/v1/auth/demo", json={
            "email": "alice@example.org",
        })).json()
        headers = {"Authorization": f"Bearer {identity['access_token']}"}
        response = await client.put("/api/v1/files/content", headers=headers,
                                    params={"name": "paper.pdf"}, content=bytes(pdf.output()))
        files = (await client.get("/api/v1/files", headers=headers)).json()

    assert response.status_code == 504
    assert response.json()["detail"]["code"] == "FILE_PROCESSING_TIMEOUT"
    assert files == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "content"), [
    ("structure.pdb", "ATOM      1  N   ALA A   1\n"),
    ("reads.fastq", "@read1\nACGT\n+\n!!!!\n"),
    ("variants.vcf", "##fileformat=VCFv4.2\n"),
])
async def test_common_scientific_text_files_are_available_as_context(tmp_path, name, content):
    app = create_app(Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")),
                     pi_runner=object())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        identity = (await client.post("/api/v1/auth/demo", json={
            "email": "alice@example.org",
        })).json()
        headers = {"Authorization": f"Bearer {identity['access_token']}"}
        uploaded = await client.put("/api/v1/files/content", headers=headers,
                                    params={"name": name}, content=content.encode())

    assert uploaded.status_code == 200
    assert app.state.catalog.file_content_for(identity["user"]["id"], uploaded.json()["id"]) == content


@pytest.mark.asyncio
async def test_pdf_parsing_pool_bounds_concurrent_uploads(tmp_path):
    pdf = FPDF()
    for _ in range(90):
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        pdf.cell(text="Protein RNA binding evidence")
    raw = bytes(pdf.output())
    app = create_app(Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        pdf_max_concurrent_parses=1, pdf_queue_timeout_seconds=0.001,
    ), pi_runner=object())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        identity = (await client.post("/api/v1/auth/demo", json={
            "email": "alice@example.org",
        })).json()
        headers = {"Authorization": f"Bearer {identity['access_token']}"}
        responses = await asyncio.gather(*(
            client.put("/api/v1/files/content", headers=headers,
                       params={"name": f"paper-{index}.pdf"}, content=raw)
            for index in range(2)
        ))
        files = (await client.get("/api/v1/files", headers=headers)).json()

    assert sorted(response.status_code for response in responses) == [200, 429]
    assert next(response for response in responses if response.status_code == 429).json()[
        "detail"
    ]["code"] == "FILE_PROCESSING_BUSY"
    assert len(files) == 1
