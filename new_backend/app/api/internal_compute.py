"""Private service-authenticated computation worker API; separate from user JWTs."""

import json
import secrets
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request

from app.api.compute import compute_error
from app.contracts.compute import (
    ComputeClaimRequest,
    ComputeHeartbeatRequest,
    ComputeResultRequest,
    ExecutionGrant,
    GrantUpdate,
    UsageReceipt,
)

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
