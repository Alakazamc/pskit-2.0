import httpx
from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import get_db
from app.schemas.common import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/api/health/live", response_model=HealthResponse)
def live() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(ok=True, service=settings.app_name, version="0.2.0")


def readiness(response: Response, db: Session) -> HealthResponse:
    settings = get_settings()
    checks: dict[str, str] = {}
    ok = True
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except SQLAlchemyError as exc:
        ok = False
        checks["database"] = f"error:{exc.__class__.__name__}"

    if settings.readiness_require_qdrant:
        try:
            with httpx.Client(timeout=3) as client:
                qdrant_response = client.get(f"{settings.qdrant_url.rstrip('/')}/healthz")
                qdrant_response.raise_for_status()
            checks["qdrant"] = "ok"
        except (httpx.HTTPError, OSError) as exc:
            ok = False
            checks["qdrant"] = f"error:{exc.__class__.__name__}"
    else:
        checks["qdrant"] = "optional"

    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(ok=ok, service=settings.app_name, version="0.2.0", checks=checks)


@router.get("/api/health/ready", response_model=HealthResponse)
def ready(response: Response, db: Session = Depends(get_db)) -> HealthResponse:
    return readiness(response, db)


@router.get("/api/health", response_model=HealthResponse)
def health(response: Response, db: Session = Depends(get_db)) -> HealthResponse:
    return readiness(response, db)
