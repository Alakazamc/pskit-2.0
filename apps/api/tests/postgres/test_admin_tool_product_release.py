from copy import deepcopy

import httpx
import pytest
from psycopg.types.json import Jsonb
from test_tool_product_repository import draft_payload, report_for

from app.config import Settings
from app.contracts.tool_products import ToolProductDraft
from app.db.postgres_migrations import migrate_postgres
from app.domain.compute.common import payload_hash
from app.main import create_app


class Identity:
    async def verify(self, token):
        from app.contracts.models import UserIdentity

        return UserIdentity(
            id=token, name=token, email=f"{token}@example.org", is_anonymous=False
        )


class QualificationStub:
    def __init__(self, repository):
        self.repository = repository

    async def probe(self, **values):
        manifest = {"protocol": {"version": "2025-11-25"}}
        snapshot = {
            "probe_id": "probe-1",
            "endpoint_id": "endpoint-1",
            "service_id": values["service_id"],
            "service_revision": 1,
            "uri": values["uri"],
            "transport": values["transport"],
            "credential_ref": values.get("credential_ref"),
            "network_zone": values["network_zone"],
            "protocol": {"version": "2025-11-25"},
            "addresses": ["93.184.216.34"],
            "checked_at": "2026-10-06T00:00:00+00:00",
        }
        with self.repository.database.transaction() as connection:
            connection.execute(
                "INSERT INTO mcp_service_endpoints "
                "(endpoint_id,uri,transport,credential_ref,network_zone,state,created_by) "
                "VALUES ('endpoint-1',%s,%s,%s,%s,'approved',%s)",
                (
                    values["uri"], values["transport"], values.get("credential_ref"),
                    values["network_zone"], values["actor_id"],
                ),
            )
            connection.execute(
                "INSERT INTO mcp_service_revisions "
                "(service_id,revision,endpoint_id,manifest_json,manifest_digest,created_by) "
                "VALUES (%s,1,'endpoint-1',%s,%s,%s)",
                (
                    values["service_id"], Jsonb(manifest), payload_hash(manifest),
                    values["actor_id"],
                ),
            )
            connection.execute(
                "INSERT INTO mcp_probe_snapshots "
                "(probe_id,endpoint_id,service_id,service_revision,snapshot_json,checked_at) "
                "VALUES ('probe-1','endpoint-1',%s,1,%s,%s)",
                (values["service_id"], Jsonb(snapshot), snapshot["checked_at"]),
            )
        return snapshot

    def get_probe(self, probe_id):
        with self.repository.database.connection() as connection:
            row = connection.execute(
                "SELECT snapshot_json FROM mcp_probe_snapshots WHERE probe_id=%s",
                (probe_id,),
            ).fetchone()
        return row[0] if row else None

    async def qualify(self, product_id, revision, suite_revision):
        with self.repository.database.connection() as connection:
            raw = connection.execute(
                "SELECT snapshot_json FROM tool_product_revisions "
                "WHERE product_id=%s AND revision=%s",
                (product_id, revision),
            ).fetchone()[0]
        draft = ToolProductDraft.model_validate(raw)
        report = report_for(draft, report_id=f"report-{product_id}-{revision}")
        self.repository.record_qualification(report)
        return report


@pytest.fixture
def admin_product_app(pg_schema):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    app = create_app(Settings(
        mode="live",
        database_url=dsn,
        database_schema=schema,
        supabase_url="https://identity.example",
        supabase_publishable_key="publishable",
        mcp_executor="disabled",
        af3_executor="disabled",
        admin_mcp_network_zones_json="{}",
        auth_csrf_secret="test-csrf-secret-with-at-least-32-chars",
    ))
    app.state.identity_provider = Identity()
    app.state.mcp_qualification = QualificationStub(app.state.tool_product_repository)
    app.state.admin_store.grant(
        "admin", "platform_admin", actor="bootstrap", reason="Test administrator"
    )
    app.state.admin_store.grant(
        "maintainer",
        "service_maintainer",
        service_id="coral",
        actor="bootstrap",
        reason="Owned CORAL service",
    )
    try:
        yield app
    finally:
        app.state.database.close()


