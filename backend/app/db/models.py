import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.db.base import Base


JsonType = JSON().with_variant(JSONB, "postgresql")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    """兼容 SQLite 返回的无时区时间，并统一为 UTC。"""

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(20), default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sessions: Mapped[list["AuthSession"]] = relationship(back_populates="user")


class SystemState(Base):
    """跨进程共享的少量系统级幂等状态。"""

    __tablename__ = "system_state"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value_json: Mapped[dict] = mapped_column("value", JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class AuthRateLimitBucket(Base):
    """Database-backed fixed-window counters shared by every Web process."""

    __tablename__ = "auth_rate_limit_buckets"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    window_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(120), index=True)
    target_type: Mapped[str] = mapped_column(String(80))
    target_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    detail_json: Mapped[dict] = mapped_column("detail", JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    session_hash: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(80), nullable=True)

    user: Mapped[User] = relationship(back_populates="sessions")


class AgentSession(Base):
    __tablename__ = "agent_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentMessage(Base):
    __tablename__ = "agent_messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("agent_sessions.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(30))
    content: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column("metadata", JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


# wzf：AgentTurn 为客户端幂等轮次和会话级互斥的持久化事实源，避免断线重发造成重复科研任务。
class AgentTurn(Base):
    __tablename__ = "agent_turns"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "session_id",
            "client_turn_id",
            name="uq_agent_turn_user_session_client",
        ),
        UniqueConstraint("active_session_key", name="uq_agent_turn_active_session"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    client_turn_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("agent_sessions.id"),
        index=True,
    )
    research_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("research_runs.id"),
        nullable=True,
        index=True,
    )
    user_message_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("agent_messages.id"),
        index=True,
    )
    assistant_message_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("agent_messages.id"),
        nullable=True,
        index=True,
    )
    request_hash: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(40), default="queued", index=True)
    active_session_key: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(200), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    attempt_no: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# wzf：科研运行独立于聊天会话，候选按 RNA 与多肽轨道持久化，避免异步结果只存在于聊天文本中。
