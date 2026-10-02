import sqlite3

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    """Report that the API process is running."""
    return {"status": "ok"}


@router.get("/health/ready", response_model=None)
async def ready(request: Request) -> dict[str, str] | JSONResponse:
    """Check local databases and report configured service modes."""
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
        except sqlite3.Error:
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
