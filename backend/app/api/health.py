from fastapi import APIRouter

from app.config import get_settings
from app.schemas.common import HealthResponse


router = APIRouter(tags=["health"])


@router.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(ok=True, service=settings.app_name, version="0.1.0")

