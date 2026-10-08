"""Audited quota, execution, sandbox and reconciliation operations."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from app.api.admin_auth import require_permission
from app.api.admin_services import error
from app.contracts.admin import (
    AdminJob,
    AdminMe,
    AdminOperation,
    AdminPage,
    AdminUser,
    AuditEvent,
    LegacyAf3ReconcileRequest,
    ReconcileRequest,
    RevisionRequest,
)
from app.contracts.sandbox import SandboxSummary
from app.domain.sandboxes import SandboxConflict as LegacySandboxConflict
from app.ports.workspace_sandbox import WorkspaceConflict

router = APIRouter(prefix="/api/v1/admin", tags=["admin-operations"])
Users = Annotated[AdminMe, Depends(require_permission("quotas:read"))]
Jobs = Annotated[AdminMe, Depends(require_permission("jobs:read"))]
Cancel = Annotated[AdminMe, Depends(require_permission("jobs:cancel"))]
Usage = Annotated[AdminMe, Depends(require_permission("usage:read"))]
Reconcile = Annotated[AdminMe, Depends(require_permission("usage:reconcile"))]
Sandboxes = Annotated[AdminMe, Depends(require_permission("sandboxes:read"))]
Drain = Annotated[AdminMe, Depends(require_permission("sandboxes:drain"))]
Audit = Annotated[AdminMe, Depends(require_permission("audit:read"))]
Limit = Annotated[int, Query(ge=1, le=100)]


@router.get("/users")
async def list_users(
    actor: Users, request: Request, limit: Limit = 50, cursor: str | None = None
) -> AdminPage[AdminUser]:
    return await run_in_threadpool(
        request.app.state.admin_operations.users, limit=limit, cursor=cursor
    )


@router.get("/users/{user_id}/limits")
async def user_limits(user_id: str, actor: Users, request: Request) -> AdminUser:
    try:
        return await run_in_threadpool(request.app.state.admin_operations.user, user_id)
    except (ValueError, LookupError) as exc:
        raise error(exc) from exc


@router.get("/jobs")
async def list_jobs(
    actor: Jobs, request: Request, limit: Limit = 50, cursor: str | None = None
) -> AdminPage[AdminJob]:
    return await run_in_threadpool(
        request.app.state.admin_operations.jobs, actor, limit=limit, cursor=cursor
    )


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: str, payload: RevisionRequest, actor: Cancel, request: Request
) -> AdminOperation:
    try:
        return await run_in_threadpool(
            request.app.state.admin_operations.cancel,
            actor,
            job_id,
            payload,
            request_id=request.headers.get("x-request-id"),
        )
    except (ValueError, LookupError, RuntimeError) as exc:
        raise error(exc) from exc


@router.get("/usage/reconciliation")
async def reconciliation_jobs(
    actor: Usage, request: Request, limit: Limit = 50, cursor: str | None = None
) -> AdminPage[AdminJob]:
    return await run_in_threadpool(
        request.app.state.admin_operations.jobs,
        actor,
        limit=limit,
        cursor=cursor,
        reconciliation=True,
    )


@router.post("/compute/jobs/{job_id}/reconcile")
async def reconcile_job(
    job_id: str, payload: ReconcileRequest, actor: Reconcile, request: Request
) -> AdminJob:
    try:
        return await run_in_threadpool(
            request.app.state.admin_operations.reconcile,
            actor,
            job_id,
            payload,
            request_id=request.headers.get("x-request-id"),
        )
    except (ValueError, LookupError, RuntimeError) as exc:
        raise error(exc) from exc


@router.post("/af3/jobs/{job_id}/reconcile")
async def reconcile_legacy_af3_job(
    job_id: str, payload: LegacyAf3ReconcileRequest, actor: Reconcile, request: Request
) -> AdminJob:
    try:
        return await run_in_threadpool(
            request.app.state.admin_operations.reconcile_legacy_af3,
            actor,
            job_id,
            payload,
            request_id=request.headers.get("x-request-id"),
        )
    except (ValueError, LookupError, RuntimeError) as exc:
        raise error(exc) from exc


@router.get("/sandboxes")
async def list_sandboxes(
    actor: Sandboxes, request: Request, limit: Limit = 50, cursor: str | None = None
) -> AdminPage[SandboxSummary]:
    operations = getattr(request.app.state, "sandbox_operations", None)
    if operations is None:
        raise HTTPException(503, detail={"code": "SANDBOX_OPERATIONS_UNAVAILABLE"})
    try:
        rows = sorted(await operations.list(), key=lambda row: row.owner_id)
    except Exception as exc:
        raise HTTPException(503, detail={"code": "SANDBOX_OPERATIONS_UNAVAILABLE"}) from exc
    rows = [r for r in rows if r.owner_id > (cursor or "")]
    return {
        "items": rows[:limit],
        "next_cursor": rows[limit - 1].owner_id if len(rows) > limit else None,
    }


@router.post("/sandboxes/{owner_id}/drain")
async def drain_sandbox(
    owner_id: str, payload: RevisionRequest, actor: Drain, request: Request
) -> AdminOperation:
    operations = getattr(request.app.state, "sandbox_operations", None)
    if operations is None:
        raise HTTPException(503, detail={"code": "SANDBOX_OPERATIONS_UNAVAILABLE"})
    store = request.app.state.admin_store
    # Persist the authorized request before a remote side effect. Remote confirmation remains authoritative.
    await run_in_threadpool(
        record_drain,
        store,
        actor.user_id,
        owner_id,
        payload,
        "requested",
        request.headers.get("x-request-id"),
    )
    try:
        operation = await operations.drain(owner_id, payload.expected_revision)
    except (ValueError, LegacySandboxConflict, WorkspaceConflict) as exc:
        raise HTTPException(409, detail={"code": "REVISION_CONFLICT"}) from exc
    except Exception as exc:
        await run_in_threadpool(
            record_drain,
            store,
            actor.user_id,
            owner_id,
            payload,
            "failed",
            request.headers.get("x-request-id"),
        )
        raise HTTPException(503, detail={"code": "SANDBOX_OPERATIONS_UNAVAILABLE"}) from exc
    state = "confirmed" if operation.state in {"confirmed", "completed", "stopped"} else "requested"
    await run_in_threadpool(
        record_drain,
        store,
        actor.user_id,
        owner_id,
        payload,
        state,
        request.headers.get("x-request-id"),
    )
    return AdminOperation(
        operation_id=operation.operation_id,
        resource_id=owner_id,
        kind="drain",
        state=state,
        revision=operation.revision,
    )


@router.post("/sandboxes/{owner_id}/replace")
async def replace_sandbox(
    owner_id: str, payload: RevisionRequest, actor: Drain, request: Request
) -> AdminOperation:
    """Replace one stopped OpenSandbox instance while preserving its volume."""
    operations = getattr(request.app.state, "sandbox_operations", None)
    if operations is None or request.app.state.settings.workspace_provider != "opensandbox":
        raise HTTPException(503, detail={"code": "SANDBOX_OPERATIONS_UNAVAILABLE"})
    store = request.app.state.admin_store
    await run_in_threadpool(
        record_sandbox_action,
        store,
        actor.user_id,
        owner_id,
        payload,
        "sandboxes:replace",
        "requested",
        request.headers.get("x-request-id"),
    )
    try:
        operation = await operations.replace_keep_volume(
            owner_id,
            payload.expected_revision,
        )
    except (ValueError, LegacySandboxConflict, WorkspaceConflict) as exc:
        raise HTTPException(409, detail={"code": "REVISION_CONFLICT"}) from exc
    except Exception as exc:
        await run_in_threadpool(
            record_sandbox_action,
            store,
            actor.user_id,
            owner_id,
            payload,
            "sandboxes:replace",
            "failed",
            request.headers.get("x-request-id"),
        )
        raise HTTPException(503, detail={"code": "SANDBOX_OPERATIONS_UNAVAILABLE"}) from exc
    await run_in_threadpool(
        record_sandbox_action,
        store,
        actor.user_id,
        owner_id,
        payload,
        "sandboxes:replace",
        "confirmed",
        request.headers.get("x-request-id"),
    )
    return AdminOperation(
        operation_id=operation.operation_id,
        resource_id=owner_id,
        kind="replace",
        state="confirmed",
        revision=operation.revision,
    )


def record_drain(store, actor, owner_id, payload, state, request_id):
    with store.transaction():
        store.audit(
            actor,
            "sandboxes:drain",
            owner_id,
            payload.reason,
            {"revision": payload.expected_revision},
            {"state": state},
            request_id,
        )


def record_sandbox_action(
    store,
    actor,
    owner_id,
    payload,
    action,
    state,
    request_id,
):
    with store.transaction():
        store.audit(
            actor,
            action,
            owner_id,
            payload.reason,
            {"revision": payload.expected_revision},
            {"state": state},
            request_id,
        )


@router.get("/audit-events")
async def audit_events(
    actor: Audit, request: Request, limit: Limit = 50, cursor: str | None = None
) -> AdminPage[AuditEvent]:
    return await run_in_threadpool(
        request.app.state.admin_store.audit_events, limit=limit, cursor=cursor
    )
