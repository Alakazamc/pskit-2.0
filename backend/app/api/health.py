from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import ServiceHeartbeat, ensure_utc, now_utc
from app.db.session import get_db
from app.schemas.common import HealthResponse, ReadinessResponse, StatusItem


router = APIRouter(tags=["health"])


@router.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(ok=True, service=settings.app_name, version=settings.app_version)


@router.get("/api/live", response_model=HealthResponse)
def live() -> HealthResponse:
    return health()


@router.get("/api/ready", response_model=ReadinessResponse)
def ready(
    response: Response,
    db: Session = Depends(get_db),
) -> ReadinessResponse:
    settings = get_settings()
    checks: list[StatusItem] = []
    try:
        db.execute(text("SELECT 1"))
        checks.append(StatusItem(name="database", status="ok", detail="query ok"))
    except Exception:
        checks.append(StatusItem(name="database", status="fail", detail="query failed"))

    if settings.readiness_require_qdrant:
        try:
            from app.rag.qdrant_store import get_qdrant_client

            info = get_qdrant_client().get_collection(settings.qdrant_collection)
            points = getattr(info, "points_count", None)
            if not points:
                raise RuntimeError("knowledge collection is empty")
            checks.append(
                StatusItem(name="qdrant", status="ok", detail=f"{points} points")
            )
        except Exception:
            checks.append(
                StatusItem(name="qdrant", status="fail", detail="collection unavailable")
            )

    heartbeat = db.get(ServiceHeartbeat, "task-worker")
    stale_before = now_utc() - timedelta(
        seconds=settings.worker_readiness_max_age_seconds
    )
    if not settings.worker_readiness_required:
        checks.append(
            StatusItem(name="task-worker", status="warn", detail="check disabled")
        )
    elif heartbeat is None:
        checks.append(
            StatusItem(name="task-worker", status="fail", detail="heartbeat missing")
        )
    else:
        heartbeat_time = ensure_utc(heartbeat.updated_at)
        if heartbeat_time < stale_before:
            checks.append(
                StatusItem(name="task-worker", status="fail", detail="heartbeat stale")
            )
        else:
            checks.append(
                StatusItem(
                    name="task-worker",
                    status="ok",
                    detail=heartbeat_time.isoformat(),
                )
            )

    if settings.science_readiness_required:
        metadata = heartbeat.metadata_json if heartbeat is not None else {}
        probe_status = str(metadata.get("science_probe_status") or "missing")
        raw_checked_at = metadata.get("science_probe_checked_at")
        try:
            checked_at = ensure_utc(
                datetime.fromisoformat(str(raw_checked_at).replace("Z", "+00:00"))
            )
            probe_age = max((now_utc() - checked_at).total_seconds(), 0.0)
        except (TypeError, ValueError):
            probe_age = None
        probe_fresh = (
            probe_status in {"fresh", "refreshing"}
            and isinstance(probe_age, (int, float))
            and 0 <= float(probe_age)
            <= settings.science_readiness_max_probe_age_seconds
        )
        science_ready = bool(metadata.get("science_ready")) and probe_fresh
        missing = metadata.get("missing_dependencies")
        missing_names = (
            ", ".join(str(item) for item in missing)
            if isinstance(missing, list)
            else "worker evidence unavailable"
        )
        checks.append(
            StatusItem(
                name="science-worker",
                status="ok" if science_ready else "fail",
                detail=(
                    "dependency boundary ready"
                    if science_ready
                    else (
                        missing_names
                        if probe_fresh
                        else f"dependency probe {probe_status} or stale"
                    )
                ),
            )
        )
    ok = not any(item.status == "fail" for item in checks)
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        ok=ok,
        service=settings.app_name,
        version=settings.app_version,
        checks=checks,
    )
