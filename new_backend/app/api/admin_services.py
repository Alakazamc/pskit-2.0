"""Scoped scientific service maintenance and reviewed release packages."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from app.api.admin_auth import require_permission
from app.api.admin_models import management_error
from app.contracts.admin import (
    AdminMe,
    AdminPage,
    AdminService,
    ConfigRelease,
    ConfigReleaseRequest,
    RevisionRequest,
    ServiceCheck,
    ServiceCheckRequest,
    ServiceDraft,
)

router = APIRouter(prefix="/api/v1/admin", tags=["admin-services"])
Read = Annotated[AdminMe, Depends(require_permission("services:read"))]
Write = Annotated[AdminMe, Depends(require_permission("services:write"))]
Publish = Annotated[AdminMe, Depends(require_permission("services:publish"))]


def error(exc):
    if isinstance(exc, PermissionError):
        return HTTPException(403, detail={"code": "ADMIN_FORBIDDEN"})
    if isinstance(exc, RuntimeError):
        return HTTPException(503, detail={"code": str(exc)})
    return management_error(exc)


@router.get("/services")
async def list_services(
    actor: Read,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: str | None = None,
) -> AdminPage[AdminService]:
    return await run_in_threadpool(
        request.app.state.admin_releases.list, actor, limit=limit, cursor=cursor
    )


@router.put("/services/{service_id}/draft")
async def save_service(
    service_id: str, payload: ServiceDraft, actor: Write, request: Request
) -> AdminService:
    try:
        return await run_in_threadpool(
            request.app.state.admin_releases.save,
            actor,
            service_id,
            payload,
            request_id=request.headers.get("x-request-id"),
        )
    except (ValueError, LookupError, PermissionError, RuntimeError) as exc:
        raise error(exc) from exc


@router.put("/capabilities/{capability_id}/draft")
async def save_capability(
    capability_id: str, payload: ServiceDraft, actor: Write, request: Request
) -> AdminService:
    if capability_id not in {cap.id for cap in payload.capabilities}:
        raise HTTPException(422, detail={"code": "CAPABILITY_DRAFT_MISSING"})
    service_id = capability_id.split(".")[0]
    return await save_service(service_id, payload, actor, request)


@router.post("/services/{service_id}/discovery")
async def discover_service(
    service_id: str, payload: RevisionRequest, actor: Write, request: Request
) -> ServiceCheck:
    try:
        return await request.app.state.admin_releases.check(
            actor,
            service_id,
            payload,
            discover=True,
            request_id=request.headers.get("x-request-id"),
        )
    except (ValueError, LookupError, PermissionError, RuntimeError) as exc:
        raise error(exc) from exc


@router.post("/services/{service_id}/checks")
async def check_service(
    service_id: str, payload: ServiceCheckRequest, actor: Write, request: Request
) -> ServiceCheck:
    try:
        return await request.app.state.admin_releases.check(
            actor, service_id, payload, request_id=request.headers.get("x-request-id")
        )
    except (ValueError, LookupError, PermissionError, RuntimeError) as exc:
        raise error(exc) from exc


@router.post("/config-releases")
async def create_release(
    payload: ConfigReleaseRequest, actor: Publish, request: Request
) -> ConfigRelease:
    try:
        return await run_in_threadpool(
            request.app.state.admin_releases.create,
            actor,
            payload,
            request_id=request.headers.get("x-request-id"),
        )
    except (ValueError, LookupError, PermissionError, RuntimeError) as exc:
        raise error(exc) from exc


@router.post("/config-releases/{release_id}/publish")
async def publish_release(
    release_id: str, payload: RevisionRequest, actor: Publish, request: Request
) -> ConfigRelease:
    try:
        return await run_in_threadpool(
            request.app.state.admin_releases.publish,
            actor,
            release_id,
            payload,
            request_id=request.headers.get("x-request-id"),
        )
    except (ValueError, LookupError, PermissionError, RuntimeError) as exc:
        raise error(exc) from exc
