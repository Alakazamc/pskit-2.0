"""新 Harness 只读投影 API 的稳定响应契约。

投影只暴露持久化事实和离散状态，不包含百分比进度、密钥、内部路径或
客户端可伪造的 ``research_run_id`` 前置条件。旧 ResearchRun API 仍由旧
路由维护，这些模式只服务于新 Harness 的会话、目标、Skill 和任务图。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class HarnessSessionProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str | None = None
    status: str
    created_at: str
    updated_at: str


class HarnessGoalProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    objective: str
    status: str
    legacy_research_run_id: str | None = None
    created_at: str
    updated_at: str


class HarnessSkillExecutionProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    goal_id: str
    skill_id: str
    skill_version: str
    status: str
    plan_version: int = Field(ge=1)
    legacy_research_run_id: str | None = None
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None


class HarnessAttemptProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    task_id: str
    attempt_no: int = Field(ge=1)
    status: str
    error_type: str | None = None
    error_message: str | None = None
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None


class HarnessTaskProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    task_key: str
    name: str
    task_type: str
    status: str
    state: str
    reason: str
    dependency_ids: tuple[str, ...] = ()
    attempt: HarnessAttemptProjection | None = None


class HarnessTaskGraphProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    session_id: str
    goal_id: str | None = None
    skill_execution_id: str | None = None
    name: str
    status: str
    revision: int = Field(ge=1)
    valid: bool
    issues: tuple[str, ...] = ()
    ready_task_ids: tuple[str, ...] = ()
    blocked_task_ids: tuple[str, ...] = ()
    tasks: tuple[HarnessTaskProjection, ...] = ()


class HarnessEvidenceProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    evidence_type: str
    status: str
    title: str | None = None
    sufficient: bool
    artifact_ids: tuple[str, ...] = ()
    source_uri: str | None = None
    content_hash: str | None = None
    created_at: str


class HarnessSessionProjectionResponse(BaseModel):
    """一个普通会话的完整新 Harness 只读视图。

    ``research_run_id`` 不在顶层，也不是请求参数；旧迁移对象仅作为每个
    Goal/Skill 的可选兼容字段展示，普通对话可完全没有该字段。
    """

    model_config = ConfigDict(extra="forbid")

    session: HarnessSessionProjection
    goals: tuple[HarnessGoalProjection, ...] = ()
    skill_executions: tuple[HarnessSkillExecutionProjection, ...] = ()
    task_graphs: tuple[HarnessTaskGraphProjection, ...] = ()
    evidence: tuple[HarnessEvidenceProjection, ...] = ()


class HarnessGraphProjectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graph: HarnessTaskGraphProjection
    evidence: tuple[HarnessEvidenceProjection, ...] = ()


__all__ = [
    "HarnessAttemptProjection",
    "HarnessEvidenceProjection",
    "HarnessGoalProjection",
    "HarnessGraphProjectionResponse",
    "HarnessSessionProjection",
    "HarnessSessionProjectionResponse",
    "HarnessSkillExecutionProjection",
    "HarnessTaskGraphProjection",
    "HarnessTaskProjection",
]
