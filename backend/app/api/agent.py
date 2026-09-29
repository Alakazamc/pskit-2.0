import asyncio
import hashlib
import inspect
import json
from collections.abc import Mapping
from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.agent.execution import (
    AgentTurnRequest,
    is_agent_turn_scheduled,
    stream_agent_turn_events,
)
from app.agent.orchestrator import public_agent_event
from app.config import get_settings
from app.harness.migration_bridge import (
    DISPATCH_METADATA_KEY,
    load_dispatch_summary,
    new_agent_dispatch_summary,
)
from app.db.models import (
    AgentMessage,
    AgentSession,
    AgentTurn,
    User,
    ensure_utc,
    now_utc,
)
from app.db.locks import acquire_transaction_lock
from app.db.session import SessionLocal, get_db
from app.research.context import resolve_session_research_context
from app.schemas.agent import (
    ActiveAgentTurnResponse,
    AgentMessageRequest,
    AgentMessageResponse,
    AgentSessionResponse,
    CreateAgentSessionRequest,
)


router = APIRouter(prefix="/api/agent", tags=["agent"])


class AgentTurnQuotaExceeded(ValueError):
    def __init__(self, error_type: str, message: str, limit: int):
        super().__init__(message)
        self.error_type = error_type
        self.limit = limit


def _enforce_agent_turn_quotas(db: Session, user: User) -> None:
    settings = get_settings()
    active_statuses = ("queued", "running")
    global_active = db.scalar(
        select(func.count())
        .select_from(AgentTurn)
        .where(AgentTurn.status.in_(active_statuses))
    ) or 0
    if global_active >= settings.max_global_active_agent_turns:
        raise AgentTurnQuotaExceeded(
            "agent_turn_global_limit",
            "Agent 任务队列已满，请稍后重试。",
            settings.max_global_active_agent_turns,
        )
    user_active = db.scalar(
        select(func.count())
        .select_from(AgentTurn)
        .where(
            AgentTurn.user_id == user.id,
            AgentTurn.status.in_(active_statuses),
        )
    ) or 0
    if user_active >= settings.max_active_agent_turns_per_user:
        raise AgentTurnQuotaExceeded(
            "agent_turn_user_limit",
            "当前账号等待或运行中的 Agent 任务过多，请等待现有任务完成。",
            settings.max_active_agent_turns_per_user,
        )


def agent_stream_response(stream) -> StreamingResponse:
    """统一声明服务端心跳周期，让前端空闲门与可配置后端保持一致。"""

    heartbeat_seconds = float(get_settings().agent_sse_heartbeat_seconds)
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-PSKit-SSE-Heartbeat-Seconds": f"{heartbeat_seconds:g}",
        },
    )


def serialize_session(session: AgentSession) -> AgentSessionResponse:
    return AgentSessionResponse(
        id=str(session.id),
        title=session.title,
        created_at=session.created_at.isoformat(),
        updated_at=session.updated_at.isoformat(),
    )


def public_message_metadata(metadata: object) -> dict:
    if not isinstance(metadata, dict):
        return {}
    public = {
        key: value
        for key, value in metadata.items()
        if not str(key).startswith("_internal_")
    }
    events = public.get("events")
    if isinstance(events, list):
        public_events: list[dict] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            projected = public_agent_event(event)
            if projected is not None:
                public_events.append(projected)
        public["events"] = public_events
    return public


def serialize_message(message: AgentMessage) -> AgentMessageResponse:
    return AgentMessageResponse(
        id=str(message.id),
        role=message.role,
        content=message.content,
        created_at=message.created_at.isoformat(),
        metadata=public_message_metadata(message.metadata_json),
    )


def load_owned_session(db: Session, user: User, session_id: UUID) -> AgentSession:
    session = db.get(AgentSession, session_id)
    if not session or session.user_id != user.id or session.archived_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return session


