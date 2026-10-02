import json
import secrets
import uuid
from pathlib import Path
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from app.adapters.live.limited_mcp import McpCapacityExceeded
from app.api.mcp_validation import validate_mcp_arguments
from app.contracts.capabilities import (
    Af3ComputeClaim, Af3FoldInput, Af3Job, ComputeWorkerResources, McpInvokeResult,
)
from app.contracts.catalog import ArtifactRef
from app.contracts.conversation import ApprovalRef, PlanSnapshot
from app.domain.persistent_conversation import ComputeLeaseConflict, GpuReconciliationConflict
from app.domain.quota import GpuQuotaExceeded
from app.domain.quota import TokenQuotaExceeded
from app.ports.providers import ProviderUnavailable

router = APIRouter(prefix="/internal", tags=["internal"], include_in_schema=False)
MAX_ARTIFACT_BYTES = 20 * 1024 * 1024


class InternalAf3Request(BaseModel):
    run_id: str
    tool_call_id: str = Field(min_length=1)
    estimated_gpu_minutes: int = Field(default=20, ge=1, le=60)
    fold_input: Af3FoldInput | None = None


class InternalMcpRequest(BaseModel):
    run_id: str
    tool_call_id: str = Field(min_length=1, max_length=256)
    arguments: dict


class ComputeArtifact(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: str = Field(min_length=1)


class Af3ResultRequest(BaseModel):
    status: Literal["completed", "failed"]
    actual_gpu_minutes: int = Field(ge=0, le=1440)
    artifacts: list[ComputeArtifact] = Field(default_factory=list)
    attempt: int | None = Field(default=None, ge=1)
    lease_token: str | None = None
    simulation: bool = False


class ComputeClaimRequest(BaseModel):
    worker_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,80}$")
    resources: ComputeWorkerResources
    lease_seconds: int = Field(default=60, ge=1, le=300)
    max_jobs: int = Field(default=1, ge=1, le=8)


class ComputeHeartbeatRequest(BaseModel):
    worker_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,80}$")
    lease_seconds: int = Field(default=60, ge=1, le=300)
    lease_token: str = Field(min_length=1)


class ComputeProgressRequest(BaseModel):
    worker_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,80}$")
    lease_token: str = Field(min_length=1)
    attempt: int = Field(ge=1)
    progress: int = Field(ge=0, le=99)


class ModelCallPreflightRequest(BaseModel):
    run_id: str
    call_id: str = Field(min_length=1, max_length=128)
    prompt_bytes: int = Field(ge=1, le=10_000_000)
    requested_output_tokens: int = Field(ge=1, le=1_000_000)


class ModelCallPreflightResponse(BaseModel):
    max_output_tokens: int


