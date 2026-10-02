import secrets
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.contracts.capabilities import Af3GpuReconciliation, Af3Job
from app.contracts.catalog import CatalogItem
from app.contracts.models import UsageSnapshot
from app.domain.catalog import SkillManifest, SkillVersionConflict
from app.domain.persistent_conversation import PersistentConversationStore
from app.services.dependency_probe import DependencyReport, inspect_dependencies

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


class UserLimitsRequest(BaseModel):
    token_monthly_limit: int = Field(ge=0)
    gpu_daily_minutes: int = Field(ge=0, le=1440)


class UserSkillGrantsRequest(BaseModel):
    skill_ids: list[str]


class GpuReconciliationRequest(BaseModel):
    actual_gpu_minutes: int = Field(ge=0, le=1440)
    reason: str = Field(min_length=5, max_length=500)


class RegisterSkillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1000)
    tools: list[str] = Field(max_length=32)
    instructions: str = Field(min_length=1, max_length=65_536)


def _require_admin(request: Request, key: str | None) -> None:
    """Allow access only when a configured administrator key matches."""
    configured = request.app.state.settings.admin_api_key
    if not configured:
        raise HTTPException(status_code=404, detail="Not found")
    if not key or not secrets.compare_digest(key, configured):
        raise HTTPException(status_code=403, detail="Forbidden")


@router.get("/dependencies")
async def dependency_status(
    request: Request, x_admin_key: Annotated[str | None, Header()] = None,
) -> DependencyReport:
    """Report live dependency status to an authenticated administrator."""
    _require_admin(request, x_admin_key)
    return await inspect_dependencies(request.app)


@router.put("/af3/jobs/{job_id}/gpu-usage")
async def reconcile_af3_gpu_usage(
    job_id: str, payload: GpuReconciliationRequest, request: Request,
    x_admin_key: Annotated[str | None, Header()] = None,
) -> Af3Job:
    """Correct one AF3 job's settled GPU usage with an audit reason."""
    _require_admin(request, x_admin_key)
    store = request.app.state.conversations
    if not isinstance(store, PersistentConversationStore):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        job = store.reconcile_af3_gpu_usage(
            job_id, payload.actual_gpu_minutes, payload.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": "GPU_RECONCILIATION_CONFLICT"}) from exc
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/af3/jobs/{job_id}/gpu-usage/audit")
async def list_af3_gpu_reconciliations(
    job_id: str, request: Request,
    x_admin_key: Annotated[str | None, Header()] = None,
) -> list[Af3GpuReconciliation]:
    """List the audited GPU accounting changes for an AF3 job."""
    _require_admin(request, x_admin_key)
    store = request.app.state.conversations
    if not isinstance(store, PersistentConversationStore) or store.af3_job_for_compute(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return store.gpu_reconciliations_for(job_id)


@router.put("/skills/{skill_id}/versions/{version}")
async def register_skill_version(
    skill_id: str, version: int, payload: RegisterSkillRequest, request: Request,
    x_admin_key: Annotated[str | None, Header()] = None,
) -> CatalogItem:
    """Register an immutable version of an administrator-managed Skill."""
    configured = request.app.state.settings.admin_api_key
    if not configured:
        raise HTTPException(status_code=404, detail="Not found")
    if not x_admin_key or not secrets.compare_digest(x_admin_key, configured):
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        manifest = SkillManifest(
            id=skill_id, version=version, name=payload.name,
            description=payload.description, tools=payload.tools,
        )
        return request.app.state.catalog.register_skill_version(manifest, payload.instructions)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID_SKILL_DEFINITION"}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID_SKILL_DEFINITION"}) from exc
    except SkillVersionConflict as exc:
        raise HTTPException(status_code=409, detail={"code": "SKILL_VERSION_CONFLICT"}) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail={"code": "SKILL_REGISTRY_UNAVAILABLE"}) from exc


@router.put("/users/{user_id}/limits")
async def set_user_limits(
    user_id: str, payload: UserLimitsRequest, request: Request,
    x_admin_key: Annotated[str | None, Header()] = None,
) -> UsageSnapshot:
    """Set a user's monthly token and daily GPU quotas."""
    configured = request.app.state.settings.admin_api_key
    if not configured:
        raise HTTPException(status_code=404, detail="Not found")
    if not x_admin_key or not secrets.compare_digest(x_admin_key, configured):
        raise HTTPException(status_code=403, detail="Forbidden")
    quotas = request.app.state.quotas
    quotas.set_token_limit(user_id, payload.token_monthly_limit)
    quotas.set_gpu_limit(user_id, payload.gpu_daily_minutes)
    return quotas.usage_for(user_id)


@router.put("/users/{user_id}/skills")
async def set_user_skill_grants(
    user_id: str, payload: UserSkillGrantsRequest, request: Request,
    x_admin_key: Annotated[str | None, Header()] = None,
) -> UserSkillGrantsRequest:
    """Replace the Skill IDs granted to a user."""
    configured = request.app.state.settings.admin_api_key
    if not configured:
        raise HTTPException(status_code=404, detail="Not found")
    if not x_admin_key or not secrets.compare_digest(x_admin_key, configured):
        raise HTTPException(status_code=403, detail="Forbidden")
    try:
        skill_ids = request.app.state.catalog.set_skill_grants(user_id, payload.skill_ids)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "INVALID_SKILL_GRANTS"}) from exc
    return UserSkillGrantsRequest(skill_ids=skill_ids)
