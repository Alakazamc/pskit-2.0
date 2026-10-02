import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.main import create_app


class RecordingPi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def prompt(self, session_id, message, on_event, **kwargs):
        self.calls.append((message, kwargs))
        return {"session_file": f"/tmp/{session_id}.jsonl", "text": "已检查结构"}


async def _login(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    response = await client.post("/api/v1/auth/demo", json={"email": email})
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.asyncio
async def test_selected_skill_resource_and_file_reach_pi_without_changing_visible_user_message(tmp_path):
    pi = RecordingPi()
    app = create_app(Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")), pi_runner=pi)
    async with app.router.lifespan_context(app):  # noqa: SIM117 - Start scheduler first.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            alice = await _login(client, "alice@example.org")
            session_id = (await client.get("/api/v1/g", headers=alice)).json()[0]["id"].replace("project-", "session-")
            uploaded = await client.put(
                "/api/v1/files/content", headers=alice,
                params={"name": "notes.txt"}, content=b"RNA target",
            )
            assert uploaded.status_code == 200
            sent = await client.post(
                f"/api/v1/c/{session_id}/messages", headers=alice,
                json={
                    "content": "Review the target",
                    "skills": [{"id": "structure-review", "name": "forged display name"}],
                    "resources": [{"id": "search_pdb", "name": "forged resource name"}],
                    "attachments": [{"id": uploaded.json()["id"], "name": "forged.txt"}],
                },
            )
            assert sent.status_code == 200, sent.text
            for _ in range(100):
                if pi.calls:
                    break
                await asyncio.sleep(0.01)
            assert pi.calls
            messages = (await client.get(f"/api/v1/c/{session_id}/messages", headers=alice)).json()

    prompt, kwargs = pi.calls[0]
    assert "Review the target" in prompt
    assert "RNA target" in prompt
    assert "notes.txt" in prompt
    assert "forged.txt" not in prompt
    assert "Use the available PDB and UniProt capabilities" in kwargs["system_prompt_suffix"]
    assert "forged display name" not in kwargs["system_prompt_suffix"]
    assert "Resource search_pdb:" not in kwargs["system_prompt_suffix"]
    assert "forged resource name" not in kwargs["system_prompt_suffix"]
    assert {item["name"] for item in json.loads(kwargs["environment"]["PSKIT_MCP_TOOLS_JSON"])} == {"search_pdb", "fetch_uniprot"}
    assert messages[0]["parts"] == [
        {"type": "text", "text": "Review the target"},
        {"type": "file", "id": uploaded.json()["id"], "name": "notes.txt"},
    ]


@pytest.mark.asyncio
async def test_uploaded_file_survives_restart_and_foreign_user_cannot_attach_it(tmp_path):
    settings = Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"))
    first = create_app(settings, pi_runner=RecordingPi())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=first), base_url="http://test") as client:
        alice = await _login(client, "alice@example.org")
        uploaded = await client.post(
            "/api/v1/files", headers=alice,
            json={"name": "dataset.csv", "size": 7, "content": "x,y\n1,2"},
        )
        assert uploaded.status_code == 200
        file_id = uploaded.json()["id"]

    pi = RecordingPi()
    restarted = create_app(settings, pi_runner=pi)
    async with restarted.router.lifespan_context(restarted):  # noqa: SIM117 - Start scheduler first.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=restarted), base_url="http://test") as client:
            alice = await _login(client, "alice@example.org")
            bob = await _login(client, "bob@example.org")
            files = (await client.get("/api/v1/files", headers=alice)).json()
            assert [file["id"] for file in files] == [file_id]
            alice_session = (await client.get("/api/v1/g", headers=alice)).json()[0]["id"].replace("project-", "session-")
            bob_session = (await client.get("/api/v1/g", headers=bob)).json()[0]["id"].replace("project-", "session-")
            foreign_usage_before = (await client.get("/api/v1/usage", headers=bob)).json()
            foreign = await client.post(
                f"/api/v1/c/{bob_session}/messages", headers=bob,
                json={"content": "Read it", "attachments": [{"id": file_id, "name": "dataset.csv"}]},
            )
            foreign_usage_after = (await client.get("/api/v1/usage", headers=bob)).json()
            sent = await client.post(
                f"/api/v1/c/{alice_session}/messages", headers=alice,
                json={"content": "Read it", "attachments": [{"id": file_id, "name": "dataset.csv"}]},
            )
            assert sent.status_code == 200
            for _ in range(100):
                if pi.calls:
                    break
                await asyncio.sleep(0.01)

    assert foreign.status_code == 404
    assert foreign.json()["detail"]["code"] == "CONTEXT_NOT_FOUND"
    assert foreign_usage_after == foreign_usage_before
    assert "x,y\\n1,2" in pi.calls[0][0]


