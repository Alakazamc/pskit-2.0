from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select, update

from app.agent.orchestrator import (
    AgentRunRecord,
    AgentRuntime,
    LangGraphAgentRunner,
    public_approval_payload,
    tool_call_fingerprint,
)
from app.harness.migration_bridge import (
    get_agent_runtime_bridge,
    load_dispatch_summary,
    new_agent_dispatch_summary,
)
from app.agent.leases import (
    AgentLeaseRevoked,
    clear_agent_commit_fence,
    install_agent_commit_fence,
)
from app.config import get_settings
from app.db.models import (
    AgentMessage,
    AgentSession,
    AgentTurn,
    User,
    ensure_utc,
    now_utc,
)
from app.db.session import SessionLocal
from app.research.context import resolve_session_research_context


logger = logging.getLogger(__name__)
AgentEventEmitter = Callable[[dict[str, Any]], None]
_STREAM_END = object()
_executor_lock = threading.Lock()
_agent_executor: ThreadPoolExecutor | None = None
_scheduled_turns_lock = threading.Lock()
_scheduled_turns: set[UUID] = set()


@dataclass(frozen=True)
class AgentTurnRequest:
    turn_id: UUID
    user_message_id: UUID
    user_id: UUID
    session_id: UUID
    user_content: str
    # 服务端生成并持久化的摘要；研究上下文由会话解析，不随请求传递。
    dispatch_summary: Mapping[str, Any] | None = None


class AgentExecutionError(RuntimeError):
    pass


class AgentTurnLeaseLost(AgentLeaseRevoked):
    pass


@dataclass(frozen=True)
class AgentTurnResult:
    message_id: UUID
    failed: bool


class AgentTurnHeartbeat:
    """后台轮次存活心跳，避免长流式调用被新请求误判为失联。"""

    def __init__(self, turn_id: UUID, lease_token: str) -> None:
        self.turn_id = turn_id
        self.lease_token = lease_token
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def __enter__(self) -> "AgentTurnHeartbeat":
        interval = min(30.0, float(get_settings().agent_sse_heartbeat_seconds))

        def heartbeat() -> None:
            while not self.stop_event.wait(interval):
                db = SessionLocal()
                try:
                    now = now_utc()
                    refreshed = db.execute(
                        update(AgentTurn)
                        .where(
                            AgentTurn.id == self.turn_id,
                            AgentTurn.status == "running",
                            AgentTurn.lease_token == self.lease_token,
                        )
                        .values(
                            updated_at=now,
                            lease_expires_at=now
                            + timedelta(
                                seconds=get_settings().agent_turn_stale_seconds
                            ),
                        )
                    )
                    db.commit()
                    if refreshed.rowcount != 1:
                        logger.warning(
                            "Agent 轮次租约已失效 turn_id=%s",
                            self.turn_id,
                        )
                        self.stop_event.set()
                except Exception:
                    db.rollback()
                    logger.exception(
                        "Agent 轮次心跳更新失败 turn_id=%s",
                        self.turn_id,
                    )
                finally:
                    db.close()

        self.thread = threading.Thread(
            target=heartbeat,
            name=f"pskit-agent-turn-heartbeat-{str(self.turn_id)[:8]}",
            daemon=True,
        )
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2)


