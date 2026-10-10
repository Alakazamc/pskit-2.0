from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.db.postgres_migrations import SCHEMA_VERSION
from app.domain.workspace_access import WorkspaceAccessConfigurationError

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    """Report that the API process is running."""
    return {"status": "ok"}


@router.get("/health/ready", response_model=None)
async def ready(request: Request) -> dict[str, object] | JSONResponse:
    """Check local databases and report configured service modes."""
    database = getattr(request.app.state, "database", None)
    if database is not None:
        try:
            database.check_schema_version(SCHEMA_VERSION)
        except Exception:  # noqa: BLE001 - all pool/schema failures make readiness unavailable
            return JSONResponse(
                status_code=503,
                content={
                    "status": "unavailable",
                    "code": "DATABASE_UNAVAILABLE",
                },
            )
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
            return JSONResponse(
                status_code=503,
                content={
                    "status": "unavailable",
                    "code": "DATABASE_UNAVAILABLE",
                },
            )
    settings = request.app.state.settings
    workspace = await request.app.state.workspace_sandbox_provider.readiness()
    try:
        rollout = request.app.state.workspace_access.current()
        rollout_status: dict[str, object] = {
            "state": "configured",
            "enabled": rollout.enabled,
            "commands_enabled": rollout.commands_enabled,
            "allowlisted_users": len(rollout.user_allowlist),
        }
    except WorkspaceAccessConfigurationError:
        rollout_status = {
            "state": "invalid",
            "enabled": False,
            "commands_enabled": False,
            "allowlisted_users": 0,
        }
    content: dict[str, object] = {
        "status": "ready",
        "identity": settings.mode,
        "agent": settings.agent_runtime,
        "mcp": request.app.state.mcp_executor,
        "af3": request.app.state.af3_executor,
        "external_services": "unchecked",
        "workspace_provider": {
            "provider": workspace.capabilities.provider,
            "state": workspace.state.value,
            "code": workspace.code,
            "runtime": workspace.capabilities.runtime,
            "file_access": workspace.capabilities.file_access,
            "command_execution": workspace.capabilities.command_execution,
            "persistent_volume": workspace.capabilities.persistent_volume,
            "session_mount_namespace": workspace.capabilities.session_mount_namespace,
            "rollout": rollout_status,
        },
    }
    if settings.workspace_provider == "opensandbox" and not workspace.ready:
        content["status"] = "unavailable"
        return JSONResponse(status_code=503, content=content)
    return content