@pytest.mark.asyncio
async def test_pi_mcp_call_is_scoped_to_run_allowed_tools_and_internal_token(tmp_path):
    class PausedPi(RecordingPi):
        def __init__(self) -> None:
            super().__init__()
            self.release = asyncio.Event()

        async def prompt(self, session_id, message, on_event, **kwargs):
            self.calls.append((message, kwargs))
            await self.release.wait()
            return {"session_file": f"/tmp/{session_id}.jsonl", "text": "已检查结构"}

    pi = PausedPi()
    app = create_app(Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")), pi_runner=pi)
    async with app.router.lifespan_context(app):  # noqa: SIM117 - Start scheduler first.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            alice = await _login(client, "alice@example.org")
            session_id = (await client.get("/api/v1/g", headers=alice)).json()[0]["id"].replace("project-", "session-")
            sent = await client.post(
                f"/api/v1/c/{session_id}/messages", headers=alice,
                json={"content": "Search structures", "resources": [{"id": "search_pdb", "name": "PDB"}]},
            )
            assert sent.status_code == 200
            for _ in range(100):
                if pi.calls:
                    break
                await asyncio.sleep(0.01)
            assert pi.calls
            environment = pi.calls[0][1]["environment"]
            run_id = sent.json()["run_id"]
            authorization = {"Authorization": f"Bearer {environment['PSKIT_AGENT_TOOL_TOKEN']}"}
            permitted = await client.post(
                "/internal/mcp/tools/search_pdb/invoke", headers=authorization,
                json={"run_id": run_id, "tool_call_id": "call-search", "arguments": {"query": "RNA"}},
            )
            invalid_arguments = await client.post(
                "/internal/mcp/tools/search_pdb/invoke", headers=authorization,
                json={"run_id": run_id, "tool_call_id": "call-invalid", "arguments": {"query": 123}},
            )
            denied = await client.post(
                "/internal/mcp/tools/fetch_uniprot/invoke", headers=authorization,
                json={"run_id": run_id, "tool_call_id": "call-denied", "arguments": {"accession": "P12345"}},
            )
            invalid_token = await client.post(
                "/internal/mcp/tools/search_pdb/invoke", headers={"Authorization": "Bearer invalid"},
                json={"run_id": run_id, "tool_call_id": "call-bad-token", "arguments": {"query": "RNA"}},
            )
            pi.release.set()

    assert permitted.status_code == 200
    assert permitted.json()["result"]["hits"][0]["pdb_id"] == "1A9N"
    assert invalid_arguments.status_code == 422
    assert invalid_arguments.json()["detail"]["code"] == "INVALID_TOOL_ARGUMENTS"
    assert denied.status_code == 403
    assert invalid_token.status_code == 401


@pytest.mark.asyncio
async def test_completed_run_cannot_reuse_its_internal_mcp_token(tmp_path):
    pi = RecordingPi()
    app = create_app(Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")), pi_runner=pi)
    async with app.router.lifespan_context(app):  # noqa: SIM117 - Start scheduler first.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            alice = await _login(client, "alice@example.org")
            session_id = (await client.get("/api/v1/g", headers=alice)).json()[0]["id"].replace("project-", "session-")
            sent = await client.post(
                f"/api/v1/c/{session_id}/messages", headers=alice,
                json={"content": "Search", "resources": [{"id": "search_pdb", "name": "PDB"}]},
            )
            run_id = sent.json()["run_id"]
            for _ in range(100):
                status = (await client.get(f"/api/v1/runs/{run_id}", headers=alice)).json()["status"]
                if status == "completed":
                    break
                await asyncio.sleep(0.01)
            assert status == "completed"
            token = pi.calls[0][1]["environment"]["PSKIT_AGENT_TOOL_TOKEN"]
            repeated = await client.post(
                "/internal/mcp/tools/search_pdb/invoke",
                headers={"Authorization": f"Bearer {token}"},
                json={"run_id": run_id, "tool_call_id": "late-call", "arguments": {"query": "RNA"}},
            )
            repeated_af3 = await client.post(
                "/internal/af3/jobs", headers={"Authorization": f"Bearer {token}"},
                json={"run_id": run_id, "tool_call_id": "late-call", "estimated_gpu_minutes": 20},
            )
    assert repeated.status_code == 409
    assert repeated.json()["detail"]["code"] == "RUN_NOT_ACTIVE"
    assert repeated_af3.status_code == 409


@pytest.mark.asyncio
async def test_unknown_skill_is_rejected_before_token_charge_or_message_creation(tmp_path):
    app = create_app(Settings(agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3")), pi_runner=RecordingPi())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        alice = await _login(client, "alice@example.org")
        session_id = (await client.get("/api/v1/g", headers=alice)).json()[0]["id"].replace("project-", "session-")
        before = (await client.get("/api/v1/usage", headers=alice)).json()
        sent = await client.post(
            f"/api/v1/c/{session_id}/messages", headers=alice,
            json={"content": "Search", "skills": [{"id": "unknown", "name": "Fake skill"}]},
        )
        after = (await client.get("/api/v1/usage", headers=alice)).json()
        messages = (await client.get(f"/api/v1/c/{session_id}/messages", headers=alice)).json()
    assert sent.status_code == 404
    assert sent.json()["detail"]["code"] == "CONTEXT_NOT_FOUND"
    assert after == before
    assert messages == []