class AgentDispatchHeartbeat:
    """Keep an executor-queued turn distinguishable from an orphan after restart."""

    def __init__(self, turn_id: UUID) -> None:
        self.turn_id = turn_id
        self.owner = f"dispatcher:{os.getpid()}:{uuid4()}"
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        settings = get_settings()
        self.lease_seconds = max(
            30.0,
            min(
                float(settings.agent_turn_stale_seconds),
                float(settings.agent_sse_heartbeat_seconds) * 4,
            ),
        )

    def start(self) -> None:
        db = SessionLocal()
        try:
            now = now_utc()
            claimed = db.execute(
                update(AgentTurn)
                .where(
                    AgentTurn.id == self.turn_id,
                    AgentTurn.status == "queued",
                    AgentTurn.lease_owner.in_(
                        ("dispatcher:new-request", "dispatcher:recovery")
                    ),
                )
                .values(
                    lease_owner=self.owner,
                    lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                    updated_at=now,
                )
            )
            db.commit()
            if claimed.rowcount != 1:
                raise AgentExecutionError(
                    "Agent 轮次已不在等待队列，禁止重复调度"
                )
        finally:
            db.close()

        interval = max(1.0, self.lease_seconds / 3)

        def heartbeat() -> None:
            while not self.stop_event.wait(interval):
                session = SessionLocal()
                try:
                    now = now_utc()
                    refreshed = session.execute(
                        update(AgentTurn)
                        .where(
                            AgentTurn.id == self.turn_id,
                            AgentTurn.status == "queued",
                            AgentTurn.lease_owner == self.owner,
                        )
                        .values(
                            updated_at=now,
                            lease_expires_at=now
                            + timedelta(seconds=self.lease_seconds),
                        )
                    )
                    session.commit()
                    if refreshed.rowcount != 1:
                        self.stop_event.set()
                except Exception:
                    session.rollback()
                    logger.exception(
                        "Agent 排队调度租约更新失败 turn_id=%s",
                        self.turn_id,
                    )
                finally:
                    session.close()

        self.thread = threading.Thread(
            target=heartbeat,
            name=f"pskit-agent-dispatch-heartbeat-{str(self.turn_id)[:8]}",
            daemon=True,
        )
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)


def get_agent_executor() -> ThreadPoolExecutor:
    global _agent_executor
    if _agent_executor is None:
        with _executor_lock:
            if _agent_executor is None:
                settings = get_settings()
                _agent_executor = ThreadPoolExecutor(
                    max_workers=settings.agent_max_concurrent_runs,
                    thread_name_prefix="pskit-agent",
                )
    return _agent_executor


def is_agent_turn_scheduled(turn_id: UUID) -> bool:
    """判断当前进程是否已经为该轮次保留了唯一执行 Future。"""

    with _scheduled_turns_lock:
        return turn_id in _scheduled_turns


def _reserve_agent_turn_schedule(turn_id: UUID) -> bool:
    with _scheduled_turns_lock:
        if turn_id in _scheduled_turns:
            return False
        _scheduled_turns.add(turn_id)
        return True


def _release_agent_turn_schedule(turn_id: UUID) -> None:
    with _scheduled_turns_lock:
        _scheduled_turns.discard(turn_id)


def recent_execution_context(
    rows: Sequence[AgentMessage],
    *,
    exclude_message_id: UUID | None,
) -> dict[str, str] | None:
    """Return the newest server-authored fact envelope as system-only context."""

    for row in rows:
        if row.id == exclude_message_id or row.role != "assistant":
            continue
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        facts = metadata.get("execution_facts")
        continuation = metadata.get("continuation")
        if not isinstance(facts, dict) and not isinstance(continuation, dict):
            continue
        payload = {
            "execution_facts": facts if isinstance(facts, dict) else {},
            "continuation": continuation if isinstance(continuation, dict) else None,
        }
        return {
            "role": "system",
            "content": (
                "最近一次服务端执行事实（仅这些任务、工具和产物标识可视为已验证）："
                + json.dumps(payload, ensure_ascii=False, default=str)
            ),
        }
    return None


def recent_chat_messages(
    db,
    session_id: UUID,
    user_id: UUID,
    limit: int | None = None,
    *,
    exclude_message_id: UUID | None = None,
) -> list[dict[str, str]]:
    settings = get_settings()
    recent_limit = limit or settings.agent_history_recent_messages
    scan_limit = max(recent_limit, settings.agent_history_scan_messages)
    rows = db.scalars(
        select(AgentMessage)
        .where(
            AgentMessage.session_id == session_id,
            AgentMessage.user_id == user_id,
        )
        .order_by(AgentMessage.created_at.desc())
        .limit(scan_limit)
    ).all()
    execution_context = recent_execution_context(
        rows,
        exclude_message_id=exclude_message_id,
    )
    messages: list[dict[str, str]] = []
    for row in reversed(rows):
        if row.id != exclude_message_id and row.role in {"user", "assistant"}:
            item = {"role": row.role, "content": row.content}
            # Legacy interrupted requests can leave consecutive user rows with
            # no assistant turn. Only the newest request in that run is valid
            # conversational context; replaying every abandoned row can trigger
            # duplicate scientific actions after recovery.
            if row.role == "user" and messages and messages[-1]["role"] == "user":
                messages[-1] = item
            else:
                messages.append(item)
    if len(messages) <= recent_limit:
        return [execution_context, *messages] if execution_context else messages
    older = messages[:-recent_limit]
    summary_lines = [
        "较早对话的确定性截断摘要（仅用于恢复上下文，不代表新的科研结论）："
    ]
    remaining = settings.agent_history_summary_chars
    for item in older:
        label = "用户" if item["role"] == "user" else "Agent"
        compact = " ".join(item["content"].split())
        line = f"- {label}：{compact[:600]}"
        if len(line) > remaining:
            break
        summary_lines.append(line)
        remaining -= len(line)
    compacted = [
        {"role": "system", "content": "\n".join(summary_lines)},
        *messages[-recent_limit:],
    ]
    if execution_context:
        compacted.insert(1, execution_context)
    return compacted


