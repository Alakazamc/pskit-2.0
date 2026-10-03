from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.db.postgres_migrations import SCHEMA_VERSION

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    """Report that the API process is running."""
    return {"status": "ok"}


@router.get("/health/ready", response_model=None)
async def ready(request: Request) -> dict[str, str] | JSONResponse:
    """Check local databases and report configured service modes."""
    database = getattr(request.app.state, "database", None)
    if database is not None:
        try:
            database.check_schema_version(SCHEMA_VERSION)
        except Exception:  # noqa: BLE001 - all pool/schema failures make readiness unavailable
            return JSONResponse(status_code=503, content={
                "status": "unavailable", "code": "DATABASE_UNAVAILABLE",
            })
    for owner in (
        request.app.state.conversations,
        request.app.state.catalog,
        request.app.state.oauth_flows,
    ):
        db = getattr(owner, "db", None)
        if db is None:
            continue
        try:
            db.execute("SELECT 1").fetchone()
        except Exception:  # noqa: BLE001 - every storage failure removes readiness
            return JSONResponse(status_code=503, content={
                "status": "unavailable", "code": "DATABASE_UNAVAILABLE",
            })
    settings = request.app.state.settings
    return {
        "status": "ready",
        "identity": settings.mode,
        "agent": settings.agent_runtime,
        "mcp": request.app.state.mcp_executor,
        "af3": request.app.state.af3_executor,
        "external_services": "unchecked",
    }
