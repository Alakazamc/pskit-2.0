"""会话研究上下文解析（ADR 0012《会话即研究上下文》）。

科研归属由会话直接得出。用户与模型都不提供 ``research_run_id``；需要归属的
科研调用在首次执行时由服务端惰性建立上下文。本模块只做解析与创建，不推进
阶段、不改写候选、不删除或合并历史数据。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import AgentSession, CandidateTrack, ResearchRun, User


SESSION_CONTEXT_ORIGIN = "session_native"
DEFAULT_CONTEXT_TITLE = "对话研究上下文"


def _owned_active_session(db: Session, user: User, session_id: UUID) -> AgentSession | None:
    """只接受属于当前用户且未归档的会话；跨用户请求一律视为不存在。"""

    session = db.get(AgentSession, session_id)
    if session is None or session.user_id != user.id:
        return None
    if session.archived_at is not None:
        return None
    return session


def _existing_contexts(db: Session, user: User, session_id: UUID) -> list[ResearchRun]:
    """按创建时间升序返回该会话名下的全部研究上下文。"""

    return list(
        db.scalars(
            select(ResearchRun)
            .where(
                ResearchRun.user_id == user.id,
                ResearchRun.session_id == session_id,
            )
            .order_by(ResearchRun.created_at.asc(), ResearchRun.id.asc())
        ).all()
    )


def _create_context(
    db: Session,
    user: User,
    session: AgentSession,
) -> ResearchRun:
    """建立上下文与双轨道；字段全部由服务端确定，不向用户提问。"""

    from app.agent.policy import ensure_strategy_policy

    context = ResearchRun(
        user_id=user.id,
        session_id=session.id,
        title=(session.title or DEFAULT_CONTEXT_TITLE).strip() or DEFAULT_CONTEXT_TITLE,
        target_json={},
        stage_state_json={"target_analysis": {"status": "pending"}},
        metadata_json={"origin": SESSION_CONTEXT_ORIGIN},
    )
    db.add(context)
    db.flush()
    db.add_all(
        [
            CandidateTrack(research_run_id=context.id, user_id=user.id, track="rna"),
            CandidateTrack(research_run_id=context.id, user_id=user.id, track="peptide"),
        ]
    )
    # 策略行与上下文在同一事务建立，保持既有策略反馈语义。
    ensure_strategy_policy(db, user, context)
    db.flush()
    return context


def resolve_session_research_context(
    db: Session,
    user: User,
    session_id: UUID,
    *,
    create: bool = False,
) -> ResearchRun | None:
    """解析该会话的研究上下文，必要时惰性建立。

    历史数据可能让一个会话对应多条上下文。这种情况固定返回最早创建的一条，
    其余保持原样只读保留，不合并也不删除。
    """

    session = _owned_active_session(db, user, session_id)
    if session is None:
        return None

    existing = _existing_contexts(db, user, session_id)
    if existing:
        return existing[0]
    if not create:
        return None

    try:
        with db.begin_nested():
            context = _create_context(db, user, session)
    except IntegrityError:
        # 并发轮次可能已经建立上下文；回滚保存点后复用权威行。
        concurrent = _existing_contexts(db, user, session_id)
        if not concurrent:
            raise
        return concurrent[0]

    # 并发插入不会触发 IntegrityError（没有唯一约束），因此再查一次以固定
    # 「最早一条」这一不变量。
    settled = _existing_contexts(db, user, session_id)
    return settled[0] if settled else context


def session_research_context_or_error(
    db: Session,
    user: User,
    session_id: UUID | None,
) -> ResearchRun:
    """需要归属的科研工具入口；缺少可用会话时给出明确错误。"""

    from app.tools.external import ToolExecutionError

    if session_id is None:
        raise ToolExecutionError(
            "This tool requires an active agent session to attribute scientific work"
        )
    context = resolve_session_research_context(db, user, session_id, create=True)
    if context is None:
        raise ToolExecutionError("Agent session not found")
    return context


__all__ = [
    "DEFAULT_CONTEXT_TITLE",
    "SESSION_CONTEXT_ORIGIN",
    "resolve_session_research_context",
    "session_research_context_or_error",
]