def expire_stale_agent_turn(
    db: Session,
    turn: AgentTurn,
    *,
    commit: bool = True,
) -> bool:
    """以租约或更新时间为条件原子回收失联轮次。"""

    if turn.status != "running":
        return False
    now = now_utc()
    stale_before = now - timedelta(seconds=get_settings().agent_turn_stale_seconds)
    if turn.lease_expires_at is not None:
        stale = ensure_utc(turn.lease_expires_at) < now
        lease_conditions = [
            AgentTurn.lease_token == turn.lease_token,
            AgentTurn.lease_expires_at == turn.lease_expires_at,
        ]
    else:
        stale = ensure_utc(turn.updated_at) < stale_before
        lease_conditions = [AgentTurn.updated_at == turn.updated_at]
    if not stale:
        return False
    expired = db.execute(
        update(AgentTurn)
        .where(
            AgentTurn.id == turn.id,
            AgentTurn.status == turn.status,
            *lease_conditions,
        )
        .values(
            status="failed",
            error_code="agent_turn_stale",
            active_session_key=None,
            lease_token=None,
            lease_owner=None,
            lease_expires_at=None,
            finished_at=now,
            updated_at=now,
        )
    )
    if expired.rowcount == 1:
        if commit:
            db.commit()
        return True
    if commit:
        db.rollback()
    return False


def expire_stale_agent_turns_for_admission(
    db: Session,
    *,
    limit: int = 100,
) -> int:
    """Bound quota cleanup without touching live queued or running work."""

    now = now_utc()
    stale_before = now - timedelta(seconds=get_settings().agent_turn_stale_seconds)
    expired_count = 0
    running_candidates = db.scalars(
        select(AgentTurn)
        .where(AgentTurn.status == "running")
        .order_by(AgentTurn.updated_at.asc())
        .limit(limit)
    ).all()
    for turn in running_candidates:
        if expire_stale_agent_turn(db, turn, commit=False):
            expired_count += 1

    remaining = max(limit - expired_count, 0)
    if remaining == 0:
        return expired_count
    queued_candidates = db.scalars(
        select(AgentTurn)
        .where(
            AgentTurn.status == "queued",
            AgentTurn.updated_at < stale_before,
            AgentTurn.lease_expires_at.is_not(None),
            AgentTurn.lease_expires_at < now,
        )
        .order_by(AgentTurn.updated_at.asc())
        .limit(remaining)
    ).all()
    for turn in queued_candidates:
        # The local registry closes the tiny window between reserving a Future
        # and starting its persistent dispatcher heartbeat. Other processes
        # remain visible through their unexpired database dispatch lease.
        if is_agent_turn_scheduled(turn.id):
            continue
        expired = db.execute(
            update(AgentTurn)
            .where(
                AgentTurn.id == turn.id,
                AgentTurn.status == "queued",
                AgentTurn.updated_at == turn.updated_at,
                AgentTurn.lease_owner == turn.lease_owner,
                AgentTurn.lease_expires_at == turn.lease_expires_at,
            )
            .values(
                status="failed",
                error_code="agent_turn_dispatch_stale",
                active_session_key=None,
                lease_token=None,
                lease_owner=None,
                lease_expires_at=None,
                finished_at=now,
                updated_at=now,
            )
        )
        expired_count += int(expired.rowcount == 1)
    return expired_count


def reserve_queued_turn_dispatch(db: Session, turn: AgentTurn) -> bool:
    """为服务重启后的 queued 轮次原子取得一次重新调度资格。"""

    if turn.status != "queued":
        return False
    now = now_utc()
    if (
        turn.lease_expires_at is not None
        and ensure_utc(turn.lease_expires_at) >= now
    ):
        return False
    conditions = [
        AgentTurn.id == turn.id,
        AgentTurn.status == "queued",
    ]
    if turn.lease_expires_at is None:
        conditions.append(AgentTurn.lease_expires_at.is_(None))
    else:
        conditions.append(AgentTurn.lease_expires_at == turn.lease_expires_at)
    reserved = db.execute(
        update(AgentTurn)
        .where(*conditions)
        .values(
            lease_owner="dispatcher:recovery",
            lease_expires_at=now + timedelta(seconds=30),
            updated_at=now,
        )
    )
    db.commit()
    return reserved.rowcount == 1


