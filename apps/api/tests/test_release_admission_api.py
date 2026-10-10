"""New maintenance denials use the same structured API error across dispatch paths."""

from types import SimpleNamespace

import httpx
import pytest

from app.api.admin_auth import get_admin_principal
from app.config import Settings
from app.contracts.admin import AdminMe
from app.domain.release_admission import AdmissionClosed
from app.main import create_app


@pytest.mark.asyncio
async def test_administrative_qualification_preserves_structured_admission_error():
    app = create_app(Settings(mode="mock", agent_runtime="mock"))
    app.dependency_overrides[get_admin_principal] = lambda: AdminMe(
        user_id="synthetic-admin", roles=["platform_admin"],
        permissions=["services:write"], service_ids=[],
    )
    app.state.tool_product_repository = SimpleNamespace(
        get_draft_revision=lambda *_: SimpleNamespace(acceptance_suite_revision=1),
    )

    async def closed(*_):
        raise AdmissionClosed("Release admission is closed")

    app.state.mcp_qualification = SimpleNamespace(qualify=closed)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local.test") as client:
        response = await client.post(
            "/api/v1/admin/tool-products/synthetic-product/qualifications",
            json={"revision": 1, "suite_revision": 1, "reason": "Synthetic release admission check"},
        )
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "RELEASE_ADMISSION_CLOSED"}}