def consume_pending_approval(
    db,
    request: AgentTurnRequest,
) -> tuple[dict[str, Any] | None, str | None]:
    user_message = db.get(AgentMessage, request.user_message_id)
    user_metadata = (
        user_message.metadata_json
        if user_message is not None and isinstance(user_message.metadata_json, dict)
        else {}
    )
    approval_id = user_metadata.get("approval_id")
    if not isinstance(approval_id, str) or not approval_id:
        return None, None

    assistant_messages = db.scalars(
        select(AgentMessage)
        .where(
            AgentMessage.session_id == request.session_id,
            AgentMessage.user_id == request.user_id,
            AgentMessage.role == "assistant",
        )
        .order_by(AgentMessage.created_at.desc())
    ).all()
    for message in assistant_messages:
        metadata = message.metadata_json if isinstance(message.metadata_json, dict) else {}
        pending = metadata.get("_internal_pending_approval")
        if not isinstance(pending, dict) or pending.get("approval_id") != approval_id:
            continue
        consumed_by = pending.get("consumed_by_turn_id")
        if consumed_by not in {None, str(request.turn_id)}:
            raise AgentExecutionError("该确认已被其他轮次使用，请重新发起工具请求。")
        tool_name = pending.get("tool_name")
        raw_arguments = pending.get("raw_arguments")
        expected_fingerprint = pending.get("arguments_hash")
        if not isinstance(tool_name, str) or not isinstance(raw_arguments, str):
            raise AgentExecutionError("待确认工具记录不完整，请重新发起工具请求。")
        actual_fingerprint = tool_call_fingerprint(tool_name, raw_arguments)
        if expected_fingerprint != actual_fingerprint:
            raise AgentExecutionError("待确认工具参数校验失败，请重新发起工具请求。")
        updated_pending = {
            **pending,
            "consumed_by_turn_id": str(request.turn_id),
            "consumed_at": now_utc().isoformat(),
        }
        message.metadata_json = {
            **metadata,
            "_internal_pending_approval": updated_pending,
        }
        # 确认是一次性授权凭证，必须先于任何工具副作用独立持久化。
        # 后续工具分支即使 rollback，也不能把它恢复为可再次消费状态。
        db.commit()
        return (
            {
                "id": f"approved-{approval_id[:16]}",
                "type": "function",
                "approval_id": approval_id,
                "function": {
                    "name": tool_name,
                    "arguments": raw_arguments,
                },
            },
            actual_fingerprint,
        )
    raise AgentExecutionError("待确认工具不存在或不属于当前对话。")


def load_agent_runtime(db, request: AgentTurnRequest) -> AgentRuntime:
    user = db.get(User, request.user_id)
    if user is None or user.disabled_at is not None:
        raise AgentExecutionError("Agent 用户不存在或已被禁用")

    session = db.get(AgentSession, request.session_id)
    if (
        session is None
        or session.user_id != user.id
        or session.archived_at is not None
    ):
        raise AgentExecutionError("Agent 会话不存在、已归档或不属于当前用户")

    # ADR 0012：研究上下文由会话解析，请求不再携带 research_run_id。
    # 这里只做只读解析，不惰性创建；首次需要归属的科研工具调用才会建立上下文。
    research_run = resolve_session_research_context(db, user, session.id, create=False)

    dispatch_summary = load_dispatch_summary(
        request.dispatch_summary,
        user_id=user.id,
        session_id=session.id,
        turn_id=request.turn_id,
    )
    if dispatch_summary is None:
        # 旧轮次没有摘要时由服务端稳定派生，绝不采用客户端自由文本。
        dispatch_summary = new_agent_dispatch_summary(
            user_id=user.id,
            session_id=session.id,
            turn_id=request.turn_id,
        )
    bridge = get_agent_runtime_bridge()
    binding = bridge.bind(
        summary=dispatch_summary,
        user_id=user.id,
        session_id=session.id,
        turn_id=request.turn_id,
    )
    approved_tool_call, approved_tool_fingerprint = consume_pending_approval(
        db,
        request,
    )
    return AgentRuntime(
        db=db,
        user=user,
        session=session,
        research_run=research_run,
        turn_id=request.turn_id,
        correlation_id=dispatch_summary.correlation_id,
        dispatch_summary=dispatch_summary.to_dict(),
        harness_executor=binding.executor,
        harness_wiring_context=binding.wiring_context,
        harness_dispatch_factory=binding.dispatch_for,
        approved_tool_call=approved_tool_call,
        approved_tool_fingerprint=approved_tool_fingerprint,
    )


