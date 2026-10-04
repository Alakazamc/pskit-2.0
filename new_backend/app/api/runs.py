import asyncio
from time import monotonic
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.api.auth import CurrentUserDep
from app.contracts.conversation import ApprovalDecisionRequest, ApprovalDecisionResponse, RunStatus
from app.domain.conversation import ConversationStore
from app.domain.quota import GpuQuotaExceeded

router = APIRouter(prefix="/api/v1", tags=["runs"])
SSE_BATCH_SIZE = 128


async def get_conversation_store(request: Request) -> ConversationStore:
    """Provide the configured conversation store."""
    return request.app.state.conversations


ConversationStoreDep = Annotated[ConversationStore, Depends(get_conversation_store)]


@router.post("/runs/{run_id}/approvals/{approval_id}")
async def decide_approval(
    run_id: str, approval_id: str, payload: ApprovalDecisionRequest,
    user: CurrentUserDep, conversations: ConversationStoreDep, request: Request,
) -> ApprovalDecisionResponse:
    """Accept or reject a pending agent action for an owned run."""
    if request.app.state.settings.agent_runtime != "pi":
        raise HTTPException(status_code=404, detail="Approval not found")
    request.app.state.guest_capabilities.require_member(user.id)
    try:
        result = conversations.decide_af3_approval(user.id, run_id, approval_id, payload.decision)
    except GpuQuotaExceeded as exc:
        raise HTTPException(status_code=409, detail={"code": "GPU_DAILY_QUOTA_EXCEEDED"}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail={"code": "APPROVAL_CONFLICT"}) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="Approval not found")
    if result.status == "rejected":
        request.app.state.agent_service.cancel_run(run_id)
    return result


@router.get("/runs/{run_id}")
async def get_run_status(
    run_id: str, user: CurrentUserDep, conversations: ConversationStoreDep
) -> RunStatus:
    """Read the status of an owned agent run."""
    status = conversations.run_status_for(user.id, run_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return status


@router.delete("/runs/{run_id}")
async def cancel_run(
    run_id: str, user: CurrentUserDep, conversations: ConversationStoreDep, request: Request,
) -> RunStatus:
    """Cancel an owned run and request cancellation of its background jobs."""
    status = conversations.run_status_for(user.id, run_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if status.status not in {"completed", "failed", "cancelled"}:
        service = request.app.state.agent_service
        if service:
            service.cancel_run(run_id)
        af3 = request.app.state.af3
        for job_id in af3.active_job_ids_for_run(user.id, run_id):
            af3.cancel(user.id, job_id)
        compute = getattr(request.app.state, "compute_jobs", None)
        if compute is not None:
            for job_id in compute.active_for_run(user.id, run_id):
                compute.cancel(user.id, job_id)
        status = conversations.cancel_run(user.id, run_id)
    return status


@router.get("/runs/{run_id}/events")
async def stream_events(
    run_id: str, user: CurrentUserDep, conversations: ConversationStoreDep,
    after: str | None = None, follow: bool = True,
) -> StreamingResponse:
    """Replay and optionally follow SSE events for an owned run."""
    if after is not None:
        try:
            int(after)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid event cursor") from exc
    if conversations.events_for(user.id, run_id, after, limit=1) is None:
        raise HTTPException(status_code=404, detail="Run not found")

    async def chunks():
        """Yield cursor-based SSE events and periodic heartbeat comments."""
        cursor = after
        heartbeat_at = monotonic()
        while True:
            pending = conversations.events_for(
                user.id, run_id, cursor, limit=SSE_BATCH_SIZE,
            ) or []
            for event in pending:
                yield f"id: {event.id}\nevent: {event.type}\ndata: {event.model_dump_json()}\n\n"
                cursor = event.id
            if len(pending) == SSE_BATCH_SIZE:
                await asyncio.sleep(0)
                continue
            if not follow:
                return
            status = conversations.run_status_for(user.id, run_id)
            if status is None or status.status in {"completed", "failed", "cancelled"}:
                return
            if monotonic() - heartbeat_at >= 15:
                yield ": heartbeat\n\n"
                heartbeat_at = monotonic()
            await asyncio.sleep(0.2)

    return StreamingResponse(
        chunks(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )
