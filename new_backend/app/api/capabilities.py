from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel

from app.adapters.live.limited_mcp import McpCapacityExceeded
from app.api.auth import CurrentUserDep
from app.api.mcp_validation import validate_mcp_arguments
from app.api.runs import ConversationStoreDep
from app.contracts.capabilities import Af3Job, Af3JobRequest, McpInvokeResult, McpTool
from app.domain.af3_requests import Af3IdempotencyConflict
from app.domain.quota import GpuQuotaExceeded
from app.domain.tool_runs import ToolRun
from app.ports.providers import Af3Provider, McpProvider, ProviderUnavailable

router = APIRouter(prefix="/api/v1", tags=["capabilities"])


async def get_mcp(request: Request) -> McpProvider:
    """Provide the configured MCP adapter to request handlers."""
    return request.app.state.mcp


async def get_af3(request: Request) -> Af3Provider:
    """Provide the configured AF3 adapter to request handlers."""
    return request.app.state.af3


McpDep = Annotated[McpProvider, Depends(get_mcp)]
Af3Dep = Annotated[Af3Provider, Depends(get_af3)]


@router.get("/mcp/tools")
async def list_mcp_tools(user: CurrentUserDep, mcp: McpDep, request: Request) -> list[McpTool]:
    """List discovered tools allowed for the authenticated user."""
    if request.app.state.mcp_unavailable:
        raise HTTPException(status_code=503, detail={"code": "MCP_UPSTREAM_UNAVAILABLE"})
    catalog = request.app.state.catalog
    return [tool for tool in mcp.tools() if catalog.tool_allowed_for(user.id, tool.name)]


@router.post("/mcp/tools/{name}/invoke")
async def invoke_mcp_tool(
    name: str, arguments: dict, user: CurrentUserDep, mcp: McpDep, request: Request,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)] = None,
) -> McpInvokeResult:
    """Validate and invoke an allowed MCP tool with optional idempotency."""
    if request.app.state.mcp_executor == "disabled":
        raise HTTPException(status_code=503, detail={"code": "MCP_NOT_CONFIGURED"})
    if request.app.state.mcp_unavailable:
        raise HTTPException(status_code=503, detail={"code": "MCP_UPSTREAM_UNAVAILABLE"})
    if not request.app.state.catalog.tool_allowed_for(user.id, name):
        raise HTTPException(status_code=403, detail={"code": "TOOL_NOT_ALLOWED"})
    validate_mcp_arguments(mcp, name, arguments)
    calls = request.app.state.mcp_tool_calls if idempotency_key else None
    scope = f"public:{user.id}"
    if calls is not None:
        try:
            state, cached = calls.claim(scope, idempotency_key, name, arguments)
        except ProviderUnavailable as exc:
            raise HTTPException(status_code=503, detail={"code": "MCP_LEDGER_UNAVAILABLE"}) from exc
        if state == "conflict":
            raise HTTPException(status_code=409, detail={"code": "MCP_TOOL_CALL_CONFLICT"})
        if state == "unknown":
            raise HTTPException(status_code=409, detail={"code": "MCP_TOOL_CALL_OUTCOME_UNKNOWN"})
        if cached is not None:
            return cached
    try:
        result = await mcp.invoke(name, arguments)
    except McpCapacityExceeded as exc:
        if calls is not None:
            calls.release_before_invoke(scope, idempotency_key)
        raise HTTPException(status_code=429, detail={"code": "MCP_CAPACITY_EXCEEDED"}) from exc
    except ProviderUnavailable as exc:
        if calls is not None:
            calls.mark_unknown(scope, idempotency_key)
        raise HTTPException(status_code=502, detail={"code": "MCP_UPSTREAM_FAILED"}) from exc
    except BaseException:
        if calls is not None:
            calls.mark_unknown(scope, idempotency_key)
        raise
    if result is None:
        if calls is not None:
            calls.mark_unknown(scope, idempotency_key)
        raise HTTPException(status_code=404, detail="Tool not found")
    run = request.app.state.tool_runs.add(user.id, name, arguments, result.result)
    answer = result.model_copy(update={"run_id": run.id})
    if calls is not None:
        calls.complete(scope, idempotency_key, answer)
    return answer


@router.get("/tool-runs")
def list_tool_runs(
    user: CurrentUserDep, request: Request,
    tool: Annotated[str | None, Query(min_length=1, max_length=256,
                                     description="Exact tool name to filter invocation history")] = None,
) -> list[ToolRun]:
    """List owned MCP invocation records, optionally for one exact tool."""
    return request.app.state.tool_runs.list_for(user.id, tool)


class ToolRunMove(BaseModel):
    project_id: str


@router.patch("/tool-runs/{run_id}/project")
async def save_tool_run_to_project(
    run_id: str, payload: ToolRunMove, user: CurrentUserDep,
    conversations: ConversationStoreDep, request: Request,
) -> ToolRun:
    """Associate an owned tool invocation with an accessible project."""
    if not any(project.id == payload.project_id for project in conversations.projects_for(user.id)):
        raise HTTPException(status_code=404, detail="Project not found")
    run = request.app.state.tool_runs.set_project(user.id, run_id, payload.project_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Tool run not found")
    return run


@router.post("/af3/jobs")
async def submit_af3_job(
    payload: Af3JobRequest, user: CurrentUserDep, af3: Af3Dep,
    conversations: ConversationStoreDep, request: Request,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", min_length=1, max_length=128)] = None,
) -> Af3Job:
    """Reserve GPU quota and submit a member's AF3 job."""
    request.app.state.guest_capabilities.require_member(user.id)
    if request.app.state.af3_executor == "disabled":
        raise HTTPException(status_code=503, detail={"code": "AF3_NOT_CONFIGURED"})
    if (request.app.state.settings.agent_runtime == "pi"
            and payload.estimated_gpu_minutes >= request.app.state.settings.af3_approval_threshold):
        raise HTTPException(status_code=403, detail={"code": "APPROVAL_REQUIRED"})
    if payload.run_id and not conversations.owns_run(user.id, payload.run_id):
        raise HTTPException(status_code=404, detail="Run not found")
    try:
        return af3.submit(user.id, payload, idempotency_key)
    except Af3IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail={"code": "AF3_IDEMPOTENCY_CONFLICT"}) from exc
    except GpuQuotaExceeded as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "GPU_DAILY_QUOTA_EXCEEDED", "message": "Daily GPU quota exhausted"},
        ) from exc


@router.get("/af3/jobs/{job_id}")
async def get_af3_job(job_id: str, user: CurrentUserDep, af3: Af3Dep) -> Af3Job:
    """Read an AF3 job belonging to the authenticated user."""
    job = af3.get(user.id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.delete("/af3/jobs/{job_id}")
async def cancel_af3_job(
    job_id: str, user: CurrentUserDep, af3: Af3Dep, conversations: ConversationStoreDep,
) -> Af3Job:
    """Cancel an owned AF3 job and its associated agent run."""
    job = af3.cancel(user.id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.run_id and job.status == "cancelled":
        conversations.cancel_run(user.id, job.run_id)
    return job