def assert_agent_turn_lease(
    db,
    turn_id: UUID,
    lease_token: str,
) -> None:
    row = db.execute(
        select(
            AgentTurn.status,
            AgentTurn.lease_token,
            AgentTurn.lease_expires_at,
        ).where(AgentTurn.id == turn_id)
    ).one_or_none()
    if (
        row is None
        or row.status != "running"
        or row.lease_token != lease_token
        or row.lease_expires_at is None
        or ensure_utc(row.lease_expires_at) < now_utc()
    ):
        raise AgentTurnLeaseLost("Agent 轮次执行租约已失效")


def persist_assistant_message(
    db,
    runtime: AgentRuntime,
    record: AgentRunRecord,
) -> UUID:
    assistant_message = AgentMessage(
        session_id=runtime.session.id,
        user_id=runtime.user.id,
        role="assistant",
        content=record.final_answer,
        metadata_json={
            "sources": record.sources,
            "rag_backend": record.rag_backend,
            "artifacts": record.artifacts,
            "execution_facts": record.execution_facts,
            "continuation": record.continuation,
            "approval": (
                public_approval_payload(record.pending_approval)
                if record.pending_approval is not None
                else None
            ),
            "_internal_research_run_id": (
                str(runtime.research_run.id)
                if runtime.research_run is not None
                else None
            ),
            "_internal_pending_approval": record.pending_approval,
            "suggestions": record.suggestions,
            "events": record.events,
            "agent_architecture": "langgraph_planner_executor_synthesizer",
            "agent_execution": "thread_isolated",
            "agent_turn_id": str(record.events[0].get("turn_id")) if record.events else None,
            "generated_at": now_utc().isoformat(),
        },
    )
    db.add(assistant_message)
    runtime.session.updated_at = now_utc()
    db.flush()
    return assistant_message.id


def safe_agent_error(exc: Exception, turn_id: UUID) -> tuple[str, str]:
    error_name = exc.__class__.__name__
    if error_name == "LlmUnavailable":
        return "llm_unavailable", "大模型服务暂不可用，请稍后恢复本轮次。"
    if isinstance(exc, AgentLeaseRevoked):
        return (
            "agent_turn_lease_lost",
            "本轮执行租约已被回收，已停止保存旧执行器结果。",
        )
    if isinstance(exc, AgentExecutionError):
        return "agent_context_invalid", str(exc)
    return (
        "agent_runtime_error",
        f"Agent 运行失败，请记录轮次 {turn_id} 并查看受控服务器日志。",
    )


def mark_agent_turn_failed(
    turn_id: UUID,
    error_code: str,
    *,
    lease_token: str | None = None,
) -> None:
    db = SessionLocal()
    try:
        conditions = [AgentTurn.id == turn_id]
        if lease_token is None:
            conditions.append(AgentTurn.status == "queued")
        else:
            conditions.extend(
                [
                    AgentTurn.status == "running",
                    AgentTurn.lease_token == lease_token,
                ]
            )
        timestamp = now_utc()
        db.execute(
            update(AgentTurn)
            .where(*conditions)
            .values(
                status="failed",
                error_code=error_code,
                active_session_key=None,
                lease_token=None,
                lease_owner=None,
                lease_expires_at=None,
                finished_at=timestamp,
                updated_at=timestamp,
            )
        )
        db.commit()
    finally:
        db.close()


