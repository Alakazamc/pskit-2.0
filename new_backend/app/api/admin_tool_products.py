"""Review-gated administration for MCP services and config-driven Tool Products."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from app.api.admin_auth import require_permission
from app.contracts.admin import AdminMe
from app.contracts.tool_products import (
    AcceptanceSuite,
    DiscoverySnapshot,
    ProbeSnapshot,
    PublishedToolProduct,
    QualificationReport,
    ToolProductDraft,
)
from app.domain.tool_products.repository import (
    QualificationStale,
    ToolProductRevisionConflict,
)

router = APIRouter(prefix="/api/v1/admin", tags=["admin-tool-products"])
Read = Annotated[AdminMe, Depends(require_permission("services:read"))]
Write = Annotated[AdminMe, Depends(require_permission("services:write"))]
Publish = Annotated[AdminMe, Depends(require_permission("services:publish"))]


class AdminToolProductRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class McpProbeRequest(AdminToolProductRequest):
    service_id: str = Field(min_length=1, max_length=120)
    uri: str = Field(min_length=1, max_length=2000)
    transport: Literal["streamable_http", "sse"] = "streamable_http"
    credential_ref: str | None = Field(default=None, max_length=200)
    network_zone: str = Field(default="public", min_length=1, max_length=120)


class DiscoveryRequest(AdminToolProductRequest):
    service_revision: int = Field(gt=0)
    reason: str = Field(min_length=5, max_length=2000)


class ProductDraftRequest(AdminToolProductRequest):
    expected_revision: int = Field(ge=0)
    reason: str = Field(min_length=5, max_length=2000)
    draft: ToolProductDraft
    acceptance_suite: AcceptanceSuite | None = None


class QualificationRequest(AdminToolProductRequest):
    revision: int = Field(gt=0)
    suite_revision: int = Field(gt=0)
    reason: str = Field(min_length=5, max_length=2000)


class PublishProductRequest(AdminToolProductRequest):
    product_id: str = Field(min_length=1, max_length=120)
    revision: int = Field(gt=0)
    reason: str = Field(min_length=5, max_length=2000)


class ReleaseActionRequest(AdminToolProductRequest):
    reason: str = Field(min_length=5, max_length=2000)


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, PermissionError):
        return HTTPException(403, detail={"code": "ADMIN_FORBIDDEN"})
    if isinstance(exc, (ToolProductRevisionConflict, QualificationStale)):
        return HTTPException(409, detail={"code": str(exc)})
    if isinstance(exc, LookupError):
        return HTTPException(404, detail={"code": str(exc)})
    if isinstance(exc, RuntimeError):
        return HTTPException(503, detail={"code": str(exc)})
    return HTTPException(422, detail={"code": str(exc)})


def _require_platform_admin(actor: AdminMe) -> None:
    if "platform_admin" not in actor.roles:
        raise HTTPException(403, detail={"code": "ADMIN_FORBIDDEN"})


def _require_product_scope(actor: AdminMe, draft: ToolProductDraft) -> None:
    if "platform_admin" in actor.roles:
        return
    service_ids = {binding.service_id for binding in draft.bindings}
    if draft.owner_user_id != actor.user_id or not service_ids.issubset(actor.service_ids):
        raise HTTPException(403, detail={"code": "ADMIN_FORBIDDEN"})


@router.post("/mcp-probes")
async def probe_mcp(
    payload: McpProbeRequest, actor: Write, request: Request
) -> ProbeSnapshot:
    _require_platform_admin(actor)
    try:
        return await request.app.state.mcp_qualification.probe(
            **payload.model_dump(), actor_id=actor.user_id
        )
    except (ValueError, LookupError, PermissionError, RuntimeError) as exc:
        raise _error(exc) from exc


@router.get("/mcp-probes/{probe_id}")
async def get_mcp_probe(
    probe_id: str, actor: Read, request: Request
) -> ProbeSnapshot:
    probe = await run_in_threadpool(
        request.app.state.mcp_qualification.get_probe, probe_id
    )
    if probe is None:
        raise HTTPException(404, detail={"code": "MCP_PROBE_NOT_FOUND"})
    if "platform_admin" not in actor.roles and probe.service_id not in actor.service_ids:
        raise HTTPException(403, detail={"code": "ADMIN_FORBIDDEN"})
    return probe


@router.post("/services/{service_id}/discoveries")
async def discover_mcp(
    service_id: str, payload: DiscoveryRequest, actor: Write, request: Request
) -> DiscoverySnapshot:
    if "platform_admin" not in actor.roles and service_id not in actor.service_ids:
        raise HTTPException(403, detail={"code": "ADMIN_FORBIDDEN"})
    try:
        return await request.app.state.mcp_qualification.discover(
            service_id, payload.service_revision
        )
    except (ValueError, LookupError, PermissionError, RuntimeError) as exc:
        raise _error(exc) from exc


@router.put("/tool-products/{product_id}/draft")
async def save_product_draft(
    product_id: str, payload: ProductDraftRequest, actor: Write, request: Request
) -> ToolProductDraft:
    _require_product_scope(actor, payload.draft)
    repository = request.app.state.tool_product_repository
    try:
        if payload.acceptance_suite is not None:
            if (
                payload.acceptance_suite.suite_id != payload.draft.acceptance_suite_id
                or payload.acceptance_suite.revision
                != payload.draft.acceptance_suite_revision
            ):
                raise ValueError("ACCEPTANCE_SUITE_MISMATCH")
            await run_in_threadpool(
                repository.save_acceptance_suite, payload.acceptance_suite
            )
        return await run_in_threadpool(
            repository.save_draft,
            actor.user_id,
            product_id,
            payload.draft,
            payload.expected_revision,
        )
    except (ValueError, LookupError, PermissionError, RuntimeError) as exc:
        raise _error(exc) from exc


@router.post("/tool-products/{product_id}/qualifications")
async def qualify_product(
    product_id: str, payload: QualificationRequest, actor: Write, request: Request
) -> QualificationReport:
    repository = request.app.state.tool_product_repository
    try:
        draft = await run_in_threadpool(
            repository.get_draft_revision, product_id, payload.revision
        )
        _require_product_scope(actor, draft)
        if payload.suite_revision != draft.acceptance_suite_revision:
            raise ValueError("ACCEPTANCE_SUITE_MISMATCH")
        return await request.app.state.mcp_qualification.qualify(
            product_id, payload.revision, payload.suite_revision
        )
    except HTTPException:
        raise
    except (ValueError, LookupError, PermissionError, RuntimeError) as exc:
        raise _error(exc) from exc


@router.get("/qualifications/{report_id}")
async def get_qualification(
    report_id: str, _actor: Read, request: Request
) -> QualificationReport:
    report = await run_in_threadpool(
        request.app.state.mcp_qualification.get_report, report_id
    )
    if report is None:
        raise HTTPException(404, detail={"code": "QUALIFICATION_REPORT_NOT_FOUND"})
    return report


@router.post("/tool-product-releases/{report_id}/publish")
async def publish_product(
    report_id: str, payload: PublishProductRequest, actor: Publish, request: Request
) -> PublishedToolProduct:
    try:
        return await run_in_threadpool(
            request.app.state.tool_product_repository.publish,
            payload.product_id,
            payload.revision,
            report_id,
            actor.user_id,
            payload.reason,
        )
    except (ValueError, LookupError, PermissionError, RuntimeError) as exc:
        raise _error(exc) from exc


@router.post("/tool-product-releases/{release_id}/suspend")
async def suspend_product(
    release_id: str, _payload: ReleaseActionRequest, actor: Publish, request: Request
) -> PublishedToolProduct:
    try:
        return await run_in_threadpool(
            request.app.state.tool_product_repository.suspend,
            release_id,
            actor.user_id,
        )
    except (ValueError, LookupError, PermissionError, RuntimeError) as exc:
        raise _error(exc) from exc


@router.post("/tool-product-releases/{release_id}/rollback")
async def rollback_product(
    release_id: str, _payload: ReleaseActionRequest, actor: Publish, request: Request
) -> PublishedToolProduct:
    try:
        return await run_in_threadpool(
            request.app.state.tool_product_repository.rollback,
            release_id,
            actor.user_id,
        )
    except (ValueError, LookupError, PermissionError, RuntimeError) as exc:
        raise _error(exc) from exc
