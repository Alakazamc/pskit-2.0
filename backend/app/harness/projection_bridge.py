"""AgentSession 到 Harness ResearchSession 的只读投影桥接。

AgentView 当前持有旧 AgentSession ID，而 Harness 投影事实表使用 ResearchSession
ID。本模块只解析已有对象和 ``legacy_agent_session_id`` 映射，不创建会话、不改写
消息或任务，也不要求普通对话提供 ``research_run_id``。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.harness_models import ResearchSession
from app.db.models import AgentSession


ProjectionSource = Literal[
    "research_session",
    "agent_session",
    "agent_session_unmapped",
]


class ProjectionBridgeError(ValueError):
    """投影 ID 或持久事实不满足桥接安全约束。"""


class ProjectionAccessError(ProjectionBridgeError):
    """请求对象不存在、归档或不属于当前用户。"""


class ProjectionAmbiguousError(ProjectionBridgeError):
    """一个旧 AgentSession 映射到多个活动 ResearchSession。"""


@dataclass(frozen=True, slots=True)
class ProjectionSessionRef:
    """投影端点可使用的只读会话引用。"""

    requested_session_id: UUID
    user_id: UUID
    research_session_id: UUID | None
    agent_session_id: UUID | None
    source: ProjectionSource

    @property
    def projection_session_id(self) -> UUID | None:
        """返回投影端点需要的 ResearchSession ID；普通对话可能没有。"""

        return self.research_session_id

    @property
    def available(self) -> bool:
        """只有存在活动 ResearchSession 时才应请求 Harness 投影端点。"""

        return self.research_session_id is not None


def _value(row: Any, name: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(name, default)
    return getattr(row, name, default)


def _uuid(value: Any, field_name: str) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ProjectionBridgeError(f"{field_name} must be a UUID") from exc


def _optional_uuid(value: Any, field_name: str) -> UUID | None:
    if value is None:
        return None
    return _uuid(value, field_name)


def _user_uuid(user_or_id: Any) -> UUID:
    return _uuid(getattr(user_or_id, "id", user_or_id), "user_id")


def _assert_owned_active(row: Any, user_id: UUID, label: str) -> UUID:
    row_id = _uuid(_value(row, "id"), f"{label}.id")
    row_user_id = _uuid(_value(row, "user_id"), f"{label}.user_id")
    if row_user_id != user_id:
        raise ProjectionAccessError("projection session is not owned by the current user")
    if _value(row, "archived_at") is not None:
        raise ProjectionAccessError("projection session is archived")
    return row_id


def resolve_projection_reference(
    requested_session_id: UUID | str,
    user_or_id: Any,
    *,
    research_sessions: Iterable[Any] = (),
    agent_sessions: Iterable[Any] = (),
) -> ProjectionSessionRef | None:
    """在已加载事实中解析 ResearchSession 或旧 AgentSession。

    该纯函数便于在 API 适配层之外复用并测试所有权和一对一映射约束。没有
    ResearchSession 的 AgentSession 返回 ``available=False``，供普通对话跳过
    投影；不会创建 ResearchRun 或任何替代对象。
    """

    requested_id = _uuid(requested_session_id, "session_id")
    user_id = _user_uuid(user_or_id)
    research_rows = tuple(research_sessions)
    agent_rows = tuple(agent_sessions)

    direct_rows = [
        row
        for row in research_rows
        if _uuid(_value(row, "id"), "research_session.id") == requested_id
    ]
    if direct_rows:
        if len(direct_rows) != 1:
            raise ProjectionAmbiguousError("duplicate ResearchSession identity")
        row = direct_rows[0]
        research_id = _assert_owned_active(row, user_id, "ResearchSession")
        return ProjectionSessionRef(
            requested_session_id=requested_id,
            user_id=user_id,
            research_session_id=research_id,
            agent_session_id=_optional_uuid(
                _value(row, "legacy_agent_session_id"),
                "research_session.legacy_agent_session_id",
            ),
            source="research_session",
        )

    matching_agents = [
        row
        for row in agent_rows
        if _uuid(_value(row, "id"), "agent_session.id") == requested_id
    ]
    if not matching_agents:
        return None
    if len(matching_agents) != 1:
        raise ProjectionAmbiguousError("duplicate AgentSession identity")
    agent = matching_agents[0]
    agent_id = _assert_owned_active(agent, user_id, "AgentSession")

    linked_rows = [
        row
        for row in research_rows
        if _optional_uuid(
            _value(row, "legacy_agent_session_id"),
            "research_session.legacy_agent_session_id",
        )
        == agent_id
    ]
    for row in linked_rows:
        row_user_id = _uuid(_value(row, "user_id"), "research_session.user_id")
        if row_user_id != user_id:
            raise ProjectionAccessError("projection mapping is not owned by the current user")
    active_rows = [row for row in linked_rows if _value(row, "archived_at") is None]
    if len(active_rows) > 1:
        raise ProjectionAmbiguousError(
            "AgentSession maps to multiple active ResearchSession objects"
        )
    if not active_rows:
        return ProjectionSessionRef(
            requested_session_id=requested_id,
            user_id=user_id,
            research_session_id=None,
            agent_session_id=agent_id,
            source="agent_session_unmapped",
        )

    research_id = _assert_owned_active(active_rows[0], user_id, "ResearchSession")
    return ProjectionSessionRef(
        requested_session_id=requested_id,
        user_id=user_id,
        research_session_id=research_id,
        agent_session_id=agent_id,
        source="agent_session",
    )


def resolve_projection_session(
    db: Session,
    user_or_id: Any,
    requested_session_id: UUID | str,
) -> ProjectionSessionRef | None:
    """从 SQLAlchemy Session 只读解析投影会话。

    查询先检查 ResearchSession，再检查 AgentSession 的旧映射；跨用户对象不会
    回退成另一种类型，避免通过 UUID 碰撞泄露存在性。函数不调用 ``add``、
    ``flush``、``commit`` 或任何删除/迁移操作。
    """

    user_id = _user_uuid(user_or_id)
    requested_id = _uuid(requested_session_id, "session_id")
    research = db.get(ResearchSession, requested_id)
    if research is not None:
        return resolve_projection_reference(
            requested_id,
            user_id,
            research_sessions=(research,),
        )

    agent = db.get(AgentSession, requested_id)
    if agent is None:
        return None
    linked_result = db.scalars(
        select(ResearchSession)
        .where(
            ResearchSession.legacy_agent_session_id == requested_id,
            ResearchSession.user_id == user_id,
            ResearchSession.archived_at.is_(None),
        )
        .order_by(ResearchSession.created_at.asc(), ResearchSession.id.asc())
    )
    linked = linked_result.all() if hasattr(linked_result, "all") else tuple(linked_result)
    return resolve_projection_reference(
        requested_id,
        user_id,
        research_sessions=linked,
        agent_sessions=(agent,),
    )


def resolve_projection_session_id(
    db: Session,
    user_or_id: Any,
    requested_session_id: UUID | str,
) -> UUID | None:
    """返回投影端点需要的 ResearchSession ID；未映射普通对话返回 ``None``。"""

    reference = resolve_projection_session(db, user_or_id, requested_session_id)
    return reference.projection_session_id if reference is not None else None