def execute_agent_turn(
    request: AgentTurnRequest,
    emit: AgentEventEmitter,
) -> AgentTurnResult:
    db = SessionLocal()
    lease_token: str | None = None
    try:
        turn = db.get(AgentTurn, request.turn_id)
        if (
            turn is None
            or turn.user_id != request.user_id
            or turn.session_id != request.session_id
            or turn.user_message_id != request.user_message_id
        ):
            raise AgentExecutionError("Agent 轮次不存在或与当前会话不匹配")
        started_at = now_utc()
        lease_token = str(uuid4())
        lease_owner = f"{os.getpid()}:{threading.current_thread().name}"
        claimed = db.execute(
            update(AgentTurn)
            .where(
                AgentTurn.id == request.turn_id,
                AgentTurn.user_id == request.user_id,
                AgentTurn.session_id == request.session_id,
                AgentTurn.user_message_id == request.user_message_id,
                AgentTurn.status == "queued",
            )
            .values(
                status="running",
                started_at=started_at,
                updated_at=started_at,
                lease_token=lease_token,
                lease_owner=lease_owner,
                lease_expires_at=started_at
                + timedelta(seconds=get_settings().agent_turn_stale_seconds),
                attempt_no=func.coalesce(AgentTurn.attempt_no, 0) + 1,
            )
        )
        if claimed.rowcount != 1:
            db.rollback()
            raise AgentTurnLeaseLost(
                "Agent 轮次已失效或已被其他执行器领取，禁止重复执行科研工具"
            )
        db.commit()
        install_agent_commit_fence(db, request.turn_id, lease_token)
        with AgentTurnHeartbeat(request.turn_id, lease_token):
            runtime = load_agent_runtime(db, request)
            runtime.lease_guard = lambda: assert_agent_turn_lease(
                db,
                request.turn_id,
                lease_token,
            )
            runner = LangGraphAgentRunner(
                runtime,
                recent_chat_messages(
                    db,
                    runtime.session.id,
                    runtime.user.id,
                    exclude_message_id=request.user_message_id,
                ),
            )
            try:
                for event in runner.iter_events(request.user_content):
                    event.setdefault("turn_id", str(request.turn_id))
                    emit(event)
            except AgentLeaseRevoked:
                raise
            except Exception as exc:
                logger.exception("Agent 轮次执行失败 turn_id=%s", request.turn_id)
                db.rollback()
                runtime = load_agent_runtime(db, request)
                runtime.lease_guard = lambda: assert_agent_turn_lease(
                    db,
                    request.turn_id,
                    lease_token,
                )
                error_code, error_message = safe_agent_error(exc, request.turn_id)
                runner.record.failed = True
                runner.record.error_code = error_code
                error_event = {
                    "type": "error",
                    "turn_id": str(request.turn_id),
                    "generated_at": now_utc().isoformat(),
                    "error": {
                        "error_type": error_code,
                        "message": error_message,
                    },
                }
                runner.record.events.append(error_event)
                runner.record.final_answer = error_message
                runner.record.suggestions = ["检查运行环境", "稍后重试当前请求"]
                emit(error_event)
                emit({"type": "message_delta", "delta": error_message})
            failed = runner.record.failed
            assert_agent_turn_lease(
                db,
                request.turn_id,
                lease_token,
            )
            # 终态更新自身就是带租约与有效期条件的 CAS；先移除通用提交围栏，
            # 否则 before_commit 会在终态清空 token 后把本次合法提交判为失效。
            clear_agent_commit_fence(db)
            message_id = persist_assistant_message(db, runtime, runner.record)
            timestamp = now_utc()
            terminal = db.execute(
                update(AgentTurn)
                .where(
                    AgentTurn.id == request.turn_id,
                    AgentTurn.status == "running",
                    AgentTurn.lease_token == lease_token,
                    AgentTurn.lease_expires_at.is_not(None),
                    AgentTurn.lease_expires_at > timestamp,
                )
                .values(
                    assistant_message_id=message_id,
                    status="failed" if failed else "succeeded",
                    error_code=runner.record.error_code if failed else None,
                    active_session_key=None,
                    lease_token=None,
                    lease_owner=None,
                    lease_expires_at=None,
                    finished_at=timestamp,
                    updated_at=timestamp,
                )
            )
            if terminal.rowcount != 1:
                db.rollback()
                raise AgentTurnLeaseLost(
                    "Agent 轮次租约在保存终态前已失效"
                )
            db.commit()
            return AgentTurnResult(message_id=message_id, failed=failed)
    except Exception as exc:
        db.rollback()
        if lease_token is not None:
            error_code, _message = safe_agent_error(exc, request.turn_id)
            mark_agent_turn_failed(
                request.turn_id,
                error_code,
                lease_token=lease_token,
            )
        raise
    finally:
        clear_agent_commit_fence(db)
        db.close()


