"""PSKit AI4S Harness 的增量领域模型。

本模块只声明新 Harness 表，不改变旧的 ``ResearchRun``、``Task`` 或
``Artifact`` 模型。旧表的外键仅用于核对历史对象，真正的迁移由独立
Alembic revision 负责。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base


JsonType = JSON().with_variant(JSONB, "postgresql")


def now_utc() -> datetime:
    """返回带时区的 UTC 时间，供新表的 Python 默认值使用。"""

    return datetime.now(timezone.utc)


def new_uuid() -> uuid.UUID:
    """生成业务对象主键。"""

    return uuid.uuid4()


def stable_uuid5(namespace: uuid.UUID, source_key: str) -> uuid.UUID:
    """为一次性迁移生成稳定 UUID5，重复迁移不会产生新对象。"""

    return uuid.uuid5(namespace, source_key)


# wzf：这些表是纯增量底座；旧模型仍由 Alembic env 显式导入并聚合到同一 metadata。
class ResearchSession(Base):
    """科研交互上下文，不垄断目标、任务或证据的生命周期。"""

    __tablename__ = "research_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        index=True,
    )
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(40),
        default="active",
        server_default=text("'active'"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    legacy_agent_session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        nullable=True,
        index=True,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JsonType,
        default=dict,
    )


class SessionMessage(Base):
    """会话消息；正文不是科学证据，证据必须另行登记。"""

    __tablename__ = "session_messages"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "sequence_no",
            name="uq_harness_session_message_sequence",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("research_sessions.id"),
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        index=True,
    )
    role: Mapped[str] = mapped_column(String(30), index=True)
    content: Mapped[str] = mapped_column(Text)
    sequence_no: Mapped[int] = mapped_column(Integer, index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JsonType,
        default=dict,
    )
    legacy_agent_message_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class InteractionTurn(Base):
    """具有客户端幂等身份和租约事实的交互轮次。"""

    __tablename__ = "interaction_turns"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "session_id",
            "client_turn_id",
            name="uq_harness_turn_user_session_client",
        ),
        UniqueConstraint("active_session_key", name="uq_harness_turn_active_session"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("research_sessions.id"),
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        index=True,
    )
    client_turn_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    request_hash: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(
        String(40),
        default="queued",
        server_default=text("'queued'"),
        index=True,
    )
    user_message_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("session_messages.id"),
        nullable=True,
        index=True,
    )
    assistant_message_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("session_messages.id"),
        nullable=True,
        index=True,
    )
    active_session_key: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
        index=True,
    )
    lease_owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    attempt_no: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    legacy_agent_turn_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResearchGoal(Base):
    """可核验的科研问题或计算目标。"""

    __tablename__ = "research_goals"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        index=True,
    )
    title: Mapped[str] = mapped_column(Text)
    objective: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(40),
        default="draft",
        server_default=text("'draft'"),
        index=True,
    )
    scope_json: Mapped[dict[str, Any]] = mapped_column("scope", JsonType, default=dict)
    constraints_json: Mapped[dict[str, Any]] = mapped_column(
        "constraints",
        JsonType,
        default=dict,
    )
    success_criteria_json: Mapped[dict[str, Any]] = mapped_column(
        "success_criteria",
        JsonType,
        default=dict,
    )
    legacy_research_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        nullable=True,
        index=True,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JsonType,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SessionGoalLink(Base):
    """会话与目标的多对多上下文关联；目标可跨多次会话继续。"""

    __tablename__ = "session_goal_links"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "goal_id",
            name="uq_harness_session_goal_link",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("research_sessions.id"),
        index=True,
    )
    goal_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("research_goals.id"),
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        index=True,
    )
    role: Mapped[str] = mapped_column(
        String(40),
        default="context",
        server_default=text("'context'"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ScientificTarget(Base):
    """科研目标引用的、带身份快照的规范化生物靶标。"""

    __tablename__ = "scientific_targets"
    __table_args__ = (
        UniqueConstraint(
            "goal_id",
            "identity_hash",
            name="uq_harness_target_goal_identity",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    goal_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("research_goals.id"),
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        index=True,
    )
    name: Mapped[str] = mapped_column(String(200))
    target_type: Mapped[str] = mapped_column(String(80), index=True)
    identifier: Mapped[str] = mapped_column(String(240), index=True)
    chain_id: Mapped[str | None] = mapped_column(String(30), nullable=True)
    sequence: Mapped[str | None] = mapped_column(Text, nullable=True)
    structure_source: Mapped[str | None] = mapped_column(String(240), nullable=True)
    structure_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    identity_hash: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(
        String(40),
        default="active",
        server_default=text("'active'"),
        index=True,
    )
    source_json: Mapped[dict[str, Any]] = mapped_column("source", JsonType, default=dict)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JsonType,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class SkillExecution(Base):
    """某个科研 Skill 针对冻结输入的一次实际执行。"""

    __tablename__ = "skill_executions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    goal_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("research_goals.id"),
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        index=True,
    )
    skill_id: Mapped[str] = mapped_column(String(160), index=True)
    skill_version: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(
        String(50),
        default="planned",
        server_default=text("'planned'"),
        index=True,
    )
    plan_version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    input_json: Mapped[dict[str, Any]] = mapped_column("input", JsonType, default=dict)
    config_json: Mapped[dict[str, Any]] = mapped_column("config", JsonType, default=dict)
    evidence_contract_json: Mapped[dict[str, Any]] = mapped_column(
        "evidence_contract",
        JsonType,
        default=dict,
    )
    legacy_research_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        nullable=True,
        index=True,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JsonType,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaskGraph(Base):
    """Session 原生的持久任务图；Goal/Skill 只是可选上下文。"""

    __tablename__ = "task_graphs"
    __table_args__ = (
        CheckConstraint(
            "revision >= 1",
            name="ck_harness_task_graph_revision_positive",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("research_sessions.id", name="fk_harness_task_graph_session"),
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", name="fk_harness_task_graph_user"),
        index=True,
    )
    goal_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("research_goals.id", name="fk_harness_task_graph_goal"),
        nullable=True,
        index=True,
    )
    skill_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("skill_executions.id", name="fk_harness_task_graph_skill_execution"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(50),
        default="planned",
        server_default=text("'planned'"),
        index=True,
    )
    revision: Mapped[int] = mapped_column(
        Integer,
        default=1,
        server_default=text("1"),
        index=True,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JsonType,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ScientificTask(Base):
    """任务图中的逻辑节点；单次错误和运行时间只存在于 ExecutionAttempt。"""

    __tablename__ = "scientific_tasks"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "task_graph_id",
            name="uq_harness_task_id_graph",
        ),
        UniqueConstraint(
            "task_graph_id",
            "task_key",
            name="uq_harness_task_graph_key",
        ),
        Index(
            "ix_harness_task_graph_ready_priority",
            "task_graph_id",
            "status",
            "available_at",
            "priority",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    task_graph_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("task_graphs.id", name="fk_harness_scientific_task_graph"),
        index=True,
    )
    skill_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("skill_executions.id"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        index=True,
    )
    task_key: Mapped[str] = mapped_column(String(160))
    name: Mapped[str] = mapped_column(Text)
    task_type: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(
        String(50),
        default="planned",
        server_default=text("'planned'"),
        index=True,
    )
    priority: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), index=True)
    queue_name: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    available_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    input_json: Mapped[dict[str, Any]] = mapped_column("input", JsonType, default=dict)
    completion_contract_json: Mapped[dict[str, Any]] = mapped_column(
        "completion_contract",
        JsonType,
        default=dict,
    )
    resource_request_json: Mapped[dict[str, Any]] = mapped_column(
        "resource_request",
        JsonType,
        default=dict,
    )
    idempotency_key: Mapped[str | None] = mapped_column(
        String(240),
        nullable=True,
        index=True,
    )
    legacy_task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        nullable=True,
        index=True,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JsonType,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class TaskDependency(Base):
    """DAG 边；两个复合外键强制端点属于同一 TaskGraph。"""

    __tablename__ = "task_dependencies"
    __table_args__ = (
        ForeignKeyConstraint(
            ["upstream_task_id", "task_graph_id"],
            ["scientific_tasks.id", "scientific_tasks.task_graph_id"],
            name="fk_harness_dependency_upstream_same_graph",
        ),
        ForeignKeyConstraint(
            ["downstream_task_id", "task_graph_id"],
            ["scientific_tasks.id", "scientific_tasks.task_graph_id"],
            name="fk_harness_dependency_downstream_same_graph",
        ),
        CheckConstraint(
            "upstream_task_id <> downstream_task_id",
            name="ck_harness_dependency_no_self",
        ),
        UniqueConstraint(
            "task_graph_id",
            "upstream_task_id",
            "downstream_task_id",
            name="uq_harness_dependency_graph_edge",
        ),
        Index(
            "ix_harness_dependency_graph_downstream",
            "task_graph_id",
            "downstream_task_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    task_graph_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("task_graphs.id", name="fk_harness_dependency_task_graph"),
        index=True,
    )
    skill_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("skill_executions.id"),
        nullable=True,
        index=True,
    )
    upstream_task_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    downstream_task_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    dependency_type: Mapped[str] = mapped_column(
        String(40),
        default="required",
        server_default=text("'required'"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class CapabilityInvocation(Base):
    """一次能力调用事实；能力调用可以只属于 Session，不依赖 Goal/Skill。"""

    __tablename__ = "capability_invocations"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "session_id",
            "idempotency_key",
            name="uq_harness_capability_invocation_user_session_idempotency",
        ),
        CheckConstraint(
            "status IN ('queued', 'waiting_for_approval', 'running', 'waiting_for_resource', "
            "'waiting_for_dependency', 'waiting_for_input', 'succeeded', 'failed', "
            "'timed_out', 'cancelled', 'interrupted', 'blocked', 'rejected')",
            name="ck_harness_capability_invocation_status",
        ),
        Index(
            "ix_harness_capability_invocation_dispatch",
            "status",
            "risk_level",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("research_sessions.id", name="fk_harness_capability_invocation_session"),
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id", name="fk_harness_capability_invocation_user"),
        index=True,
    )
    turn_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("interaction_turns.id", name="fk_harness_capability_invocation_turn"),
        nullable=True,
        index=True,
    )
    goal_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("research_goals.id", name="fk_harness_capability_invocation_goal"),
        nullable=True,
        index=True,
    )
    skill_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey(
            "skill_executions.id",
            name="fk_harness_capability_invocation_skill_execution",
        ),
        nullable=True,
        index=True,
    )
    task_graph_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("task_graphs.id", name="fk_harness_capability_invocation_task_graph"),
        nullable=True,
        index=True,
    )
    scientific_task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("scientific_tasks.id", name="fk_harness_capability_invocation_task"),
        nullable=True,
        index=True,
    )
    execution_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey(
            "execution_attempts.id",
            name="fk_harness_capability_invocation_attempt",
        ),
        nullable=True,
        index=True,
    )
    capability_id: Mapped[str] = mapped_column(String(160), index=True)
    capability_version: Mapped[str] = mapped_column(String(120))
    manifest_digest: Mapped[str] = mapped_column(String(128), index=True)
    execution_mode: Mapped[str] = mapped_column(String(40), index=True)
    risk_level: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(
        String(50),
        default="queued",
        server_default=text("'queued'"),
        index=True,
    )
    input_json: Mapped[dict[str, Any]] = mapped_column("input", JsonType, default=dict)
    request_hash: Mapped[str] = mapped_column(String(128), index=True)
    permission_snapshot_json: Mapped[list[dict[str, Any]]] = mapped_column(
        "permission_snapshot",
        JsonType,
        default=list,
    )
    approval_reference_hash: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(240), index=True)
    result_json: Mapped[dict[str, Any]] = mapped_column("result", JsonType, default=dict)
    error_type: Mapped[str | None] = mapped_column(String(160), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JsonType,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )


class ExecutionAttempt(Base):
    """ScientificTask 的不可变执行事实；重试会新建行并连接 parent_attempt。"""

    __tablename__ = "execution_attempts"
    __table_args__ = (
        UniqueConstraint(
            "scientific_task_id",
            "attempt_no",
            name="uq_harness_attempt_task_number",
        ),
        UniqueConstraint(
            "scientific_task_id",
            "idempotency_key",
            name="uq_harness_attempt_task_idempotency",
        ),
        Index(
            "ix_harness_attempt_dispatch",
            "status",
            "lease_expires_at",
            "available_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    scientific_task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("scientific_tasks.id"),
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        index=True,
    )
    attempt_no: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1"), index=True
    )
    status: Mapped[str] = mapped_column(
        String(50),
        default="queued",
        server_default=text("'queued'"),
        index=True,
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(240), nullable=True)
    input_json: Mapped[dict[str, Any]] = mapped_column("input", JsonType, default=dict)
    output_json: Mapped[dict[str, Any]] = mapped_column("output", JsonType, default=dict)
    error_type: Mapped[str | None] = mapped_column(String(160), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    lease_token: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    available_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    failure_fingerprint: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )
    diagnosis: Mapped[str | None] = mapped_column(Text, nullable=True)
    # wzf：重试诊断与证据引用进入显式列；metadata 仅作为旧数据回填来源和降级兼容层。
    diagnostic_evidence_ids: Mapped[list[str]] = mapped_column(
        "diagnostic_evidence_ids",
        JsonType,
        default=list,
    )
    retry_payload_hash: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )
    retry_policy_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    retry_evidence_snapshot_hash: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )
    parent_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("execution_attempts.id"),
        nullable=True,
        index=True,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JsonType,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )


class WorkflowCheckpoint(Base):
    """可恢复的 Skill 事实快照，不是文件备份或代码回滚点。"""

    __tablename__ = "workflow_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "skill_execution_id",
            "checkpoint_no",
            name="uq_harness_checkpoint_sequence",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    skill_execution_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("skill_executions.id"),
        index=True,
    )
    checkpoint_no: Mapped[int] = mapped_column(Integer, index=True)
    plan_version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    status: Mapped[str] = mapped_column(
        String(40),
        default="active",
        server_default=text("'active'"),
        index=True,
    )
    state_json: Mapped[dict[str, Any]] = mapped_column("state", JsonType, default=dict)
    ready_task_ids_json: Mapped[list[Any]] = mapped_column(
        "ready_task_ids",
        JsonType,
        default=list,
    )
    blocked_dependency_ids_json: Mapped[list[Any]] = mapped_column(
        "blocked_dependency_ids",
        JsonType,
        default=list,
    )
    digest: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JsonType,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class HarnessOutboxEvent(Base):
    """事务内写入、可安全重投的 Harness 事件。"""

    __tablename__ = "harness_outbox_events"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_harness_outbox_dedupe_key"),
        CheckConstraint(
            "status IN ('pending', 'publishing', 'published', 'failed', "
            "'waiting_for_dependency', 'waiting_for_resource', 'dead_letter')",
            name="ck_harness_outbox_status",
        ),
        CheckConstraint(
            "(status = 'waiting_for_dependency' AND waiting_reason = 'dependency') OR "
            "(status = 'waiting_for_resource' AND waiting_reason = 'resource') OR "
            "(status NOT IN ('waiting_for_dependency', 'waiting_for_resource') "
            "AND waiting_reason IS NULL)",
            name="ck_harness_outbox_waiting_reason",
        ),
        CheckConstraint(
            "(status = 'publishing' AND claim_token IS NOT NULL "
            "AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
            "(status <> 'publishing' AND claim_token IS NULL "
            "AND lease_owner IS NULL AND lease_expires_at IS NULL)",
            name="ck_harness_outbox_fencing",
        ),
        CheckConstraint(
            "(status = 'published' AND published_at IS NOT NULL) OR "
            "(status <> 'published' AND published_at IS NULL)",
            name="ck_harness_outbox_published_at",
        ),
        Index(
            "ix_harness_outbox_dispatch",
            "status",
            "available_at",
            "lease_expires_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    dedupe_key: Mapped[str] = mapped_column(String(300), index=True)
    event_type: Mapped[str] = mapped_column(String(160), index=True)
    aggregate_type: Mapped[str] = mapped_column(String(100), index=True)
    aggregate_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column("payload", JsonType, default=dict)
    status: Mapped[str] = mapped_column(
        String(30),
        default="pending",
        server_default=text("'pending'"),
        index=True,
    )
    pending_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        index=True,
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    claim_token: Mapped[str | None] = mapped_column(String(160), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    waiting_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ScientificEvidence(Base):
    """可审计的结构化证据，允许关联新对象及旧 Artifact。"""

    __tablename__ = "scientific_evidence"
    __table_args__ = (
        CheckConstraint(
            "goal_id IS NOT NULL OR skill_execution_id IS NOT NULL "
            "OR scientific_task_id IS NOT NULL OR attempt_id IS NOT NULL "
            "OR artifact_id IS NOT NULL",
            name="ck_harness_evidence_has_owner",
        ),
        Index("ix_harness_evidence_goal_type", "goal_id", "evidence_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        index=True,
    )
    goal_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("research_goals.id"),
        nullable=True,
        index=True,
    )
    skill_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("skill_executions.id"),
        nullable=True,
        index=True,
    )
    scientific_task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("scientific_tasks.id"),
        nullable=True,
        index=True,
    )
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("execution_attempts.id"),
        nullable=True,
        index=True,
    )
    artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("artifacts.id"),
        nullable=True,
        index=True,
    )
    # wzf：多产物证据使用显式 JSON 数组；artifact_id 保留为旧版单产物兼容列。
    artifact_ids: Mapped[list[str]] = mapped_column(
        "artifact_ids",
        JsonType,
        default=list,
    )
    evidence_type: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[str] = mapped_column(
        String(40),
        default="unreviewed",
        server_default=text("'unreviewed'"),
        index=True,
    )
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    statement: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_data_json: Mapped[dict[str, Any]] = mapped_column(
        "structured_data",
        JsonType,
        default=dict,
    )
    source_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    sufficient: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    reviewer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JsonType,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ProvenanceEdge(Base):
    """多态来源关系；端点类型与规范标识符成对保存，不伪造跨表外键。"""

    __tablename__ = "provenance_edges"
    __table_args__ = (
        UniqueConstraint(
            "source_type",
            "source_id",
            "target_type",
            "target_id",
            "relation_type",
            name="uq_harness_provenance_edge",
        ),
        Index("ix_harness_provenance_source", "source_type", "source_id"),
        Index("ix_harness_provenance_target", "target_type", "target_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        index=True,
    )
    source_type: Mapped[str] = mapped_column(String(100), index=True)
    source_id: Mapped[str] = mapped_column(String(240), index=True)
    target_type: Mapped[str] = mapped_column(String(100), index=True)
    target_id: Mapped[str] = mapped_column(String(240), index=True)
    relation_type: Mapped[str] = mapped_column(String(120), index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JsonType,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


# wzf：候选、评分、结构预测和策略反馈使用独立领域表，避免把旧 Candidate/StrategyPolicy
# 的 ResearchRun 外键继续扩散到新 Harness；这些表只保存来源 ID，不改变旧表结构。
class CandidateSet(Base):
    """同一目标、分子类别、迭代和生成配置下可比较的候选集合。"""

    __tablename__ = "candidate_sets"
    __table_args__ = (
        UniqueConstraint(
            "skill_execution_id",
            "molecule_class",
            "iteration",
            "generation_config_hash",
            name="uq_harness_candidate_set_skill_molecule_iteration_config",
        ),
        Index(
            "ix_harness_candidate_set_goal_molecule_iteration",
            "goal_id",
            "molecule_class",
            "iteration",
        ),
        CheckConstraint(
            "iteration >= 0",
            name="ck_harness_candidate_set_iteration_nonnegative",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'cancelled', 'unverified')",
            name="ck_harness_candidate_set_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    goal_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("research_goals.id"),
        index=True,
    )
    skill_execution_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("skill_executions.id"),
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        index=True,
    )
    molecule_class: Mapped[str] = mapped_column(String(120), index=True)
    iteration: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), index=True)
    generation_config_json: Mapped[dict[str, Any]] = mapped_column(
        "generation_config",
        JsonType,
        default=dict,
    )
    generation_config_hash: Mapped[str] = mapped_column(String(128), index=True)
    generator: Mapped[str] = mapped_column(String(160), index=True)
    generator_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    generation_task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("scientific_tasks.id"),
        nullable=True,
        index=True,
    )
    parent_set_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("candidate_sets.id"),
        nullable=True,
        index=True,
    )
    legacy_candidate_track_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        nullable=True,
        index=True,
    )
    summary_json: Mapped[dict[str, Any]] = mapped_column("summary", JsonType, default=dict)
    status: Mapped[str] = mapped_column(
        String(40),
        default="pending",
        server_default=text("'pending'"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ScientificCandidate(Base):
    """具有稳定表示和可追溯生成来源的计算候选；不代表实验验证结果。"""

    __tablename__ = "scientific_candidates"
    __table_args__ = (
        UniqueConstraint(
            "candidate_set_id",
            "representation_hash",
            name="uq_harness_candidate_set_representation_hash",
        ),
        Index(
            "ix_harness_candidate_goal_molecule_status",
            "goal_id",
            "status",
        ),
        CheckConstraint(
            "length > 0",
            name="ck_harness_scientific_candidate_length_positive",
        ),
        CheckConstraint(
            "status IN ('generated', 'active', 'scored', 'selected', 'rejected', "
            "'superseded', 'unverified')",
            name="ck_harness_scientific_candidate_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    candidate_set_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("candidate_sets.id"),
        index=True,
    )
    goal_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("research_goals.id"),
        index=True,
    )
    skill_execution_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("skill_executions.id"),
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        index=True,
    )
    target_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("scientific_targets.id"),
        nullable=True,
        index=True,
    )
    representation: Mapped[str] = mapped_column(Text)
    representation_hash: Mapped[str] = mapped_column(String(128), index=True)
    length: Mapped[int] = mapped_column(Integer)
    generator: Mapped[str] = mapped_column(String(160), index=True)
    generator_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    generation_task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("scientific_tasks.id"),
        nullable=True,
        index=True,
    )
    generation_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("execution_attempts.id"),
        nullable=True,
        index=True,
    )
    raw_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("artifacts.id"),
        nullable=True,
        index=True,
    )
    parent_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("scientific_candidates.id"),
        nullable=True,
        index=True,
    )
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parameter_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    generation_metrics_json: Mapped[dict[str, Any]] = mapped_column(
        "generation_metrics",
        JsonType,
        default=dict,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JsonType, default=dict)
    status: Mapped[str] = mapped_column(
        String(40),
        default="generated",
        server_default=text("'generated'"),
        index=True,
    )
    legacy_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ScoreRun(Base):
    """版本化评分配置在一个候选集上的不可变执行快照。"""

    __tablename__ = "score_runs"
    __table_args__ = (
        UniqueConstraint(
            "candidate_set_id",
            "config_hash",
            "input_membership_hash",
            name="uq_harness_score_run_set_config_membership",
        ),
        Index("ix_harness_score_run_goal_status", "goal_id", "status"),
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled', 'unverified')",
            name="ck_harness_score_run_status",
        ),
        CheckConstraint(
            "candidate_count >= 0 AND scored_count >= 0 AND selected_count >= 0 "
            "AND failed_count >= 0",
            name="ck_harness_score_run_counts_nonnegative",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    candidate_set_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("candidate_sets.id"),
        index=True,
    )
    goal_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("research_goals.id"),
        index=True,
    )
    skill_execution_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("skill_executions.id"),
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        index=True,
    )
    config_version: Mapped[str] = mapped_column(String(120), index=True)
    config_json: Mapped[dict[str, Any]] = mapped_column("config", JsonType, default=dict)
    config_hash: Mapped[str] = mapped_column(String(128), index=True)
    input_membership_hash: Mapped[str] = mapped_column(String(128), index=True)
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(
        String(40),
        default="queued",
        server_default=text("'queued'"),
        index=True,
    )
    candidate_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    scored_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    selected_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    failed_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    error_type: Mapped[str | None] = mapped_column(String(160), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    legacy_snapshot_key: Mapped[str | None] = mapped_column(
        String(300),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class CandidateScoreResult(Base):
    """一个候选在一个 ScoreRun 中的指标、排名和筛选结果。"""

    __tablename__ = "candidate_score_results"
    __table_args__ = (
        UniqueConstraint(
            "score_run_id",
            "candidate_id",
            name="uq_harness_candidate_score_run_candidate",
        ),
        Index("ix_harness_candidate_score_selection", "score_run_id", "selection_status"),
        CheckConstraint(
            "rank IS NULL OR rank > 0",
            name="ck_harness_candidate_score_rank_positive",
        ),
        CheckConstraint(
            "selection_status IN ('pending', 'qualified', 'selected', 'rejected', "
            "'superseded', 'unverified')",
            name="ck_harness_candidate_score_selection_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    score_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("score_runs.id"),
        index=True,
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("scientific_candidates.id"),
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        index=True,
    )
    raw_metrics_json: Mapped[dict[str, Any]] = mapped_column("raw_metrics", JsonType, default=dict)
    normalized_metrics_json: Mapped[dict[str, Any]] = mapped_column(
        "normalized_metrics",
        JsonType,
        default=dict,
    )
    total_score: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    selection_status: Mapped[str] = mapped_column(
        String(40),
        default="pending",
        server_default=text("'pending'"),
        index=True,
    )
    selection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("scientific_evidence.id"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class StructurePredictionRun(Base):
    """候选结构预测的一次请求、资源状态和证据归一化事实。"""

    __tablename__ = "structure_prediction_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_harness_structure_prediction_idempotency"),
        CheckConstraint(
            "status IN ('queued', 'running', 'waiting_for_resource', 'blocked', "
            "'missing_artifact', 'succeeded', 'failed', 'cancelled')",
            name="ck_harness_structure_prediction_status",
        ),
        Index("ix_harness_structure_prediction_candidate_status", "candidate_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("scientific_candidates.id"),
        index=True,
    )
    target_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("scientific_targets.id"),
        index=True,
    )
    goal_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("research_goals.id"),
        index=True,
    )
    skill_execution_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("skill_executions.id"),
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        index=True,
    )
    scientific_task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("scientific_tasks.id"),
        nullable=True,
        index=True,
    )
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("execution_attempts.id"),
        nullable=True,
        index=True,
    )
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("scientific_evidence.id"),
        nullable=True,
        index=True,
    )
    request_config_json: Mapped[dict[str, Any]] = mapped_column(
        "request_config",
        JsonType,
        default=dict,
    )
    request_config_hash: Mapped[str] = mapped_column(String(128), index=True)
    model: Mapped[str] = mapped_column(String(160), index=True)
    model_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    structure_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("artifacts.id"),
        nullable=True,
        index=True,
    )
    result_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("artifacts.id"),
        nullable=True,
        index=True,
    )
    resource_json: Mapped[dict[str, Any]] = mapped_column("resource", JsonType, default=dict)
    resource_usage_json: Mapped[dict[str, Any]] = mapped_column(
        "resource_usage",
        JsonType,
        default=dict,
    )
    status: Mapped[str] = mapped_column(
        String(40),
        default="queued",
        server_default=text("'queued'"),
        index=True,
    )
    error_type: Mapped[str | None] = mapped_column(String(160), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    legacy_af3_task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        nullable=True,
        index=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(300), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DecisionPolicy(Base):
    """策略在某个作用域和版本上的不可变参数快照。"""

    __tablename__ = "decision_policies"
    __table_args__ = (
        UniqueConstraint(
            "scope_type",
            "scope_key",
            "version",
            name="uq_harness_decision_policy_scope_version",
        ),
        CheckConstraint(
            "status IN ('draft', 'active', 'disabled', 'retired')",
            name="ck_harness_decision_policy_status",
        ),
        CheckConstraint(
            "version > 0",
            name="ck_harness_decision_policy_version_positive",
        ),
        Index("ix_harness_decision_policy_scope_enabled", "scope_type", "scope_key", "enabled"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    scope_type: Mapped[str] = mapped_column(String(100), index=True)
    scope_key: Mapped[str] = mapped_column(String(240), index=True)
    goal_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("research_goals.id"),
        nullable=True,
        index=True,
    )
    skill_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    skill_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("skill_executions.id"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=True,
        index=True,
    )
    algorithm: Mapped[str] = mapped_column(String(120), index=True)
    family: Mapped[str] = mapped_column(String(120), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"), index=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text("true"),
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(40),
        default="active",
        server_default=text("'active'"),
        index=True,
    )
    parameters_json: Mapped[dict[str, Any]] = mapped_column("parameters", JsonType, default=dict)
    q_table_json: Mapped[dict[str, Any]] = mapped_column("q_table", JsonType, default=dict)
    action_mask_json: Mapped[dict[str, Any]] = mapped_column("action_mask", JsonType, default=dict)
    config_json: Mapped[dict[str, Any]] = mapped_column("config", JsonType, default=dict)
    metrics_json: Mapped[dict[str, Any]] = mapped_column("metrics", JsonType, default=dict)
    config_hash: Mapped[str] = mapped_column(String(128), index=True)
    legacy_policy_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class StrategyFeedback(Base):
    """针对真实 Agent 决策及其结果的可去重反馈，不更新候选或基础模型参数。"""

    __tablename__ = "strategy_feedback"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_harness_strategy_feedback_dedupe_key"),
        Index("ix_harness_strategy_feedback_policy_status", "policy_id", "status"),
        CheckConstraint(
            "policy_version > 0",
            name="ck_harness_strategy_feedback_policy_version_positive",
        ),
        CheckConstraint(
            "status IN ('recorded', 'applied', 'rejected', 'superseded', 'unverified')",
            name="ck_harness_strategy_feedback_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    policy_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("decision_policies.id"),
        index=True,
    )
    policy_version: Mapped[int] = mapped_column(Integer, index=True)
    goal_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("research_goals.id"),
        nullable=True,
        index=True,
    )
    skill_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    skill_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("skill_executions.id"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        index=True,
    )
    turn_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("interaction_turns.id"),
        nullable=True,
        index=True,
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("session_messages.id"),
        nullable=True,
        index=True,
    )
    scientific_task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("scientific_tasks.id"),
        nullable=True,
        index=True,
    )
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("execution_attempts.id"),
        nullable=True,
        index=True,
    )
    evidence_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("scientific_evidence.id"),
        nullable=True,
        index=True,
    )
    state_key: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(String(120), index=True)
    reward: Mapped[float] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_state_key: Mapped[str] = mapped_column(Text)
    q_before: Mapped[float | None] = mapped_column(Float, nullable=True)
    q_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome_json: Mapped[dict[str, Any]] = mapped_column("outcome", JsonType, default=dict)
    tool_call_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    status: Mapped[str] = mapped_column(
        String(40),
        default="recorded",
        server_default=text("'recorded'"),
        index=True,
    )
    dedupe_key: Mapped[str] = mapped_column(String(300), index=True)
    legacy_transition_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


__all__ = [
    "CapabilityInvocation",
    "CandidateScoreResult",
    "CandidateSet",
    "DecisionPolicy",
    "ExecutionAttempt",
    "HarnessOutboxEvent",
    "InteractionTurn",
    "JsonType",
    "ProvenanceEdge",
    "ResearchGoal",
    "ResearchSession",
    "SessionGoalLink",
    "ScientificEvidence",
    "ScientificCandidate",
    "ScientificTarget",
    "ScientificTask",
    "ScoreRun",
    "SessionMessage",
    "SkillExecution",
    "StrategyFeedback",
    "StructurePredictionRun",
    "TaskGraph",
    "TaskDependency",
    "WorkflowCheckpoint",
    "new_uuid",
    "now_utc",
    "stable_uuid5",
]