def headers(user):
    return {"Authorization": f"Bearer {user}"}


@pytest.mark.asyncio
async def test_only_platform_admin_can_enter_uri_and_probe_revision_is_persisted(admin_product_app):
    app = admin_product_app
    payload = {
        "service_id": "coral",
        "uri": "https://coral.example/mcp",
        "transport": "streamable_http",
        "credential_ref": None,
        "network_zone": "public",
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        denied = await client.post("/api/v1/admin/mcp-probes", headers=headers("maintainer"), json=payload)
        allowed = await client.post("/api/v1/admin/mcp-probes", headers=headers("admin"), json=payload)
        fetched = await client.get(
            f"/api/v1/admin/mcp-probes/{allowed.json()['probe_id']}",
            headers=headers("admin"),
        )

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert fetched.status_code == 200
    assert allowed.json()["service_revision"] == 1
    with app.state.database.connection() as connection:
        assert connection.execute(
            "SELECT state FROM mcp_service_endpoints WHERE endpoint_id='endpoint-1'"
        ).fetchone() == ("approved",)
        assert connection.execute(
            "SELECT revision FROM mcp_service_revisions WHERE service_id='coral'"
        ).fetchone() == (1,)


@pytest.mark.asyncio
async def test_maintainer_can_save_owned_draft_but_cannot_publish(admin_product_app):
    app = admin_product_app
    payload = draft_payload()
    payload["owner_user_id"] = "maintainer"
    body = {
        "expected_revision": 0,
        "reason": "Configure owned CORAL product",
        "draft": payload,
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        saved = await client.put(
            "/api/v1/admin/tool-products/product-coral/draft",
            headers=headers("maintainer"),
            json=body,
        )
        qualified = await client.post(
            "/api/v1/admin/tool-products/product-coral/qualifications",
            headers=headers("maintainer"),
            json={"revision": 1, "suite_revision": 2, "reason": "Run release evidence"},
        )
        denied = await client.post(
            f"/api/v1/admin/tool-product-releases/{qualified.json()['report_id']}/publish",
            headers=headers("maintainer"),
            json={
                "product_id": "product-coral", "revision": 1,
                "reason": "Attempt self publication",
            },
        )

    assert saved.status_code == 200
    assert qualified.status_code == 200
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_platform_admin_publishes_only_current_exact_qualification(admin_product_app):
    app = admin_product_app
    first = draft_payload()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.put(
            "/api/v1/admin/tool-products/product-coral/draft",
            headers=headers("admin"),
            json={"expected_revision": 0, "reason": "Initial product draft", "draft": first},
        )).status_code == 200
        qualified = await client.post(
            "/api/v1/admin/tool-products/product-coral/qualifications",
            headers=headers("admin"),
            json={"revision": 1, "suite_revision": 2, "reason": "Qualify exact release"},
        )
        published = await client.post(
            f"/api/v1/admin/tool-product-releases/{qualified.json()['report_id']}/publish",
            headers=headers("admin"),
            json={"product_id": "product-coral", "revision": 1, "reason": "Approve evidence"},
        )

        changed = deepcopy(first)
        changed["revision"] = 2
        changed["description"] = {"en": "Changed", "zh-CN": "已修改"}
        assert (await client.put(
            "/api/v1/admin/tool-products/product-coral/draft",
            headers=headers("admin"),
            json={"expected_revision": 1, "reason": "Change after qualification", "draft": changed},
        )).status_code == 200
        stale = await client.post(
            f"/api/v1/admin/tool-product-releases/{qualified.json()['report_id']}/publish",
            headers=headers("admin"),
            json={"product_id": "product-coral", "revision": 1, "reason": "Reuse stale evidence"},
        )

    assert published.status_code == 200
    assert published.json()["state"] == "published"
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "QUALIFICATION_STALE"
