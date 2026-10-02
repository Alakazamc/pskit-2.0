import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.main import create_app


class UnusedPi:
    async def prompt(self, *args, **kwargs):
        raise AssertionError("Pi is not needed to list Skills")


class RecordingPi:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def prompt(self, session_id, message, on_event, **kwargs):
        self.calls.append(kwargs)
        return {"session_file": f"/tmp/{session_id}.jsonl", "text": "Review complete"}


async def _login(client: httpx.AsyncClient) -> dict[str, str]:
    response = await client.post("/api/v1/auth/demo", json={"email": "alice@example.org"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.asyncio
async def test_admin_registered_skill_appears_in_other_live_instance(tmp_path):
    settings = Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        admin_api_key="admin-secret",
    )
    first = create_app(settings, pi_runner=UnusedPi())
    second = create_app(settings, pi_runner=UnusedPi())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=second), base_url="http://test",
    ) as reader:
        alice = await _login(reader)
        assert "rna-review" not in {
            skill["id"] for skill in (await reader.get("/api/v1/skills", headers=alice)).json()
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=first), base_url="http://test",
        ) as writer:
            registered = await writer.put(
                "/api/v1/admin/skills/rna-review/versions/1",
                headers={"X-Admin-Key": "admin-secret"},
                json={
                    "name": "RNA Review", "description": "Review RNA structures",
                    "tools": ["search_pdb"], "instructions": "Inspect RNA evidence carefully.",
                },
            )
            assert registered.status_code == 200, registered.text
        skills = (await reader.get("/api/v1/skills", headers=alice)).json()
        assert {skill["id"]: skill for skill in skills}["rna-review"] == {
            "id": "rna-review", "name": "RNA Review",
            "description": "Review RNA structures", "version": 1,
        }


@pytest.mark.asyncio
async def test_registered_skill_upgrade_uses_latest_trusted_instructions_and_tools(tmp_path):
    pi = RecordingPi()
    app = create_app(Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        admin_api_key="admin-secret",
    ), pi_runner=pi)
    url = "/api/v1/admin/skills/rna-review/versions"
    admin = {"X-Admin-Key": "admin-secret"}
    first = {
        "name": "RNA Review", "description": "Review RNA",
        "tools": ["search_pdb"], "instructions": "Use PDB for RNA evidence.",
    }
    second = {
        "name": "RNA Review", "description": "Review RNA and proteins",
        "tools": ["fetch_uniprot"], "instructions": "Use UniProt for RNA evidence.",
    }
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            alice = await _login(client)
            assert (await client.put(f"{url}/1", json=first)).status_code == 403
            assert (await client.put(f"{url}/1", headers=admin, json=first)).status_code == 200
            assert (await client.put(f"{url}/1", headers=admin, json=first)).status_code == 200
            conflict = await client.put(f"{url}/1", headers=admin, json=second)
            assert conflict.status_code == 409
            assert conflict.json()["detail"]["code"] == "SKILL_VERSION_CONFLICT"
            upgraded = await client.put(f"{url}/2", headers=admin, json=second)
            assert upgraded.status_code == 200, upgraded.text
            assert {skill["id"]: skill for skill in (
                await client.get("/api/v1/skills", headers=alice)
            ).json()}["rna-review"]["version"] == 2
            session_id = (
                await client.get("/api/v1/g", headers=alice)
            ).json()[0]["id"].replace("project-", "session-")
            sent = await client.post(
                f"/api/v1/c/{session_id}/messages", headers=alice,
                json={"content": "Review this", "skills": [{
                    "id": "rna-review", "name": "forged Skill name",
                }]},
            )
            assert sent.status_code == 200, sent.text
            for _ in range(100):
                if pi.calls:
                    break
                await asyncio.sleep(0.01)
    assert len(pi.calls) == 1
    assert "Use UniProt for RNA evidence." in pi.calls[0]["system_prompt_suffix"]
    assert "Use PDB for RNA evidence." not in pi.calls[0]["system_prompt_suffix"]
    assert "forged Skill name" not in pi.calls[0]["system_prompt_suffix"]
    assert {tool["name"] for tool in json.loads(
        pi.calls[0]["environment"]["PSKIT_MCP_TOOLS_JSON"]
    )} == {"fetch_uniprot"}


@pytest.mark.asyncio
async def test_registered_skill_rejects_empty_metadata_and_unknown_tool(tmp_path):
    app = create_app(Settings(
        agent_runtime="pi", agent_db_path=str(tmp_path / "agent.sqlite3"),
        admin_api_key="admin-secret",
    ), pi_runner=UnusedPi())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        admin = {"X-Admin-Key": "admin-secret"}
        base = {
            "name": "RNA Review", "description": "Review RNA",
            "tools": ["search_pdb"], "instructions": "Inspect evidence.",
        }
        for invalid in (
            {**base, "name": "   "},
            {**base, "description": "\t"},
            {**base, "tools": ["unknown_tool"]},
            {**base, "tools": ["search_pdb", "search_pdb"]},
        ):
            response = await client.put(
                "/api/v1/admin/skills/rna-review/versions/1",
                headers=admin, json=invalid,
            )
            assert response.status_code == 422, response.text
        built_in = await client.put(
            "/api/v1/admin/skills/structure-review/versions/2",
            headers=admin, json=base,
        )
        assert built_in.status_code == 409
        assert built_in.json()["detail"]["code"] == "SKILL_VERSION_CONFLICT"
        alice = await _login(client)
        assert "rna-review" not in {
            skill["id"] for skill in (await client.get("/api/v1/skills", headers=alice)).json()
        }
