"""Authenticated read-only proof that the actual backend enforces durable admission."""

import secrets

from fastapi import APIRouter, Header, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.adapters.disabled import DisabledMcp
from app.adapters.live.limited_mcp import LimitedMcp
from app.domain.release_admission import AdmissionClosed
from app.services.pdf_processing import PdfProcessingPool
from app.services.qualification_admission import AdmissionMcpQualificationExecutor

router = APIRouter(prefix="/internal", tags=["internal"], include_in_schema=False)


@router.get("/release-admission")
async def release_admission_observation(request: Request, challenge: str,
                                        authorization: str | None = Header(default=None)):
    settings = request.app.state.settings
    gate = getattr(request.app.state, "admission_gate", None)
    if (not settings.release_admission_enabled or gate is None
            or not settings.release_admission_key
            or not secrets.compare_digest(authorization or "", "Bearer " + settings.release_admission_key)):
        raise HTTPException(404)
    # A readiness flag alone is insufficient: every enabled capability must
    # share this exact gate object. Disabled capabilities have no admission path.
    for name in ("conversations", "compute_jobs", "compute_leases", "workspace_attempts", "workspace_sandbox_store",
                 "mcp_tool_calls", "mcp_capacity", "pdf_capacity"):
        component = getattr(request.app.state, name, None)
        if component is not None and getattr(component, "admission_gate", None) is not gate:
            raise HTTPException(503, detail={"code": "ADMISSION_ENFORCEMENT_UNVERIFIED"})
    mcp = getattr(request.app.state, "mcp", None)
    mcp_capacity = getattr(request.app.state, "mcp_capacity", None)
    pdf = getattr(request.app.state, "pdf_processor", None)
    pdf_capacity = getattr(request.app.state, "pdf_capacity", None)
    qualification = getattr(request.app.state, "mcp_qualification", None)
    executor = getattr(qualification, "executor", None)
    if ((not isinstance(mcp, DisabledMcp) and (
            not isinstance(mcp, LimitedMcp) or mcp_capacity is None or mcp.shared is not mcp_capacity))
            or not isinstance(pdf, PdfProcessingPool) or pdf_capacity is None or pdf._shared is not pdf_capacity):
        raise HTTPException(503, detail={"code": "ADMISSION_ENFORCEMENT_UNVERIFIED"})
    if (qualification is not None and (
            not isinstance(executor, AdmissionMcpQualificationExecutor)
            or mcp_capacity is None
            or executor.calls is not request.app.state.mcp_tool_calls
            or executor.capacity.shared is not mcp_capacity)):
        raise HTTPException(503, detail={"code": "ADMISSION_ENFORCEMENT_UNVERIFIED"})
    try:
        return await run_in_threadpool(gate.observe, challenge)
    except (AdmissionClosed, ValueError):
        raise HTTPException(503, detail={"code": "ADMISSION_ENFORCEMENT_UNAVAILABLE"}) from None
