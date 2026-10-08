import secrets
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from app.api.admin_auth import get_admin_principal
from app.api.admin_services import error
from app.contracts.admin import AdminUser, LimitsUpdate
from app.contracts.capabilities import Af3GpuReconciliation, Af3Job
from app.contracts.catalog import CatalogItem, SkillDetail, SkillReviewRequest
from app.contracts.models import UsageSnapshot
from app.domain.catalog import ContextNotFound, SkillManifest, SkillVersionConflict
from app.domain.persistent_conversation import PersistentConversationStore
from app.services.dependency_probe import DependencyReport, inspect_dependencies

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class UserLimitsRequest(BaseModel):
    token_monthly_limit: int = Field(ge=0)
    gpu_daily_minutes: int = Field(ge=0, le=1440)


class UserSkillGrantsRequest(BaseModel):
    skill_ids: list[str]
    expected_revision: int | None = Field(default=None, ge=0)
    reason: str | None = Field(default=None, min_length=5, max_length=500)


class GpuReconciliationRequest(BaseModel):
    actual_gpu_minutes: int = Field(ge=0, le=1440)
    reason: str = Field(min_length=5, max_length=500)


class RegisterSkillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1000)
    tools: list[str] = Field(max_length=32)
    instructions: str = Field(min_length=1, max_length=65_536)
    expected_revision: int | None = Field(default=None, ge=0)
    reason: str | None = Field(default=None, min_length=5, max_length=500)


async def _require_admin(request: Request, key: str | None, permission: str = "audit:read") -> str:
    """Allow access only when a configured administrator key matches."""
    if request.headers.get("authorization"):
        from app.api.auth import bearer, get_current_user

        credentials = await bearer(request)
        user = await get_current_user(credentials, request)
        principal = await get_admin_principal(user, request)
        if permission not in principal.permissions:
            raise HTTPException(403, detail={"code": "ADMIN_FORBIDDEN"})
        return principal.user_id
    configured = request.app.state.settings.admin_api_key
    if not configured:
        raise HTTPException(status_code=404, detail="Not found")
    if not key or not secrets.compare_digest(key, configured):
        raise HTTPException(status_code=403, detail="Forbidden")
    return "operator:legacy-admin-key"


@router.get("/dependencies")
async def dependency_status(
    request: Request,
    x_admin_key: Annotated[str | None, Header()] = None,
) -> DependencyReport:
    """Report live dependency status to an authenticated administrator."""
    await _require_admin(request, x_admin_key)
    return await inspect_dependencies(request.app)


@router.put("/af3/jobs/{job_id}/gpu-usage")
async def reconcile_af3_gpu_usage(
    job_id: str,
    payload: GpuReconciliationRequest,
    request: Request,
    x_admin_key: Annotated[str | None, Header()] = None,
) -> Af3Job:
    """Correct one AF3 job's settled GPU usage with an audit reason."""
    actor_id = await _require_admin(request, x_admin_key, "usage:reconcile")
    if request.headers.get("authorization"):
        raise HTTPException(422, detail={"code": "USE_AF3_RECONCILIATION_ENDPOINT"})
    store = request.app.state.conversations
    if not isinstance(store, PersistentConversationStore):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        job = store.reconcile_af3_gpu_usage(
            job_id,
            payload.actual_gpu_minutes,
            payload.reason,
            audit_actor=actor_id,
            audit_callback=getattr(request.app.state, "admin_store", None).audit
            if getattr(request.app.state, "admin_store", None)
            else None,
            audit_request_id=request.headers.get("x-request-id"),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=409, detail={"code": "GPU_RECONCILIATION_CONFLICT"}
        ) from exc
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/af3/jobs/{job_id}/gpu-usage/audit")
async def list_af3_gpu_reconciliations(
    job_id: str,
    request: Request,
    x_admin_key: Annotated[str | None, Header()] = None,
) -> list[Af3GpuReconciliation]:
    """List the audited GPU accounting changes for an AF3 job."""
    await _require_admin(request, x_admin_key)
    store = request.app.state.conversations
    if (
        not isinstance(store, PersistentConversationStore)
        or store.af3_job_for_compute(job_id) is None
    ):
        raise HTTPException(status_code=404, detail="Job not found")
    return store.gpu_reconciliations_for(job_id)


@router.put("/skills/{skill_id}/versions/{version}")
async def register_skill_version(
    skill_id: str,
    version: int,
    payload: RegisterSkillRequest,
    request: Request,
    x_admin_key: Annotated[str | None, Header()] = None,
) -> CatalogItem:
    """Register an immutable version of an administrator-managed Skill."""
    actor_id = await _require_admin(request, x_admin_key, "services:publish")
    if request.headers.get("authorization") and (
        payload.expected_revision is None or not payload.reason
    ):
        raise HTTPException(422, detail={"code": "REVISION_REASON_REQUIRED"})
    try:
        manifest = SkillManifest(
            id=skill_id,
            version=version,
            name=payload.name,
            description=payload.description,
            tools=payload.tools,
        )
        return request.app.state.catalog.register_skill_version(
            manifest,
            payload.instructions,
            audit_actor=actor_id,
            audit_callback=getattr(request.app.state, "admin_store", None).audit
            if getattr(request.app.state, "admin_store", None)
            else None,
            audit_reason=payload.reason or "Legacy operator Skill registration",
            audit_request_id=request.headers.get("x-request-id"),
            expected_revision=payload.expected_revision,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID_SKILL_DEFINITION"}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID_SKILL_DEFINITION"}) from exc
    except SkillVersionConflict as exc:
        raise HTTPException(status_code=409, detail={"code": "SKILL_VERSION_CONFLICT"}) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail={"code": "SKILL_REGISTRY_UNAVAILABLE"}) from exc


