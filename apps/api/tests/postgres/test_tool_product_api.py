from copy import deepcopy

import httpx
import pytest
from fastapi import FastAPI, Request
from test_tool_product_repository import draft_payload, report_for

from app.api import tool_products
from app.api.auth import get_current_user
from app.contracts.models import UserIdentity
from app.contracts.tool_products import ToolProductDraft
from app.db.postgres import PostgresDatabase
from app.db.postgres_migrations import migrate_postgres
from app.domain.tool_products.registry import ToolProductRegistry
from app.domain.tool_products.repository import ToolProductRepository


def product_payload(product_id, slug, *, revision=1, audience="public", allowed=None):
    payload = deepcopy(
        draft_payload(revision=revision, audience=audience, allowed=allowed)
    )
    payload["product_id"] = product_id
    payload["slug"] = slug
    payload["ui_schema"]["product"]["slug"] = slug
    payload["acceptance_suite_id"] = f"suite-{slug}"
    return payload


def publish(repository, payload, *, expected=0, suffix="1"):
    draft = repository.save_draft(
        "owner", payload["product_id"], ToolProductDraft.model_validate(payload), expected
    )
    report = report_for(draft, report_id=f"report-{payload['slug']}-{suffix}")
    repository.record_qualification(report)
    return repository.publish(draft.product_id, draft.revision, report.report_id, "publisher")


@pytest.fixture
def product_api(pg_schema):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    repository = ToolProductRepository(database)
    app = FastAPI()
    app.state.tool_product_registry = ToolProductRegistry(repository)
    app.include_router(tool_products.router)

    async def current_user(request: Request):
        user_id = request.headers.get("X-Test-User", "guest")
        return UserIdentity(
            id=user_id,
            email=f"{user_id}@example.org",
            name=user_id,
            is_anonymous=request.headers.get("X-Test-Anonymous") == "1",
        )

    app.dependency_overrides[get_current_user] = current_user
    try:
        yield app, repository
    finally:
        database.close()


@pytest.mark.asyncio
async def test_list_returns_only_published_products_visible_to_guest(product_api):
    app, repository = product_api
    release = publish(repository, product_payload("product-coral", "coral"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/v1/tool-products",
            headers={"X-Test-User": "guest-1", "X-Test-Anonymous": "1"},
        )

    assert response.status_code == 200
    assert [item["release_id"] for item in response.json()["items"]] == [release.release_id]
    serialized = response.text
    assert "generate_rna_for_protein" not in serialized
    assert "credential_ref" not in serialized
    assert "endpoint" not in serialized
    assert "service_id" not in serialized


@pytest.mark.asyncio
async def test_list_is_cursor_paginated_and_bounds_limit(product_api):
    app, repository = product_api
    publish(repository, product_payload("product-alpha", "alpha"))
    publish(repository, product_payload("product-coral", "coral"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        first = await client.get("/api/v1/tool-products?limit=1", headers={"X-Test-User": "alice"})
        second = await client.get(
            f"/api/v1/tool-products?limit=1&cursor={first.json()['next_cursor']}",
            headers={"X-Test-User": "alice"},
        )
        too_small = await client.get("/api/v1/tool-products?limit=0", headers={"X-Test-User": "alice"})
        too_large = await client.get("/api/v1/tool-products?limit=101", headers={"X-Test-User": "alice"})

    assert [item["slug"] for item in first.json()["items"]] == ["alpha"]
    assert first.json()["next_cursor"] == "alpha"
    assert [item["slug"] for item in second.json()["items"]] == ["coral"]
    assert second.json()["next_cursor"] is None
    assert too_small.status_code == too_large.status_code == 422


@pytest.mark.asyncio
async def test_unknown_suspended_and_unauthorized_are_all_404(product_api):
    app, repository = product_api
    release = publish(
        repository,
        product_payload(
            "product-private", "private-coral", audience="restricted", allowed=["bob"]
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        unknown = await client.get(
            "/api/v1/tool-products/missing", headers={"X-Test-User": "alice"}
        )
        denied = await client.get(
            "/api/v1/tool-products/private-coral", headers={"X-Test-User": "alice"}
        )
        allowed = await client.get(
            "/api/v1/tool-products/private-coral", headers={"X-Test-User": "bob"}
        )
        repository.suspend(release.release_id, "publisher")
        suspended = await client.get(
            "/api/v1/tool-products/private-coral", headers={"X-Test-User": "bob"}
        )

    assert allowed.status_code == 200
    assert unknown.status_code == denied.status_code == suspended.status_code == 404
    assert unknown.json() == denied.json() == suspended.json()


@pytest.mark.asyncio
async def test_members_are_visible_to_users_but_not_anonymous_guests(product_api):
    app, repository = product_api
    publish(repository, product_payload("product-member", "member-coral", audience="members"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        member = await client.get(
            "/api/v1/tool-products/member-coral", headers={"X-Test-User": "alice"}
        )
        guest = await client.get(
            "/api/v1/tool-products/member-coral",
            headers={"X-Test-User": "guest-1", "X-Test-Anonymous": "1"},
        )

    assert member.status_code == 200
    assert guest.status_code == 404


@pytest.mark.asyncio
async def test_permission_is_rechecked_after_an_earlier_successful_read(product_api):
    app, repository = product_api
    publish(repository, product_payload("product-coral", "coral"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        before = await client.get(
            "/api/v1/tool-products/coral", headers={"X-Test-User": "alice"}
        )
        publish(
            repository,
            product_payload(
                "product-coral", "coral", revision=2, audience="restricted", allowed=["bob"]
            ),
            expected=1,
            suffix="2",
        )
        after = await client.get(
            "/api/v1/tool-products/coral", headers={"X-Test-User": "alice"}
        )

    assert before.status_code == 200
    assert after.status_code == 404
