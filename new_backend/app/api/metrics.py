import secrets
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request

router = APIRouter(tags=["metrics"], include_in_schema=False)


@router.get("/internal/metrics")
async def metrics(
    request: Request, admin_key: Annotated[str | None, Header(alias="X-Admin-Key")] = None,
) -> dict:
    """Return internal counters only when the administrator key matches."""
    configured = request.app.state.settings.admin_api_key
    if not configured or not admin_key or not secrets.compare_digest(admin_key, configured):
        raise HTTPException(status_code=404, detail="Not found")
    return request.app.state.metrics.snapshot()
