"""Private service-authenticated computation worker API; separate from user JWTs."""

import json
import secrets
from types import SimpleNamespace
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import ValidationError

from app.api.compute import compute_error, jobs_for
from app.contracts.catalog import ArtifactRef
from app.contracts.compute import (
    ComputeClaimRequest,
    ComputeHeartbeatRequest,
    ComputeJob,
    ComputeJobRequest,
    ComputeResultRequest,
    ComputeResumeContext,
    ExecutionGrant,
    GrantUpdate,
    InternalComputeSubmit,
    UsageReceipt,
)
from app.domain.compute.artifacts import MAX_ARTIFACT_BYTES, ComputeArtifacts

router = APIRouter(prefix="/internal/compute", tags=["compute-worker"])


def authorized_service(request, service_id, key):
    leases = getattr(request.app.state, "compute_leases", None)
    keys = json.loads(request.app.state.settings.compute_service_keys_json)
    expected = keys.get(service_id, "")
    if leases is None or not expected or not key or not secrets.compare_digest(expected, key):
        raise HTTPException(404, detail="Not found")
    return leases


@router.post("/jobs/claim")
def claim(payload: ComputeClaimRequest, request: Request,
          key: Annotated[str | None, Header(alias="X-Compute-Key")] = None) -> ExecutionGrant | None:
    try:
        return authorized_service(request, payload.service_id, key).claim(payload)
    except (ValueError, LookupError) as exc:
        raise compute_error(exc) from exc


@router.post("/jobs/{job_id}/heartbeat")
def heartbeat(job_id: str, payload: ComputeHeartbeatRequest, request: Request,
              service_id: Annotated[str, Header(alias="X-Compute-Service")],
              key: Annotated[str | None, Header(alias="X-Compute-Key")] = None) -> GrantUpdate:
    try:
        return authorized_service(request, service_id, key).heartbeat(service_id, job_id, payload)
    except (ValueError, LookupError) as exc:
        raise compute_error(exc) from exc


@router.post("/jobs/{job_id}/result")
def result(job_id: str, payload: ComputeResultRequest, request: Request,
           service_id: Annotated[str, Header(alias="X-Compute-Service")],
           key: Annotated[str | None, Header(alias="X-Compute-Key")] = None) -> UsageReceipt:
    try:
        return authorized_service(request, service_id, key).complete(service_id, job_id, payload)
    except (ValueError, LookupError) as exc:
        raise compute_error(exc) from exc


@router.get("/jobs/{job_id}/artifacts/{artifact_id}")
@router.put("/jobs/{job_id}/artifacts/{artifact_id}")
async def upload_artifact(
    job_id: str, artifact_id: str, request: Request,
    service_id: Annotated[str, Header(alias="X-Compute-Service")],
    worker_id: Annotated[str, Header(alias="X-Compute-Worker", min_length=1, max_length=120)],
    attempt: Annotated[int, Header(alias="X-Compute-Attempt", ge=1)],
    fencing_token: Annotated[str, Header(alias="X-Compute-Fence", min_length=1, max_length=200)],
    metadata: Annotated[str, Header(alias="X-Compute-Artifact", max_length=4096)],
    key: Annotated[str | None, Header(alias="X-Compute-Key")] = None,
) -> ArtifactRef:
    leases = authorized_service(request, service_id, key)
    try:
        artifact = ArtifactRef.model_validate_json(metadata)
        if artifact.id != artifact_id:
            raise ValueError("INVALID_ARTIFACT_ID")
        ComputeArtifacts.validate_metadata(artifact)
        identity = SimpleNamespace(worker_id=worker_id, attempt=attempt, fencing_token=fencing_token)
        if request.method == "GET":
            return leases.artifact_uploaded(service_id, job_id, identity, artifact)
        raw = bytearray()
        async for chunk in request.stream():
            if len(raw) + len(chunk) > min(artifact.size, MAX_ARTIFACT_BYTES):
                raise ValueError("INVALID_ARTIFACT_CONTENT")
            raw.extend(chunk)
        return leases.put_artifact(service_id, job_id, identity, artifact, bytes(raw))
    except ValidationError as exc:
        raise HTTPException(422, detail={"code": "INVALID_ARTIFACT_METADATA"}) from exc
    except (ValueError, LookupError) as exc:
        raise compute_error(exc) from exc


def agent_owner(request, run_id, authorization):
    service = request.app.state.agent_service
    token = authorization.removeprefix("Bearer ") if authorization else ""
    if service is None or not service.verify_tool_token(run_id, token):
        raise HTTPException(401, detail="Invalid agent tool token")
    store = request.app.state.conversations
    owner = store.owner_for_run(run_id)
    if owner is None:
        raise HTTPException(404, detail="Not found")
    if store.run_status_for(owner, run_id).status != "running":
        raise HTTPException(409, detail={"code": "RUN_NOT_ACTIVE"})
    policy = getattr(request.app.state, "guest_capabilities", None)
    if policy is not None:
        policy.require_member(owner)
    return owner


@router.post("/jobs")
def submit_agent_job(payload: InternalComputeSubmit, request: Request,
    authorization: Annotated[str | None, Header()] = None) -> ComputeJob:
    owner = agent_owner(request, payload.run_id, authorization)
    jobs = jobs_for(request)
    try:
        return jobs.submit(owner, ComputeJobRequest.model_validate(
            payload.model_dump(exclude={"run_id", "tool_call_id"})),
            f"agent:{payload.run_id}:{payload.tool_call_id}",
            run_id=payload.run_id, tool_call_id=payload.tool_call_id)
    except (ValueError, LookupError) as exc:
        raise compute_error(exc) from exc


@router.get("/jobs/{job_id}")
def get_agent_result(job_id: str, run_id: str, request: Request,
    authorization: Annotated[str | None, Header()] = None) -> ComputeResumeContext:
    owner = agent_owner(request, run_id, authorization)
    jobs = jobs_for(request)
    job = jobs.get(owner, job_id)
    if job is None or job.run_id != run_id:
        raise HTTPException(404, detail="Not found")
    with jobs.database.connection() as connection:
        ids = connection.execute("SELECT j.id FROM agent_jobs j JOIN compute_job_data d ON d.job_id=j.id "
            "WHERE j.run_id=%s AND j.user_id=%s AND j.status IN ('completed','failed','cancelled') "
            "ORDER BY j.ordinal", (run_id, owner)).fetchall()
        related = [jobs.get(owner, item[0], connection=connection) for item in ids]
    return ComputeResumeContext(job=job, related=related)
