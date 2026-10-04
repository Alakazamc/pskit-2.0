from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.api.auth import CurrentUserDep
from app.contracts.models import StorageQuota, UsageActivity, UsageEntry, UsageSnapshot
from app.domain.quota import QuotaLedger

router = APIRouter(prefix="/api/v1", tags=["usage"])


async def get_quotas(request: Request) -> QuotaLedger:
    """Provide the configured token and GPU quota ledger."""
    return request.app.state.quotas


QuotaDep = Annotated[QuotaLedger, Depends(get_quotas)]


@router.get("/usage")
async def get_usage(user: CurrentUserDep, quotas: QuotaDep, request: Request) -> UsageSnapshot:
    """Return current token, GPU, and file storage usage for the user."""
    usage = quotas.usage_for(user.id)
    catalog = request.app.state.catalog
    limit = catalog.total_storage_limit_for(user.id)
    used = catalog.stored_bytes_for(user.id)
    return usage.model_copy(update={"storage": StorageQuota(
        limit=limit, used=used,
        remaining=None if limit is None else max(0, limit - used),
    )})


@router.get("/usage/entries")
async def get_usage_entries(user: CurrentUserDep, quotas: QuotaDep) -> list[UsageEntry]:
    """List the user's recorded quota usage entries."""
    return quotas.usage_entries_for(user.id)


@router.get("/usage/activity")
def get_usage_activity(
    user: CurrentUserDep, quotas: QuotaDep, days: Annotated[int, Query(ge=1, le=366)] = 365,
) -> UsageActivity:
    """Return owner-scoped daily actual usage, zero-filled across a bounded UTC range."""
    return quotas.activity_for(user.id, days)