class ResearchRun(Base):
    __tablename__ = "research_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("agent_sessions.id"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(Text, default="New Research Run")
    status: Mapped[str] = mapped_column(String(40), default="draft", index=True)
    current_stage: Mapped[str] = mapped_column(String(60), default="target_analysis", index=True)
    target_json: Mapped[dict] = mapped_column("target", JsonType, default=dict)
    stage_state_json: Mapped[dict] = mapped_column("stage_state", JsonType, default=dict)
    evidence_json: Mapped[list] = mapped_column("evidence", JsonType, default=list)
    policy_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class CandidateTrack(Base):
    __tablename__ = "candidate_tracks"
    __table_args__ = (
        UniqueConstraint("research_run_id", "track", name="uq_candidate_track_run_track"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    research_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("research_runs.id"),
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    track: Mapped[str] = mapped_column(String(20), index=True)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    current_iteration: Mapped[int] = mapped_column(Integer, default=0)
    score_config_json: Mapped[dict] = mapped_column("score_config", JsonType, default=dict)
    summary_json: Mapped[dict] = mapped_column("summary", JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Candidate(Base):
    __tablename__ = "candidates"
    __table_args__ = (
        UniqueConstraint("generation_task_id", "sequence", name="uq_candidate_generation_task_sequence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    research_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("research_runs.id"),
        index=True,
    )
    candidate_track_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("candidate_tracks.id"),
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    schema_version: Mapped[str] = mapped_column(String(40), default="1.0")
    track: Mapped[str] = mapped_column(String(20), index=True)
    sequence: Mapped[str] = mapped_column(Text)
    sequence_length: Mapped[int] = mapped_column(Integer)
    generator_name: Mapped[str] = mapped_column(String(120))
    generator_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    generation_task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("tasks.id"),
        nullable=True,
        index=True,
    )
    raw_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("artifacts.id"),
        nullable=True,
    )
    parent_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("candidates.id"),
        nullable=True,
        index=True,
    )
    iteration: Mapped[int] = mapped_column(Integer, default=0, index=True)
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parameters_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    raw_metrics_json: Mapped[dict] = mapped_column("raw_metrics", JsonType, default=dict)
    normalized_metrics_json: Mapped[dict] = mapped_column("normalized_metrics", JsonType, default=dict)
    total_score: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    score_config_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    selection_status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    selection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    af3_task_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("tasks.id"),
        nullable=True,
        index=True,
    )
    af3_status: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    af3_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("artifacts.id"),
        nullable=True,
    )
    metadata_json: Mapped[dict] = mapped_column("metadata", JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ResearchTaskLink(Base):
    __tablename__ = "research_task_links"
    __table_args__ = (UniqueConstraint("task_id", name="uq_research_task_link_task"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    research_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("research_runs.id"),
        index=True,
    )
    task_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tasks.id"), index=True)
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("candidates.id"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(60), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class StrategyPolicy(Base):
    __tablename__ = "strategy_policies"
    __table_args__ = (
        UniqueConstraint("research_run_id", name="uq_strategy_policy_research_run"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    research_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("research_runs.id"),
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(default=True)
    alpha: Mapped[float] = mapped_column(Float, default=0.2)
    gamma: Mapped[float] = mapped_column(Float, default=0.8)
    epsilon: Mapped[float] = mapped_column(Float, default=0.0)
    q_table_json: Mapped[dict] = mapped_column("q_table", JsonType, default=dict)
    action_mask_json: Mapped[dict] = mapped_column("action_mask", JsonType, default=dict)
    metrics_json: Mapped[dict] = mapped_column("metrics", JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class StrategyTransition(Base):
    __tablename__ = "strategy_transitions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    policy_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("strategy_policies.id"), index=True)
    research_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("research_runs.id"),
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    state_key: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(String(120), index=True)
    reward: Mapped[float] = mapped_column(Float)
    next_state_key: Mapped[str] = mapped_column(Text)
    q_before: Mapped[float] = mapped_column(Float)
    q_after: Mapped[float] = mapped_column(Float)
    outcome_json: Mapped[dict] = mapped_column("outcome", JsonType, default=dict)
    policy_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    tool_call_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    task_type: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(40), default="queued", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    input_json: Mapped[dict] = mapped_column("input", JsonType, default=dict)
    output_json: Mapped[dict] = mapped_column("output", JsonType, default=dict)
    error_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaskExecutionLease(Base):
    """任务执行租约；独立表避免为既有 Task 表做破坏性迁移。"""

    __tablename__ = "task_execution_leases"

    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("tasks.id"),
        primary_key=True,
    )
    lease_token: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    lease_owner: Mapped[str] = mapped_column(String(200))
    attempt_no: Mapped[int] = mapped_column(Integer, default=1)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class TaskRetry(Base):
    """记录不可变任务尝试之间的一对一重试链。"""

    __tablename__ = "task_retries"
    __table_args__ = (
        UniqueConstraint("child_task_id", name="uq_task_retry_child"),
        UniqueConstraint(
            "parent_task_id",
            "client_retry_id",
            name="uq_task_retry_parent_client",
        ),
    )

    parent_task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("tasks.id"),
        primary_key=True,
    )
    child_task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("tasks.id"),
        index=True,
    )
    client_retry_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    reason: Mapped[str] = mapped_column(String(80), default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(80), index=True)
    storage_backend: Mapped[str] = mapped_column(String(40), default="local")
    object_key: Mapped[str] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(Text)
    mime_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class ServiceHeartbeat(Base):
    __tablename__ = "service_heartbeats"

    name: Mapped[str] = mapped_column(String(80), primary_key=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    metadata_json: Mapped[dict] = mapped_column("metadata", JsonType, default=dict)


class RagIndexState(Base):
    __tablename__ = "rag_index_state"

    collection: Mapped[str] = mapped_column(String(120), primary_key=True)
    knowledge_hash: Mapped[str] = mapped_column(String(128))
    embedding_model: Mapped[str] = mapped_column(String(160))
    reranker_model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