@router.post("/model/calls/preflight")
async def preflight_model_call(
    payload: ModelCallPreflightRequest, request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> ModelCallPreflightResponse:
    """Reserve output token capacity for a trusted Pi model call."""
    service = request.app.state.agent_service
    if service is None:
        raise HTTPException(status_code=404, detail="Not found")
    token = authorization.removeprefix("Bearer ") if authorization else ""
    if not service.verify_tool_token(payload.run_id, token):
        raise HTTPException(status_code=401, detail="Invalid agent tool token")
    store = request.app.state.conversations
    owner = store.owner_for_run(payload.run_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="Run not found")
    try:
        maximum = store.reserve_model_call(
            owner, payload.run_id, payload.call_id, payload.prompt_bytes,
            payload.requested_output_tokens,
        )
    except TokenQuotaExceeded as exc:
        raise HTTPException(status_code=429, detail={"code": "TOKEN_QUOTA_EXCEEDED"}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": "MODEL_CALL_CONFLICT"}) from exc
    return ModelCallPreflightResponse(max_output_tokens=maximum)


@router.post("/model/v1/chat/completions")
async def proxy_model_call(
    request: Request, authorization: Annotated[str | None, Header()] = None,
) -> Response:
    """Gate Pi's OpenAI-compatible requests before forwarding to the configured model service."""
    service = request.app.state.agent_service
    settings = request.app.state.settings
    base_url = settings.model_gateway_base_url or settings.new_api_base_url
    if service is None or not base_url or not service.model_gateway_api_key:
        raise HTTPException(status_code=404, detail="Model gateway unavailable")
    bearer = authorization.removeprefix("Bearer ") if authorization else ""
    run_id, separator, token = bearer.partition(".")
    if not separator or not service.verify_tool_token(run_id, token):
        raise HTTPException(status_code=401, detail="Invalid model proxy token")
    store = request.app.state.conversations
    owner = store.owner_for_run(run_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="Run not found")
    raw = await request.body()
    if not raw or len(raw) > 10_000_000:
        raise HTTPException(status_code=413, detail="Model request is too large")
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid model request") from exc
    expected_model = settings.model_gateway_model or settings.new_api_model
    if not isinstance(payload, dict) or payload.get("model") != expected_model:
        raise HTTPException(status_code=400, detail="Model is not configured")
    field = "max_completion_tokens" if "max_completion_tokens" in payload else "max_tokens"
    requested = payload.get(field, 4096)
    if type(requested) is not int or requested < 1:
        raise HTTPException(status_code=400, detail="Invalid model output limit")
    call_id = uuid.uuid4().hex
    try:
        maximum = store.reserve_model_call(owner, run_id, call_id, len(raw), requested)
    except TokenQuotaExceeded:
        return Response(
            content=json.dumps({"error": {"message": "PSKIT_TOKEN_QUOTA_EXCEEDED",
                                          "code": "PSKIT_TOKEN_QUOTA_EXCEEDED"}}),
            status_code=402, media_type="application/json",
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": "RUN_NOT_ACTIVE"}) from exc
    payload[field] = maximum
    url = f"{base_url.rstrip('/').removesuffix('/v1')}/v1/chat/completions"
    client = getattr(request.app.state, "model_gateway_http_client", None)
    owned_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0))
    try:
        upstream = await client.send(client.build_request(
            "POST", url, json=payload,
            headers={"Authorization": f"Bearer {service.model_gateway_api_key}",
                     "Accept": "text/event-stream" if payload.get("stream") else "application/json"},
        ), stream=True)
    except httpx.HTTPError as exc:
        if owned_client:
            await client.aclose()
        raise HTTPException(status_code=502, detail="Model gateway request failed") from exc

    if upstream.status_code in {400, 401, 403, 404, 422, 429}:
        store.release_model_call(owner, run_id, call_id)
    if upstream.status_code != 200:
        body = await upstream.aread()
        media_type = upstream.headers.get("content-type", "application/json")
        await upstream.aclose()
        if owned_client:
            await client.aclose()
        return Response(body, status_code=upstream.status_code, media_type=media_type)

    async def stream():
        """Relay an upstream model stream and close its HTTP resources."""
        try:
            if upstream.is_stream_consumed:
                yield upstream.content
            else:
                async for chunk in upstream.aiter_raw():
                    yield chunk
        finally:
            await upstream.aclose()
            if owned_client:
                await client.aclose()

    return StreamingResponse(
        stream(), status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "text/event-stream"),
    )