@router.get("/skills/reviews")
async def list_skill_reviews(
    request: Request,
    x_admin_key: Annotated[str | None, Header()] = None,
) -> list[SkillDetail]:
    """List user Skill versions awaiting public catalog review."""
    await _require_admin(request, x_admin_key, "services:publish")
    return request.app.state.catalog.pending_skill_reviews()


@router.post("/skills/{skill_id}/versions/{version}/review")
async def review_skill_version(
    skill_id: str,
    version: int,
    payload: SkillReviewRequest,
    request: Request,
    x_admin_key: Annotated[str | None, Header()] = None,
) -> CatalogItem:
    """Approve a pending Skill for everyone or return it privately with a reason."""
    actor_id = await _require_admin(request, x_admin_key, "services:publish")
    try:
        return request.app.state.catalog.review_skill(
            skill_id,
            version,
            approved=payload.decision == "approve",
            reason=payload.reason,
            audit_actor=actor_id,
            audit_callback=getattr(request.app.state, "admin_store", None).audit
            if getattr(request.app.state, "admin_store", None)
            else None,
            audit_request_id=request.headers.get("x-request-id"),
        )
    except ContextNotFound as exc:
        raise HTTPException(status_code=404, detail={"code": "SKILL_NOT_FOUND"}) from exc
    except SkillVersionConflict as exc:
        raise HTTPException(status_code=409, detail={"code": "SKILL_REVIEW_CONFLICT"}) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail={"code": "SKILL_REGISTRY_UNAVAILABLE"}) from exc


@router.put("/users/{user_id}/limits")
async def set_user_limits(
    user_id: str,
    payload: LimitsUpdate | UserLimitsRequest,
    request: Request,
    x_admin_key: Annotated[str | None, Header()] = None,
) -> AdminUser | UsageSnapshot:
    """Apply browser limits with current authorization, revision and atomic audit."""
    actor_id = await _require_admin(request, x_admin_key, "quotas:write")
    browser = bool(request.headers.get("authorization"))
    operations = getattr(request.app.state, "admin_operations", None)
    if browser and not isinstance(payload, LimitsUpdate):
        raise HTTPException(422, detail={"code": "REVISION_REASON_REQUIRED"})
    if operations:
        store = request.app.state.admin_store
        if not isinstance(payload, LimitsUpdate):
            current = await run_in_threadpool(operations.user, user_id)
            payload = LimitsUpdate(
                expected_revision=current.revision,
                reason="Legacy operator limit update",
                token_monthly_limit=payload.token_monthly_limit,
                gpu_daily_minutes=payload.gpu_daily_minutes,
                cpu_daily_core_ms=current.cpu_daily_core_ms,
                concurrency_limit=current.concurrency_limit,
                storage_limit_bytes=current.storage_limit_bytes or 0,
            )
        try:
            result = await run_in_threadpool(
                operations.update_limits,
                store.principal(actor_id),
                user_id,
                payload,
                request_id=request.headers.get("x-request-id"),
                preserve_resource_limits=not browser,
            )
        except (ValueError, LookupError, RuntimeError) as exc:
            raise error(exc) from exc
        return result if browser else request.app.state.quotas.usage_for(user_id)
    quotas = request.app.state.quotas
    quotas.set_token_limit(user_id, payload.token_monthly_limit)
    quotas.set_gpu_limit(user_id, payload.gpu_daily_minutes)
    return quotas.usage_for(user_id)


@router.put("/users/{user_id}/skills", response_model_exclude_none=True)
async def set_user_skill_grants(
    user_id: str,
    payload: UserSkillGrantsRequest,
    request: Request,
    x_admin_key: Annotated[str | None, Header()] = None,
) -> UserSkillGrantsRequest:
    """Replace the Skill IDs granted to a user."""
    actor_id = await _require_admin(request, x_admin_key, "services:publish")
    if request.headers.get("authorization") and (
        payload.expected_revision is None or not payload.reason
    ):
        raise HTTPException(422, detail={"code": "REVISION_REASON_REQUIRED"})
    try:
        skill_ids = request.app.state.catalog.set_skill_grants(
            user_id,
            payload.skill_ids,
            audit_actor=actor_id,
            audit_callback=getattr(request.app.state, "admin_store", None).audit
            if getattr(request.app.state, "admin_store", None)
            else None,
            audit_reason=payload.reason or "Legacy operator Skill grants",
            audit_request_id=request.headers.get("x-request-id"),
            expected_revision=payload.expected_revision,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID_SKILL_GRANTS"}) from exc
    return UserSkillGrantsRequest(skill_ids=skill_ids)
