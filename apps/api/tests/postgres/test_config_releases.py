"""Reviewed releases update the real immutable compute catalog atomically."""

import asyncio

import httpx
import pytest

from app.api import admin_services, compute
from app.domain.admin.releases import ConfigReleaseService
from app.domain.compute.jobs import ComputeJobs
from app.domain.compute.ledger import ComputeLedger


@pytest.mark.asyncio
async def test_discovered_schema_change_does_not_mutate_published_capability(admin_system):
    app, database = admin_system
    schema = {
        "type": "object",
        "properties": {"sequence": {"type": "string"}},
        "required": ["sequence"],
    }

    def upstream(request):
        return httpx.Response(
            200,
            json={
                "capabilities": [
                    {
                        "id": "rna.predict",
                        "input_schema": schema,
                        "output_schema": {"type": "object"},
                    }
                ]
            },
        )

    app.state.admin_releases = ConfigReleaseService(
        app.state.admin_store,
        endpoints={"lab": {"url": "http://lab", "schema_path": "/schema"}},
        transport=httpx.MockTransport(upstream),
    )
    ledger = ComputeLedger(database, cpu_daily_limit_ms=60000, gpu_daily_limit_ms=60000)
    app.state.compute_jobs = ComputeJobs(database, ledger)
    app.include_router(admin_services.router)
    app.include_router(compute.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = {"Authorization": "Bearer verified"}
        draft = {
            "expected_revision": 0,
            "reason": "Reviewed science service draft",
            "name": "RNA",
            "owner_user_id": "operator",
            "transport": "http",
            "endpoint_ref": "lab",
            "model_version": "weights-v1",
            "capabilities": [{"id": "rna.predict", "version": "1", "input_schema": schema}],
        }
        saved = await client.put("/api/v1/admin/services/rna/draft", headers=headers, json=draft)
        assert saved.status_code == 200
        checked = await client.post(
            "/api/v1/admin/services/rna/checks",
            headers=headers,
            json={
                "expected_revision": 1,
                "reason": "Compare actual service schemas",
                "kind": "schema",
            },
        )
        assert checked.json()["status"] == "passed"
        release = await client.post(
            "/api/v1/admin/config-releases",
            headers=headers,
            json={
                "expected_revision": 0,
                "reason": "Review immutable release",
                "services": [{"service_id": "rna", "revision": 1}],
            },
        )
        assert release.status_code == 200
        published = await client.post(
            f"/api/v1/admin/config-releases/{release.json()['release_id']}/publish",
            headers=headers,
            json={"expected_revision": 1, "reason": "Approve production capability policy"},
        )
        assert published.status_code == 200
        before = (await client.get("/api/v1/compute/capabilities", headers=headers)).json()
        assert before[0]["version"] == "1"
        schema = {"type": "object", "properties": {"new_sequence": {"type": "string"}}}
        discovered = await client.post(
            "/api/v1/admin/services/rna/discovery",
            headers=headers,
            json={"expected_revision": 1, "reason": "Observe new schema candidate"},
        )
        assert discovered.json()["status"] == "passed"
        assert discovered.json()["discovered_capabilities"][0]["input_schema"] == schema
        assert (await client.get("/api/v1/compute/capabilities", headers=headers)).json() == before
        events = (await client.get("/api/v1/admin/audit-events", headers=headers)).json()["items"]
        assert any(e["action"] == "services:publish" for e in events)


@pytest.mark.asyncio
async def test_release_audit_failure_rolls_back_compute_catalog(admin_system):
    app, database = admin_system

    def upstream(request):
        return httpx.Response(
            200,
            json={
                "capabilities": [
                    {
                        "id": "rna.predict",
                        "input_schema": {"type": "object"},
                        "output_schema": {"type": "object"},
                    }
                ]
            },
        )

    app.state.admin_releases = ConfigReleaseService(
        app.state.admin_store,
        endpoints={"lab": {"url": "http://lab"}},
        transport=httpx.MockTransport(upstream),
    )
    app.state.compute_jobs = ComputeJobs(database)
    app.include_router(admin_services.router)
    app.include_router(compute.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h = {"Authorization": "Bearer verified"}
        await client.put(
            "/api/v1/admin/services/rna/draft",
            headers=h,
            json={
                "expected_revision": 0,
                "reason": "Create service candidate",
                "name": "RNA",
                "owner_user_id": "operator",
                "transport": "http",
                "endpoint_ref": "lab",
                "model_version": "v1",
                "capabilities": [
                    {"id": "rna.predict", "version": "1", "input_schema": {"type": "object"}}
                ],
            },
        )
        await client.post(
            "/api/v1/admin/services/rna/checks",
            headers=h,
            json={"expected_revision": 1, "reason": "Check real schemas", "kind": "schema"},
        )
        release = (
            await client.post(
                "/api/v1/admin/config-releases",
                headers=h,
                json={
                    "expected_revision": 0,
                    "reason": "Build tested release",
                    "services": [{"service_id": "rna", "revision": 1}],
                },
            )
        ).json()
        with database.connection() as conn:
            conn.execute(
                "CREATE FUNCTION reject_publish_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.action='services:publish' THEN RAISE EXCEPTION 'publish audit unavailable'; END IF; RETURN NEW; END $$"
            )
            conn.execute(
                "CREATE TRIGGER reject_publish_audit BEFORE INSERT ON admin_audit_events FOR EACH ROW EXECUTE FUNCTION reject_publish_audit()"
            )
        with pytest.raises(Exception, match="publish audit unavailable"):
            await client.post(
                f"/api/v1/admin/config-releases/{release['release_id']}/publish",
                headers=h,
                json={"expected_revision": 1, "reason": "Publish reviewed release"},
            )
        assert (await client.get("/api/v1/compute/capabilities", headers=h)).json() == []
        service = (await client.get("/api/v1/admin/services", headers=h)).json()["items"][0]
        assert service["state"] == "validated"


@pytest.mark.asyncio
async def test_new_release_rejects_superseded_version_for_new_submissions(admin_system):
    app, database = admin_system

    def upstream(request):
        return httpx.Response(
            200,
            json={
                "capabilities": [
                    {
                        "id": "rna.predict",
                        "input_schema": {"type": "object"},
                        "output_schema": {"type": "object"},
                    }
                ]
            },
        )

    app.state.admin_releases = ConfigReleaseService(
        app.state.admin_store,
        endpoints={"lab": {"url": "http://lab"}},
        transport=httpx.MockTransport(upstream),
    )
    app.state.compute_jobs = ComputeJobs(database, ComputeLedger(database))
    app.include_router(admin_services.router)
    app.include_router(compute.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        h = {"Authorization": "Bearer verified"}
        for revision, version in [(0, "1"), (1, "2")]:
            response = await client.put(
                "/api/v1/admin/services/rna/draft",
                headers=h,
                json={
                    "expected_revision": revision,
                    "reason": "Create reviewed version",
                    "name": "RNA",
                    "owner_user_id": "operator",
                    "transport": "http",
                    "endpoint_ref": "lab",
                    "model_version": f"v{version}",
                    "capabilities": [
                        {
                            "id": "rna.predict",
                            "version": version,
                            "input_schema": {"type": "object"},
                        }
                    ],
                },
            )
            assert response.status_code == 200
            await client.post(
                "/api/v1/admin/services/rna/checks",
                headers=h,
                json={
                    "expected_revision": revision + 1,
                    "reason": "Validate observed schemas",
                    "kind": "schema",
                },
            )
            release = (
                await client.post(
                    "/api/v1/admin/config-releases",
                    headers=h,
                    json={
                        "expected_revision": 0,
                        "reason": "Build reviewed release",
                        "services": [{"service_id": "rna", "revision": revision + 1}],
                    },
                )
            ).json()
            assert (
                await client.post(
                    f"/api/v1/admin/config-releases/{release['release_id']}/publish",
                    headers=h,
                    json={"expected_revision": 1, "reason": "Publish reviewed version"},
                )
            ).status_code == 200
            if version == "1":
                queued = (
                    await client.post(
                        "/api/v1/compute/jobs",
                        headers={**h, "Idempotency-Key": "queued-before-release"},
                        json={"capability_id": "rna.predict", "version": "1", "arguments": {}},
                    )
                ).json()
        aliases = (await client.get("/api/v1/compute/capabilities", headers=h)).json()
        assert [c["version"] for c in aliases] == ["2"]
        old = await client.post(
            "/api/v1/compute/jobs",
            headers={**h, "Idempotency-Key": "retired-policy"},
            json={"capability_id": "rna.predict", "version": "1", "arguments": {}},
        )
        assert old.status_code == 404

        from app.contracts.compute import ComputeClaimRequest
        from app.domain.compute.leases import ComputeLeases

        assert (await client.get(f"/api/v1/compute/jobs/{queued['id']}", headers=h)).json()[
            "capability"
        ]["version"] == "1"
        leases = ComputeLeases(database, app.state.compute_jobs.ledger)
        assert leases.claim(ComputeClaimRequest(service_id="rna", worker_id="late-worker")) is None
        assert (await client.get(f"/api/v1/compute/jobs/{queued['id']}", headers=h)).json()[
            "status"
        ] == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["schema_drift", "http_error"])
async def test_failed_schema_recheck_invalidates_release_eligibility(admin_system, failure):
    app, database = admin_system
    failed = False

    def upstream(request):
        if failed and failure == "http_error":
            return httpx.Response(503)
        schema = {"type": "object", "required": ["new_sequence"]} if failed else {"type": "object"}
        return httpx.Response(
            200,
            json={
                "capabilities": [
                    {
                        "id": "rna.predict",
                        "input_schema": schema,
                        "output_schema": {"type": "object"},
                    }
                ]
            },
        )

    app.state.admin_releases = ConfigReleaseService(
        app.state.admin_store,
        endpoints={"lab": {"url": "http://lab"}},
        transport=httpx.MockTransport(upstream),
    )
    app.state.compute_jobs = ComputeJobs(database)
    app.include_router(admin_services.router)
    app.include_router(compute.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = {"Authorization": "Bearer verified"}
        pending = None
        for prior_revision, version in [(0, "1"), (1, "2")]:
            saved = await client.put(
                "/api/v1/admin/services/rna/draft",
                headers=headers,
                json={
                    "expected_revision": prior_revision,
                    "reason": "Review immutable service version",
                    "name": "RNA",
                    "owner_user_id": "operator",
                    "transport": "http",
                    "endpoint_ref": "lab",
                    "model_version": f"v{version}",
                    "capabilities": [
                        {
                            "id": "rna.predict",
                            "version": version,
                            "input_schema": {"type": "object"},
                            "output_schema": {"type": "object"},
                        }
                    ],
                },
            )
            assert saved.status_code == 200
            revision = prior_revision + 1
            check_payload = {
                "expected_revision": revision,
                "reason": "Validate actual source schemas",
                "kind": "schema",
            }
            checked = await client.post(
                "/api/v1/admin/services/rna/checks", headers=headers, json=check_payload
            )
            assert checked.json()["status"] == "passed"
            release_payload = {
                "expected_revision": 0,
                "reason": "Create reviewed release",
                "services": [{"service_id": "rna", "revision": revision}],
            }
            release = await client.post(
                "/api/v1/admin/config-releases", headers=headers, json=release_payload
            )
            assert release.status_code == 200
            if version == "1":
                assert (
                    await client.post(
                        f"/api/v1/admin/config-releases/{release.json()['release_id']}/publish",
                        headers=headers,
                        json={"expected_revision": 1, "reason": "Publish prior approved version"},
                    )
                ).status_code == 200
            else:
                pending = release.json()
        before = (await client.get("/api/v1/compute/capabilities", headers=headers)).json()
        validated = (await client.get("/api/v1/admin/services", headers=headers)).json()["items"][0]
        assert validated["state"] == "validated"
        assert validated["schema_digest"]
        failed = True
        discovery = await client.post(
            "/api/v1/admin/services/rna/discovery",
            headers=headers,
            json={"expected_revision": 2, "reason": "Observe without changing eligibility"},
        )
        assert discovery.status_code == 200
        assert (await client.get("/api/v1/admin/services", headers=headers)).json()["items"][
            0
        ] == validated
        checked = await client.post(
            "/api/v1/admin/services/rna/checks", headers=headers, json=check_payload
        )
        assert checked.status_code == 200
        assert checked.json()["status"] == "failed"
        invalid = (await client.get("/api/v1/admin/services", headers=headers)).json()["items"][0]
        assert invalid["revision"] == 2
        assert invalid["state"] == "draft"
        assert invalid["schema_digest"] is None
        assert invalid["published_revision"] == 1
        rejected_create = await client.post(
            "/api/v1/admin/config-releases", headers=headers, json=release_payload
        )
        assert rejected_create.status_code == 422
        assert rejected_create.json()["detail"]["code"] == "SERVICE_VALIDATION_REQUIRED"
        rejected_publish = await client.post(
            f"/api/v1/admin/config-releases/{pending['release_id']}/publish",
            headers=headers,
            json={"expected_revision": 1, "reason": "Attempt stale validated release"},
        )
        assert rejected_publish.status_code == 422
        assert rejected_publish.json()["detail"]["code"] == "SERVICE_VALIDATION_REQUIRED"
        assert (await client.get("/api/v1/compute/capabilities", headers=headers)).json() == before


@pytest.mark.asyncio
async def test_inflight_schema_check_cannot_override_newer_validated_revision(admin_system):
    app, _ = admin_system
    entered = asyncio.Event()
    resume = asyncio.Event()
    calls = 0
    newer_schema = {
        "type": "object",
        "properties": {"sequence": {"type": "string"}},
        "required": ["sequence"],
    }

    async def upstream(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await resume.wait()
            schema = {"type": "object", "required": ["old_wrong_field"]}
        else:
            schema = newer_schema
        return httpx.Response(
            200,
            json={
                "capabilities": [
                    {
                        "id": "rna.predict",
                        "input_schema": schema,
                        "output_schema": {"type": "object"},
                    }
                ]
            },
        )

    app.state.admin_releases = ConfigReleaseService(
        app.state.admin_store,
        endpoints={"lab": {"url": "http://lab"}},
        transport=httpx.MockTransport(upstream),
    )
    app.include_router(admin_services.router)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = {"Authorization": "Bearer verified"}
        body = {
            "expected_revision": 0,
            "reason": "Create initial service draft",
            "name": "RNA",
            "owner_user_id": "operator",
            "transport": "http",
            "endpoint_ref": "lab",
            "model_version": "v1",
            "capabilities": [
                {"id": "rna.predict", "version": "1", "input_schema": {"type": "object"}}
            ],
        }
        assert (
            await client.put("/api/v1/admin/services/rna/draft", headers=headers, json=body)
        ).status_code == 200
        old_check = asyncio.create_task(
            client.post(
                "/api/v1/admin/services/rna/checks",
                headers=headers,
                json={
                    "expected_revision": 1,
                    "reason": "Check initial source schema",
                    "kind": "schema",
                },
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            body["expected_revision"] = 1
            body["capabilities"][0]["input_schema"] = newer_schema
            body["capabilities"][0]["version"] = "2"
            assert (
                await client.put("/api/v1/admin/services/rna/draft", headers=headers, json=body)
            ).status_code == 200
            latest = await client.post(
                "/api/v1/admin/services/rna/checks",
                headers=headers,
                json={
                    "expected_revision": 2,
                    "reason": "Check newer source schema",
                    "kind": "schema",
                },
            )
            assert latest.json()["status"] == "passed"
            before = (await client.get("/api/v1/admin/services", headers=headers)).json()
            resume.set()
            stale = await old_check
            assert stale.status_code == 409
            assert stale.json()["detail"]["code"] == "REVISION_CONFLICT"
            assert (await client.get("/api/v1/admin/services", headers=headers)).json() == before
        finally:
            resume.set()
            if not old_check.done():
                await old_check