def run_agent_turn_worker(
    request: AgentTurnRequest,
    emit: AgentEventEmitter,
) -> None:
    try:
        result = execute_agent_turn(request, emit)
    except Exception as exc:
        logger.exception("Agent 后台执行失败 turn_id=%s", request.turn_id)
        error_code, message = safe_agent_error(exc, request.turn_id)
        mark_agent_turn_failed(request.turn_id, error_code)
        emit(
            {
                "type": "error",
                "turn_id": str(request.turn_id),
                "generated_at": now_utc().isoformat(),
                "error": {
                    "error_type": error_code,
                    "message": message,
                },
            }
        )
        emit(
            {
                "type": "message_delta",
                "turn_id": str(request.turn_id),
                "generated_at": now_utc().isoformat(),
                "delta": message,
            }
        )
    else:
        emit(
            {
                "type": "message_done",
                "turn_id": str(request.turn_id),
                "generated_at": now_utc().isoformat(),
                "message_id": str(result.message_id),
                "status": "failed" if result.failed else "succeeded",
            }
        )
    finally:
        emit(
            {
                "type": "done",
                "turn_id": str(request.turn_id),
                "generated_at": now_utc().isoformat(),
            }
        )


def _consume_executor_result(future, turn_id: UUID | None = None) -> None:
    try:
        future.result()
    except Exception:
        logger.exception("Agent 执行线程出现未捕获异常")
    finally:
        if turn_id is not None:
            _release_agent_turn_schedule(turn_id)


async def stream_agent_turn_events(
    request: AgentTurnRequest,
    *,
    heartbeat_seconds: float | None = None,
    executor: Executor | None = None,
) -> AsyncIterator[dict[str, Any]]:
    settings = get_settings()
    heartbeat = (
        float(heartbeat_seconds)
        if heartbeat_seconds is not None
        else float(settings.agent_sse_heartbeat_seconds)
    )
    heartbeat = max(heartbeat, 0.001)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict[str, Any] | object] = asyncio.Queue()
    accepting_events = threading.Event()
    accepting_events.set()
    dispatch_heartbeat: AgentDispatchHeartbeat | None = None

    def enqueue(item: dict[str, Any] | object) -> None:
        if accepting_events.is_set():
            try:
                loop.call_soon_threadsafe(queue.put_nowait, item)
            except RuntimeError:
                accepting_events.clear()

    def worker() -> None:
        try:
            if dispatch_heartbeat is not None:
                dispatch_heartbeat.stop()
            run_agent_turn_worker(request, enqueue)
        finally:
            _release_agent_turn_schedule(request.turn_id)
            enqueue(_STREAM_END)

    if not _reserve_agent_turn_schedule(request.turn_id):
        raise AgentExecutionError(
            "该 Agent 轮次已在当前进程调度，禁止重复加入执行队列"
        )
    try:
        dispatch_heartbeat = AgentDispatchHeartbeat(request.turn_id)
        dispatch_heartbeat.start()
        future = loop.run_in_executor(executor or get_agent_executor(), worker)
    except Exception:
        if dispatch_heartbeat is not None:
            dispatch_heartbeat.stop()
        _release_agent_turn_schedule(request.turn_id)
        raise

    def consume_result(completed) -> None:
        if dispatch_heartbeat is not None:
            dispatch_heartbeat.stop()
        _consume_executor_result(completed, request.turn_id)

    future.add_done_callback(consume_result)

    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=heartbeat)
            except asyncio.TimeoutError:
                yield {
                    "type": "heartbeat",
                    "turn_id": str(request.turn_id),
                    "generated_at": now_utc().isoformat(),
                }
                continue
            if item is _STREAM_END:
                break
            if isinstance(item, dict):
                item.setdefault("turn_id", str(request.turn_id))
                yield item
    finally:
        # wzf：客户端断开只停止事件投递；后台轮次继续完成并保存，避免遗失已入队科研任务。
        accepting_events.clear()
