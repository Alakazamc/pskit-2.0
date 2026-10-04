"""Public computation APIs: JWT ownership, version policy and bounded admission."""

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request

from app.api.auth import CurrentUserDep
from app.contracts.compute import CapabilityVersion, ComputeJob, ComputeJobRequest, ComputeUsage

router = APIRouter(prefix="/api/v1/compute", tags=["compute"])


def jobs_for(request):
    jobs = getattr(request.app.state, "compute_jobs", None)
    if jobs is None:
        raise HTTPException(503, detail={"code": "COMPUTE_NOT_CONFIGURED"})
    return jobs


def compute_error(exc):
    code = str(exc)
    return HTTPException(404 if isinstance(exc, LookupError) else
                         429 if code.endswith("QUOTA_EXCEEDED") else
                         422 if code.startswith("INVALID") else 409, detail={"code": code})


@router.get("/capabilities")
async def capabilities(user: CurrentUserDep, request: Request) -> list[CapabilityVersion]:
    return jobs_for(request).catalog.for_user(user.id)


@router.post("/jobs")
async def submit(payload: ComputeJobRequest, user: CurrentUserDep, request: Request,
                 key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)]
                 ) -> ComputeJob:
    jobs = jobs_for(request)
    policy = getattr(request.app.state, "guest_capabilities", None)
    if policy is not None:
        policy.require_member(user.id)
    try:
        return jobs.submit(user.id, payload, key)
    except (ValueError, LookupError) as exc:
        raise compute_error(exc) from exc


@router.get("/jobs/{job_id}")
async def get(job_id: str, user: CurrentUserDep, request: Request) -> ComputeJob:
    job = jobs_for(request).get(user.id, job_id)
    if job is None:
        raise HTTPException(404, detail={"code": "JOB_NOT_FOUND"})
    return job


@router.post("/jobs/{job_id}/cancel")
async def cancel(job_id: str, user: CurrentUserDep, request: Request) -> ComputeJob:
    try:
        return jobs_for(request).cancel(user.id, job_id)
    except (ValueError, LookupError) as exc:
        raise compute_error(exc) from exc


@router.get("/usage")
async def usage(user: CurrentUserDep, request: Request) -> ComputeUsage:
    jobs = jobs_for(request)
    if jobs.ledger is None:
        raise HTTPException(503, detail={"code": "COMPUTE_LEDGER_UNAVAILABLE"})
    return jobs.ledger.usage_for(user.id)
