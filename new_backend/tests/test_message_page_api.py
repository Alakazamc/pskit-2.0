"""The page route is additive; original lists and route ownership still work."""

import httpx
import pytest

from app.main import create_app


@pytest.mark.asyncio
async def test_additive_page_route_keeps_original_list_and_enforces_scope():
    app = create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        auth = (await client.post("/api/v1/auth/demo", json={"email": "pages@example.org"})).json()
        headers = {"Authorization": f"Bearer {auth['access_token']}"}
        personal = (await client.post("/api/v1/c", headers=headers,
                                      json={"title": "History"})).json()
        base = f"/api/v1/c/{personal['id']}/messages"
        for index in range(4):
            response = await client.post(base, headers=headers, json={"content": str(index)})
            assert response.status_code == 200
        original = (await client.get(base, headers=headers)).json()
        assert isinstance(original, list) and len(original) == 8
        first = (await client.get(base + "/page?limit=3", headers=headers)).json()
        assert first["items"] == original[-3:]
        cursor = first["next_before"]
        second = (await client.get(base + "/page", params={"limit": 3, "before": cursor},
                                   headers=headers)).json()
        assert second["items"] == original[-6:-3]
        assert (await client.get(base, headers=headers)).json() == original
        assert (await client.get(base + "/page?limit=101", headers=headers)).status_code == 422
        assert (await client.get(base + "/page?before=foreign", headers=headers)).status_code == 404
        project = (await client.post("/api/v1/g", headers=headers, json={"name": "Other"})).json()
        key = f"g-p-{project['id'].removeprefix('project-')}"
        wrong = f"/api/v1/g/{key}/c/{personal['id']}/messages/page"
        assert (await client.get(wrong, headers=headers)).status_code == 404