@router.post("/runs/{run_id}/plan")
async def update_internal_plan(
    run_id: str, payload: PlanSnapshot, request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> PlanSnapshot:
    """Record a plan update from an authenticated agent run."""
    service = request.app.state.agent_service
    if service is None:
        raise HTTPException(status_code=404, detail="Not found")
    token = authorization.removeprefix("Bearer ") if authorization else ""
    if not service.verify_tool_token(run_id, token):
        raise HTTPException(status_code=401, detail="Invalid agent tool token")
    store = request.app.state.conversations
    owner = store.owner_for_run(run_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="Run not found")
    try:
        store.record_plan(owner, run_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": "RUN_NOT_ACTIVE"}) from exc
    return payload


@router.post("/compute/af3/jobs/claim")
async def claim_compute_af3_jobs(
    payload: ComputeClaimRequest, request: Request,
    compute_key: Annotated[str | None, Header(alias="X-Compute-Key")] = None,
) -> list[Af3ComputeClaim]:
    """Lease compatible queued AF3 jobs to a compute worker."""
    settings = request.app.state.settings
    if request.app.state.af3_executor != "callback" or not settings.compute_callback_key:
        raise HTTPException(status_code=404, detail="Not found")
    if not compute_key or not secrets.compare_digest(compute_key, settings.compute_callback_key):
        raise HTTPException(status_code=404, detail="Not found")
    return request.app.state.conversations.claim_compute_jobs(
        payload.worker_id, payload.resources,
        lease_seconds=payload.lease_seconds, limit=payload.max_jobs,
        max_execution_seconds=settings.af3_execution_timeout_seconds,
        require_explicit_memory=settings.mode == "live",
    )


@router.post("/compute/af3/jobs/{job_id}/heartbeat")
async def heartbeat_compute_af3_job(
    job_id: str, payload: ComputeHeartbeatRequest, request: Request,
    compute_key: Annotated[str | None, Header(alias="X-Compute-Key")] = None,
) -> Af3Job:
    """Renew a compute worker's active AF3 job lease."""
    settings = request.app.state.settings
    if request.app.state.af3_executor != "callback" or not settings.compute_callback_key:
        raise HTTPException(status_code=404, detail="Not found")
    if not compute_key or not secrets.compare_digest(compute_key, settings.compute_callback_key):
        raise HTTPException(status_code=404, detail="Not found")
    if not request.app.state.conversations.renew_compute_lease(
        job_id, payload.worker_id, payload.lease_token, lease_seconds=payload.lease_seconds,
        max_execution_seconds=settings.af3_execution_timeout_seconds,
    ):
        raise HTTPException(status_code=409, detail={"code": "COMPUTE_LEASE_NOT_OWNED"})
    return request.app.state.conversations.af3_job_for_compute(job_id)


@router.get("/compute/af3/jobs/owned")
async def owned_compute_af3_jobs(
    worker_id: str, request: Request,
    compute_key: Annotated[str | None, Header(alias="X-Compute-Key")] = None,
) -> list[Af3ComputeClaim]:
    """Recover this worker's active claims after a disconnected claim response."""
    settings = request.app.state.settings
    if request.app.state.af3_executor != "callback" or not settings.compute_callback_key:
        raise HTTPException(status_code=404, detail="Not found")
    if not compute_key or not secrets.compare_digest(compute_key, settings.compute_callback_key):
        raise HTTPException(status_code=404, detail="Not found")
    if not worker_id or len(worker_id) > 80 or any(
        char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-"
        for char in worker_id
    ):
        raise HTTPException(status_code=422, detail="Invalid worker ID")
    return request.app.state.conversations.owned_compute_jobs(worker_id)


@router.get("/compute/af3/jobs/{job_id}")
async def get_compute_af3_job(
    job_id: str, request: Request,
    compute_key: Annotated[str | None, Header(alias="X-Compute-Key")] = None,
) -> Af3Job:
    """Read a job through the authenticated compute worker API."""
    settings = request.app.state.settings
    if request.app.state.af3_executor != "callback" or not settings.compute_callback_key:
        raise HTTPException(status_code=404, detail="Not found")
    if not compute_key or not secrets.compare_digest(compute_key, settings.compute_callback_key):
        raise HTTPException(status_code=404, detail="Not found")
    job = request.app.state.conversations.af3_job_for_compute(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/compute/af3/jobs/{job_id}/progress")
async def progress_compute_af3_job(
    job_id: str, payload: ComputeProgressRequest, request: Request,
    compute_key: Annotated[str | None, Header(alias="X-Compute-Key")] = None,
) -> Af3Job:
    """Persist a compute worker's monotonic progress before acknowledging it."""
    settings = request.app.state.settings
    if request.app.state.af3_executor != "callback" or not settings.compute_callback_key:
        raise HTTPException(status_code=404, detail="Not found")
    if not compute_key or not secrets.compare_digest(compute_key, settings.compute_callback_key):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        job = request.app.state.conversations.update_compute_progress(
            job_id, payload.worker_id, payload.lease_token, payload.attempt,
            payload.progress, max_execution_seconds=settings.af3_execution_timeout_seconds,
        )
    except ComputeLeaseConflict as exc:
        raise HTTPException(status_code=409, detail={"code": "COMPUTE_LEASE_NOT_OWNED"}) from exc
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.put("/af3/jobs/{job_id}/artifacts/{artifact_id}")
async def upload_af3_artifact(
    job_id: str, artifact_id: str, name: str, kind: str, request: Request,
    attempt: int | None = None,
    compute_key: Annotated[str | None, Header(alias="X-Compute-Key")] = None,
    compute_lease: Annotated[str | None, Header(alias="X-Compute-Lease")] = None,
) -> ArtifactRef:
    """Store a bounded AF3 artifact under the active compute lease."""
    settings = request.app.state.settings
    if request.app.state.af3_executor != "callback" or not settings.compute_callback_key:
        raise HTTPException(status_code=404, detail="Not found")
    if not compute_key or not secrets.compare_digest(compute_key, settings.compute_callback_key):
        raise HTTPException(status_code=404, detail="Not found")
    safe_name = Path(name).name
    if safe_name in {"", ".", ".."} or not kind or not artifact_id:
        raise HTTPException(status_code=422, detail={"code": "INVALID_ARTIFACT"})
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_ARTIFACT_BYTES:
            raise HTTPException(status_code=413, detail={"code": "ARTIFACT_TOO_LARGE"})
        chunks.append(chunk)
    try:
        artifact = request.app.state.conversations.save_artifact_blob(
            job_id, artifact_id, safe_name, kind, b"".join(chunks),
            attempt=attempt, lease_token=compute_lease,
            max_execution_seconds=settings.af3_execution_timeout_seconds,
        )
    except ComputeLeaseConflict as exc:
        raise HTTPException(status_code=409, detail={"code": "COMPUTE_LEASE_NOT_OWNED"}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": "ARTIFACT_CONFLICT"}) from exc
    if artifact is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return artifact


@router.post("/af3/jobs/{job_id}/result")
async def receive_af3_result(
    job_id: str, payload: Af3ResultRequest, request: Request,
    compute_key: Annotated[str | None, Header(alias="X-Compute-Key")] = None,
) -> Af3Job:
    """Settle an AF3 job and its GPU usage from a compute callback."""
    settings = request.app.state.settings
    if request.app.state.af3_executor != "callback" or not settings.compute_callback_key:
        raise HTTPException(status_code=404, detail="Not found")
    if not compute_key or not secrets.compare_digest(compute_key, settings.compute_callback_key):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        job = request.app.state.conversations.settle_af3_job(
            job_id, payload.status, payload.actual_gpu_minutes,
            [artifact.model_dump() for artifact in payload.artifacts],
            attempt=payload.attempt, lease_token=payload.lease_token,
            simulation=payload.simulation,
            max_execution_seconds=settings.af3_execution_timeout_seconds,
        )
    except ComputeLeaseConflict as exc:
        raise HTTPException(status_code=409, detail={"code": "COMPUTE_LEASE_NOT_OWNED"}) from exc
    except GpuReconciliationConflict as exc:
        raise HTTPException(status_code=409, detail={"code": "GPU_RECONCILIATION_CONFLICT"}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": "ARTIFACT_CONFLICT"}) from exc
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post("/mcp/tools/{name}/invoke")
async def invoke_internal_mcp(
    name: str,
    payload: InternalMcpRequest,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> McpInvokeResult:
    """Invoke an authorized MCP tool for an active Pi agent run."""
    if request.app.state.mcp_executor == "disabled":
        raise HTTPException(status_code=503, detail={"code": "MCP_NOT_CONFIGURED"})
    if request.app.state.mcp_unavailable:
        raise HTTPException(status_code=503, detail={"code": "MCP_UPSTREAM_UNAVAILABLE"})
    service = request.app.state.agent_service
    if service is None:
        raise HTTPException(status_code=404, detail="Not found")
    token = authorization.removeprefix("Bearer ") if authorization else ""
    if not service.verify_tool_token(payload.run_id, token):
        raise HTTPException(status_code=401, detail="Invalid agent tool token")
    store = request.app.state.conversations
    owner = store.owner_for_run(payload.run_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if store.run_status_for(owner, payload.run_id).status != "running":
        raise HTTPException(status_code=409, detail={"code": "RUN_NOT_ACTIVE"})
    if name not in store.run_context(payload.run_id).get("allowed_tools", []):
        raise HTTPException(status_code=403, detail={"code": "TOOL_NOT_ALLOWED"})
    if not request.app.state.catalog.tool_allowed_for(owner, name):
        raise HTTPException(status_code=403, detail={"code": "TOOL_NOT_ALLOWED"})
    validate_mcp_arguments(request.app.state.mcp, name, payload.arguments)
    calls = request.app.state.mcp_tool_calls
    try:
        state, cached = calls.claim(payload.run_id, payload.tool_call_id, name, payload.arguments)
    except ProviderUnavailable as exc:
        raise HTTPException(status_code=503, detail={"code": "MCP_LEDGER_UNAVAILABLE"}) from exc
    if state == "conflict":
        raise HTTPException(status_code=409, detail={"code": "MCP_TOOL_CALL_CONFLICT"})
    if state == "unknown":
        raise HTTPException(status_code=409, detail={"code": "MCP_TOOL_CALL_OUTCOME_UNKNOWN"})
    if cached is not None:
        return cached
    try:
        result = await request.app.state.mcp.invoke(name, payload.arguments)
    except McpCapacityExceeded as exc:
        calls.release_before_invoke(payload.run_id, payload.tool_call_id)
        raise HTTPException(status_code=429, detail={"code": "MCP_CAPACITY_EXCEEDED"}) from exc
    except ProviderUnavailable as exc:
        calls.mark_unknown(payload.run_id, payload.tool_call_id)
        raise HTTPException(status_code=502, detail={"code": "MCP_UPSTREAM_FAILED"}) from exc
    except BaseException:
        calls.mark_unknown(payload.run_id, payload.tool_call_id)
        raise
    if result is None:
        calls.mark_unknown(payload.run_id, payload.tool_call_id)
        raise HTTPException(status_code=404, detail="Tool not found")
    calls.complete(payload.run_id, payload.tool_call_id, result)
    return result


@router.post("/af3/jobs")
async def submit_internal_af3(
    payload: InternalAf3Request,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Af3Job | ApprovalRef:
    """Submit an agent's AF3 job or request required approval."""
    if request.app.state.af3_executor == "disabled":
        raise HTTPException(status_code=503, detail={"code": "AF3_NOT_CONFIGURED"})
    service = request.app.state.agent_service
    if service is None:
        raise HTTPException(status_code=404, detail="Not found")
    token = authorization.removeprefix("Bearer ") if authorization else ""
    if not service.verify_tool_token(payload.run_id, token):
        raise HTTPException(status_code=401, detail="Invalid agent tool token")
    owner = request.app.state.conversations.owner_for_run(payload.run_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="Run not found")
    request.app.state.guest_capabilities.require_member(owner)
    if request.app.state.conversations.run_status_for(owner, payload.run_id).status != "running":
        raise HTTPException(status_code=409, detail={"code": "RUN_NOT_ACTIVE"})
    try:
        if payload.estimated_gpu_minutes >= request.app.state.settings.af3_approval_threshold:
            return request.app.state.conversations.request_af3_approval(
                owner, payload.run_id, payload.tool_call_id, payload.estimated_gpu_minutes,
                payload.fold_input,
            )
        return request.app.state.conversations.create_af3_job(
            owner, payload.estimated_gpu_minutes, payload.run_id, payload.tool_call_id,
            payload.fold_input,
            simulation=request.app.state.af3_executor == "mock",
        )
    except GpuQuotaExceeded as exc:
        raise HTTPException(
            status_code=409, detail={"code": "GPU_DAILY_QUOTA_EXCEEDED"}
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409, detail={"code": "AF3_ARGUMENT_CONFLICT"}
        ) from exc


@router.get("/af3/jobs/{job_id}")
async def get_internal_af3(
    job_id: str,
    run_id: str,
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Af3Job:
    """Read an AF3 job scoped to the requesting agent run."""
    service = request.app.state.agent_service
    if service is None:
        raise HTTPException(status_code=404, detail="Not found")
    token = authorization.removeprefix("Bearer ") if authorization else ""
    if not service.verify_tool_token(run_id, token):
        raise HTTPException(status_code=401, detail="Invalid agent tool token")
    store = request.app.state.conversations
    owner = store.owner_for_run(run_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="Run not found")
    request.app.state.guest_capabilities.require_member(owner)
    job = store.get_af3_job(owner, job_id)
    if job is None or job.run_id != run_id:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