def serialize_active_turn(
    db: Session,
    turn: AgentTurn,
) -> ActiveAgentTurnResponse:
    user_message = db.get(AgentMessage, turn.user_message_id)
    return ActiveAgentTurnResponse(
        turn_id=str(turn.id),
        client_turn_id=str(turn.client_turn_id),
        status=turn.status,
        user_content=user_message.content if user_message is not None else "",
        error_code=turn.error_code,
        created_at=turn.created_at.isoformat(),
        updated_at=turn.updated_at.isoformat(),
    )


@router.post("/sessions", response_model=AgentSessionResponse)
def create_session(
    payload: CreateAgentSessionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = AgentSession(user_id=user.id, title=payload.title or "New Chat")
    db.add(session)
    db.commit()
    db.refresh(session)
    return serialize_session(session)


@router.get("/sessions", response_model=list[AgentSessionResponse])
def list_sessions(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    sessions = db.scalars(
        select(AgentSession)
        .where(AgentSession.user_id == user.id, AgentSession.archived_at.is_(None))
        .order_by(AgentSession.updated_at.desc())
    ).all()
    return [serialize_session(session) for session in sessions]


@router.get("/sessions/{session_id}", response_model=list[AgentMessageResponse])
def get_session_history(
    session_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    load_owned_session(db, user, session_id)
    messages = db.scalars(
        select(AgentMessage)
        .where(AgentMessage.session_id == session_id, AgentMessage.user_id == user.id)
        .order_by(AgentMessage.created_at.asc())
    ).all()
    return [serialize_message(message) for message in messages]


@router.delete("/sessions/{session_id}")
def archive_session(
    session_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = load_owned_session(db, user, session_id)
    session.archived_at = now_utc()
    session.updated_at = now_utc()
    db.commit()
    return {"ok": True}


def sse_event(event_type: str, payload: dict) -> str:
    return f"data: {json.dumps({'type': event_type, **payload}, ensure_ascii=False)}\n\n"


async def stream_agent_turn(
    turn_id: UUID,
    user_message_id: UUID,
    user_id: UUID,
    session_id: UUID,
    user_content: str,
    research_run_id: UUID | None = None,
    dispatch_summary: Mapping[str, object] | None = None,
):
    # ADR 0012：research_run_id 仅为兼容旧调用签名而保留，运行时由会话解析研究
    # 上下文，不再从调用方接收归属。
    del research_run_id
    request = AgentTurnRequest(
        turn_id=turn_id,
        user_message_id=user_message_id,
        user_id=user_id,
        session_id=session_id,
        user_content=user_content,
        dispatch_summary=dispatch_summary,
    )
    async for event in stream_agent_turn_events(request):
        event_type = str(event.get("type") or "event")
        payload = {key: value for key, value in event.items() if key != "type"}
        yield sse_event(event_type, payload)


# wzf：正式函数接收服务端 dispatch_summary；旧版测试/扩展 monkeypatch 可能仍只有六个参数。
def _stream_agent_turn_compat(
    turn_id: UUID,
    user_message_id: UUID,
    user_id: UUID,
    session_id: UUID,
    user_content: str,
    research_run_id: UUID | None,
    dispatch_summary: Mapping[str, object] | None,
):
    callback = stream_agent_turn
    positional = (
        turn_id,
        user_message_id,
        user_id,
        session_id,
        user_content,
        research_run_id,
    )
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        # 无法读取签名时保留新协议，避免服务端摘要静默丢失。
        return callback(*positional, dispatch_summary=dispatch_summary)
    try:
        signature.bind(*positional, dispatch_summary=dispatch_summary)
    except TypeError:
        try:
            signature.bind(*positional, dispatch_summary)
        except TypeError:
            # 兼容旧 monkeypatch：仅在七参数绑定失败时回退到原六参数调用。
            signature.bind(*positional)
            return callback(*positional)
        return callback(*positional, dispatch_summary)
    return callback(*positional, dispatch_summary=dispatch_summary)


async def stream_existing_agent_turn(turn_id: UUID):
    """重复请求只订阅原轮次终态，不再次执行 Planner 或科研工具。"""
    heartbeat = float(get_settings().agent_sse_heartbeat_seconds)
    while True:
        db = SessionLocal()
        try:
            turn = db.get(AgentTurn, turn_id)
            if turn is None:
                yield sse_event(
                    "error",
                    {
                        "turn_id": str(turn_id),
                        "error": {
                            "error_type": "agent_turn_missing",
                            "message": "原 Agent 轮次不存在，无法恢复。",
                        },
                    },
                )
                yield sse_event("done", {"turn_id": str(turn_id)})
                return
            if expire_stale_agent_turn(db, turn):
                turn = db.get(AgentTurn, turn_id)
                if turn is None:
                    return
            resume_request = None
            # wzf：进程内已排队的 Future 即使等待超过调度租约，也不能再次入队；
            # 服务重启后注册表为空，才由持久化租约允许一个恢复请求接管。
            if (
                not is_agent_turn_scheduled(turn.id)
                and reserve_queued_turn_dispatch(db, turn)
            ):
                turn = db.get(AgentTurn, turn_id)
                user_message = (
                    db.get(AgentMessage, turn.user_message_id)
                    if turn is not None
                    else None
                )
                if turn is not None and user_message is not None:
                    dispatch_summary = load_dispatch_summary(
                        user_message.metadata_json,
                        user_id=turn.user_id,
                        session_id=turn.session_id,
                        turn_id=turn.id,
                    )
                    resume_request = AgentTurnRequest(
                        turn_id=turn.id,
                        user_message_id=user_message.id,
                        user_id=turn.user_id,
                        session_id=turn.session_id,
                        user_content=user_message.content,
                        dispatch_summary=(
                            dispatch_summary.to_dict()
                            if dispatch_summary is not None
                            else None
                        ),
                    )
            status_value = turn.status
            assistant_message_id = turn.assistant_message_id
            error_code = turn.error_code
        finally:
            db.close()
        if resume_request is not None:
            async for event in stream_agent_turn_events(resume_request):
                event_type = str(event.get("type") or "event")
                payload = {
                    key: value
                    for key, value in event.items()
                    if key != "type"
                }
                yield sse_event(event_type, payload)
            return
        if status_value in {"succeeded", "failed"}:
            if status_value == "failed":
                yield sse_event(
                    "error",
                    {
                        "turn_id": str(turn_id),
                        "error": {
                            "error_type": error_code or "agent_turn_failed",
                            "message": (
                                "原 Agent 轮次已失联并被安全终止，请使用新的 turn_id 重试。"
                                if error_code == "agent_turn_stale"
                                else "原 Agent 轮次执行失败；请查看历史消息或受控服务器日志。"
                            ),
                        },
                    },
                )
            if assistant_message_id is not None:
                yield sse_event(
                    "message_done",
                    {
                        "turn_id": str(turn_id),
                        "message_id": str(assistant_message_id),
                        "status": status_value,
                        "recovered": True,
                    },
                )
            yield sse_event(
                "done",
                {
                    "turn_id": str(turn_id),
                    "status": status_value,
                    "recovered": True,
                },
            )
            return
        yield sse_event(
            "heartbeat",
            {
                "turn_id": str(turn_id),
                "turn_status": status_value,
                "recovered": True,
                "generated_at": now_utc().isoformat(),
            },
        )
        await asyncio.sleep(heartbeat)


@router.get(
    "/sessions/{session_id}/turns/active",
    response_model=ActiveAgentTurnResponse | None,
)
def get_active_agent_turn(
    session_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session = load_owned_session(db, user, session_id)
    active_key = f"{user.id}:{session.id}"
    turn = db.scalar(
        select(AgentTurn).where(AgentTurn.active_session_key == active_key)
    )
    if turn is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    if expire_stale_agent_turn(db, turn):
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    db.refresh(turn)
    return serialize_active_turn(db, turn)


@router.get("/turns/{turn_reference}/events")
def recover_agent_turn_events(
    turn_reference: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    turn = db.get(AgentTurn, turn_reference)
    if turn is not None and turn.user_id != user.id:
        turn = None
    if turn is None:
        matches = db.scalars(
            select(AgentTurn).where(
                AgentTurn.user_id == user.id,
                AgentTurn.client_turn_id == turn_reference,
            )
        ).all()
        if len(matches) > 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "client_turn_id is ambiguous across sessions; "
                    "recover with the server turn_id"
                ),
            )
        turn = matches[0] if matches else None
    if turn is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent turn not found",
        )
    return agent_stream_response(stream_existing_agent_turn(turn.id))


@router.post("/sessions/{session_id}/message")
def send_message(
    session_id: UUID,
    payload: AgentMessageRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    request_hash = hashlib.sha256(
        json.dumps(
            {"content": payload.content, "approval_id": payload.approval_id},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    # The transaction-scoped lock makes the quota check and insert one admission
    # decision across every web process. It is deliberately acquired before DB
    # reads so SQLite does not need to upgrade a stale read snapshot to a writer.
    acquire_transaction_lock(db, "agents.admission")
    session = load_owned_session(db, user, session_id)
    # ADR 0012：不再由客户端绑定科研运行。已有研究上下文按会话只读解析；没有
    # 上下文时保持为空，直到某个需要归属的科研工具首次执行才惰性建立。
    research_run = resolve_session_research_context(db, user, session.id, create=False)
    existing_turn = db.scalar(
        select(AgentTurn).where(
            AgentTurn.user_id == user.id,
            AgentTurn.session_id == session.id,
            AgentTurn.client_turn_id == payload.turn_id,
        )
    )
    if existing_turn is not None:
        if existing_turn.request_hash != request_hash:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="turn_id has already been used for a different request",
            )
        # Release the cross-process admission lock before the long-lived stream.
        # Idempotent replay is allowed even while all new-turn slots are full.
        db.commit()
        return agent_stream_response(stream_existing_agent_turn(existing_turn.id))

    expire_stale_agent_turns_for_admission(db)
    active_key = f"{user.id}:{session.id}"
    active_turn = db.scalar(
        select(AgentTurn).where(AgentTurn.active_session_key == active_key)
    )
    if active_turn is not None and expire_stale_agent_turn(
        db,
        active_turn,
        commit=False,
    ):
        active_turn = None
    if active_turn is not None:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_type": "agent_turn_active",
                "message": "当前会话已有 Agent 轮次执行中，请等待或使用原 turn_id 恢复。",
                "turn_id": str(active_turn.client_turn_id),
            },
        )
    try:
        _enforce_agent_turn_quotas(db, user)
    except AgentTurnQuotaExceeded as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error_type": exc.error_type,
                "message": str(exc),
                "limit": exc.limit,
            },
            headers={"Retry-After": "5"},
        ) from exc

    user_message = AgentMessage(
        session_id=session.id,
        user_id=user.id,
        role="user",
        content=payload.content,
        metadata_json={
            "client_turn_id": str(payload.turn_id),
            "approval_id": payload.approval_id,
        },
    )
    db.add(user_message)
    db.flush()
    agent_turn = AgentTurn(
        client_turn_id=payload.turn_id,
        user_id=user.id,
        session_id=session.id,
        research_run_id=research_run.id if research_run else None,
        user_message_id=user_message.id,
        request_hash=request_hash,
        status="queued",
        active_session_key=active_key,
        lease_owner="dispatcher:new-request",
        lease_expires_at=now_utc() + timedelta(seconds=30),
    )
    db.add(agent_turn)
    try:
        # flush/commit 同一异常边界内，保留原有并发重复轮次的 IntegrityError 回退。
        db.flush()
        dispatch_summary = new_agent_dispatch_summary(
            user_id=user.id,
            session_id=session.id,
            turn_id=agent_turn.id,
        )
        user_message.metadata_json = {
            **(user_message.metadata_json or {}),
            DISPATCH_METADATA_KEY: dispatch_summary.to_dict(),
        }
        session.updated_at = now_utc()
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        duplicate = db.scalar(
            select(AgentTurn).where(
                AgentTurn.user_id == user.id,
                AgentTurn.session_id == session.id,
                AgentTurn.client_turn_id == payload.turn_id,
            )
        )
        if duplicate is not None and duplicate.request_hash == request_hash:
            return agent_stream_response(stream_existing_agent_turn(duplicate.id))
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="当前会话已有 Agent 轮次执行中",
        ) from exc
    db.refresh(user_message)
    db.refresh(agent_turn)

    return agent_stream_response(
        _stream_agent_turn_compat(
            agent_turn.id,
            user_message.id,
            user.id,
            session.id,
            payload.content,
            research_run.id if research_run else None,
            dispatch_summary.to_dict(),
        )
    )
