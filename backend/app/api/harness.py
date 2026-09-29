"""受保护的 Harness 运行时接线探针。

该 API 只返回服务端资格判定摘要，不执行科学工具、不返回收据原文、密钥
或内部路径。默认结果是旧兼容路线，便于在 feature flag 关闭时安全回滚。
"""

from __future__ import annotations

from typing import Any, Iterable
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.models import AgentSession, User
from app.db.harness_models import (
    ExecutionAttempt,
    ResearchGoal,
    ResearchSession,
    ScientificEvidence,
    ScientificTask,
    SessionGoalLink,
    SkillExecution,
    TaskDependency,
    TaskGraph,
)
from app.db.session import get_db
from app.harness.projection import (
    AttemptFact,
    DependencyFact,
    GraphFact,
    TaskFact,
    TaskGraphProjection,
    project_task_graph,
)
from app.harness.projection_bridge import (
    ProjectionBridgeError,
    ProjectionSessionRef,
    resolve_projection_session,
)
from app.harness.aptamer_service import AptamerSkillService
from app.harness.runtime_wiring import (
    LEGACY_ROUTE,
    RuntimeWiringContext,
    resolve_runtime_wiring,
)
from app.schemas.harness import (
    HarnessAttemptProjection,
    HarnessEvidenceProjection,
    HarnessGoalProjection,
    HarnessGraphProjectionResponse,
    HarnessSessionProjection,
    HarnessSessionProjectionResponse,
    HarnessSkillExecutionProjection,
    HarnessTaskGraphProjection,
    HarnessTaskProjection,
)


router = APIRouter(prefix="/api/harness", tags=["harness"])


class HarnessRouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_id: str = Field(min_length=1, max_length=120)
    session_id: UUID
    turn_id: UUID | None = None
    claims: dict[str, Any] | None = None


class HarnessRouteResponse(BaseModel):
    allowed: bool
    route: str
    capability_id: str
    correlation_id: str
    reason: str
    server_verified: bool


def get_harness_wiring_context() -> RuntimeWiringContext | None:
    """生产装配点；未显式覆盖依赖时默认关闭 Session-native 路线。"""

    return None


@router.post("/route", response_model=HarnessRouteResponse)
def inspect_route(
    payload: HarnessRouteRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    wiring_context: RuntimeWiringContext | None = Depends(get_harness_wiring_context),
) -> HarnessRouteResponse:
    session = db.get(AgentSession, payload.session_id)
    if session is None or session.user_id != user.id or session.archived_at is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )
    decision = resolve_runtime_wiring(
        payload.capability_id,
        user_id=user.id,
        session_id=session.id,
        turn_id=payload.turn_id,
        # 客户端 claims 只用于显式拒绝伪造 native，不参与授权。
        client_claims=payload.claims,
        context=wiring_context,
    )
    return HarnessRouteResponse(**decision.to_public_dict())


class AptamerPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: dict[str, Any] = Field(min_length=1)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=240)
    capability_inputs: dict[str, Any] | None = None
    # 旧字段仅作兼容输入；Aptamer Skill 不会隐式生成该字段。
    legacy_research_run_id: UUID | None = None


class AptamerPlanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    route: str
    reason: str
    handoff_status: str | None = None
    persisted: bool = False
    plan: dict[str, Any] | None = None
    intent: dict[str, Any] = Field(default_factory=lambda: {"count": 0, "entities": []})
    outbox: dict[str, Any] | None = None


def get_aptamer_skill_service() -> AptamerSkillService | None:
    """生产装配覆盖点；默认没有已进入 Harness UoW 的 native 服务。"""

    return None


def _aptamer_unavailable(reason: str) -> AptamerPlanResponse:
    return AptamerPlanResponse(
        status="unavailable",
        route=LEGACY_ROUTE,
        reason=reason,
        handoff_status=None,
        persisted=False,
        plan=None,
        intent={"count": 0, "entities": []},
        outbox=None,
    )


@router.post(
    "/sessions/{session_id}/skills/aptamer_closed_loop/plan",
    response_model=AptamerPlanResponse,
)
def plan_aptamer_closed_loop(
    session_id: UUID,
    payload: AptamerPlanRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    service: AptamerSkillService | None = Depends(get_aptamer_skill_service),
) -> AptamerPlanResponse:
    """受保护的 Aptamer 计划入口；默认只返回安全的 unavailable 摘要。

    生产装配必须通过依赖覆盖传入已进入 HarnessUnitOfWork 的服务。客户端不
    提供 activation/resource 证据，普通会话也不需要 research_run_id。
    """

    session = _load_owned_research_session(db, user, session_id)
    if service is None:
        return _aptamer_unavailable("runtime_unavailable")
    try:
        service_session = service.uow.session
        decision = service.plan_and_handoff(
            session=service_session,
            user_id=user.id,
            session_id=session.id,
            target=payload.target,
            idempotency_key=payload.idempotency_key,
            capability_inputs=payload.capability_inputs,
            legacy_research_run_id=(
                str(payload.legacy_research_run_id)
                if payload.legacy_research_run_id is not None
                else None
            ),
        )
    except Exception:
        # 不把数据库路径、事务或内部依赖错误回显给浏览器。
        return _aptamer_unavailable("runtime_unavailable")
    return AptamerPlanResponse(**decision.to_public_dict())


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _safe_error(value: str | None) -> str | None:
    """避免投影把服务器路径、命令行或凭证片段回传给浏览器。"""

    if not value:
        return None
    text = str(value).strip()
    if len(text) > 1000 or "/" in text or "\\" in text:
        return "execution failed; inspect controlled diagnostics"
    return text


def _load_owned_research_session(db: Session, user: User, session_id: UUID) -> ResearchSession:
    session = db.get(ResearchSession, session_id)
    if session is None or session.user_id != user.id or session.archived_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Harness session not found")
    return session


def _serialize_attempt(attempt: ExecutionAttempt) -> HarnessAttemptProjection:
    return HarnessAttemptProjection(
        id=str(attempt.id), task_id=str(attempt.scientific_task_id), attempt_no=attempt.attempt_no,
        status=attempt.status, error_type=attempt.error_type,
        error_message=_safe_error(attempt.error_message), created_at=attempt.created_at.isoformat(),
        updated_at=attempt.updated_at.isoformat(), started_at=_iso(attempt.started_at),
        finished_at=_iso(attempt.finished_at),
    )


def _project_graph(
    graph: TaskGraph,
    tasks: Iterable[ScientificTask],
    attempts: Iterable[ExecutionAttempt],
    dependencies: Iterable[TaskDependency],
) -> HarnessTaskGraphProjection:
    task_rows, attempt_rows, dependency_rows = tuple(tasks), tuple(attempts), tuple(dependencies)
    projected: TaskGraphProjection = project_task_graph(
        GraphFact(str(graph.id), str(graph.user_id), graph.status, graph.revision),
        (TaskFact(str(t.id), str(t.task_graph_id), str(t.user_id), t.task_key, t.status) for t in task_rows),
        (AttemptFact(str(a.id), str(a.scientific_task_id), str(a.user_id), a.status, a.attempt_no) for a in attempt_rows),
        (DependencyFact(str(d.task_graph_id), str(d.upstream_task_id), str(d.downstream_task_id), d.dependency_type, str(graph.user_id)) for d in dependency_rows),
    )
    task_by_id = {str(item.id): item for item in task_rows}
    attempt_by_id = {str(item.id): item for item in attempt_rows}
    todo_by_id = {item.task_id: item for item in projected.todo}
    serialized_tasks = []
    for task_id in sorted(task_by_id, key=lambda value: (task_by_id[value].task_key, value)):
        task, todo = task_by_id[task_id], todo_by_id.get(task_id)
        attempt = attempt_by_id.get(todo.attempt_id) if todo and todo.attempt_id else None
        serialized_tasks.append(HarnessTaskProjection(
            id=task_id, task_key=task.task_key, name=task.name, task_type=task.task_type,
            status=task.status, state=todo.state if todo else "blocked",
            reason=todo.reason if todo else "projection missing task state",
            dependency_ids=todo.dependency_ids if todo else (),
            attempt=_serialize_attempt(attempt) if attempt else None,
        ))
    return HarnessTaskGraphProjection(
        id=str(graph.id), session_id=str(graph.session_id),
        goal_id=str(graph.goal_id) if graph.goal_id else None,
        skill_execution_id=str(graph.skill_execution_id) if graph.skill_execution_id else None,
        name=graph.name, status=projected.status, revision=graph.revision,
        valid=projected.valid, issues=projected.issues,
        ready_task_ids=projected.ready_task_ids, blocked_task_ids=projected.blocked_task_ids,
        tasks=tuple(serialized_tasks),
    )


def _serialize_goal(goal: ResearchGoal) -> HarnessGoalProjection:
    return HarnessGoalProjection(
        id=str(goal.id), title=goal.title, objective=goal.objective, status=goal.status,
        legacy_research_run_id=str(goal.legacy_research_run_id) if goal.legacy_research_run_id else None,
        created_at=goal.created_at.isoformat(), updated_at=goal.updated_at.isoformat(),
    )


def _serialize_skill(skill: SkillExecution) -> HarnessSkillExecutionProjection:
    return HarnessSkillExecutionProjection(
        id=str(skill.id), goal_id=str(skill.goal_id), skill_id=skill.skill_id,
        skill_version=skill.skill_version, status=skill.status, plan_version=skill.plan_version,
        legacy_research_run_id=str(skill.legacy_research_run_id) if skill.legacy_research_run_id else None,
        created_at=skill.created_at.isoformat(), updated_at=skill.updated_at.isoformat(),
        started_at=_iso(skill.started_at), finished_at=_iso(skill.finished_at),
    )


def _serialize_evidence(item: ScientificEvidence) -> HarnessEvidenceProjection:
    artifact_ids = list(item.artifact_ids or [])
    if item.artifact_id and str(item.artifact_id) not in artifact_ids:
        artifact_ids.append(str(item.artifact_id))
    return HarnessEvidenceProjection(
        id=str(item.id), evidence_type=item.evidence_type, status=item.status,
        title=item.title, sufficient=item.sufficient,
        artifact_ids=tuple(str(value) for value in artifact_ids), source_uri=item.source_uri,
        content_hash=item.content_hash, created_at=item.created_at.isoformat(),
    )


def _owned_projection_context(db: Session, user: User, session: ResearchSession, *, graph_id: UUID | None = None):
    links = db.scalars(select(SessionGoalLink).where(SessionGoalLink.session_id == session.id, SessionGoalLink.user_id == user.id)).all()
    goal_ids = {item.goal_id for item in links}
    graphs_query = select(TaskGraph).where(TaskGraph.session_id == session.id, TaskGraph.user_id == user.id)
    if graph_id is not None:
        graphs_query = graphs_query.where(TaskGraph.id == graph_id)
    graphs = db.scalars(graphs_query.order_by(TaskGraph.created_at.asc())).all()
    goal_ids.update(item.goal_id for item in graphs if item.goal_id is not None)
    skill_ids = {item.skill_execution_id for item in graphs if item.skill_execution_id is not None}
    goals = db.scalars(select(ResearchGoal).where(ResearchGoal.user_id == user.id, ResearchGoal.id.in_(goal_ids)).order_by(ResearchGoal.created_at.asc())).all() if goal_ids else []
    skill_clauses = []
    if goal_ids:
        skill_clauses.append(SkillExecution.goal_id.in_(goal_ids))
    if skill_ids:
        skill_clauses.append(SkillExecution.id.in_(skill_ids))
    skills = db.scalars(select(SkillExecution).where(SkillExecution.user_id == user.id, or_(*skill_clauses)).order_by(SkillExecution.created_at.asc())).all() if skill_clauses else []
    graph_ids = {item.id for item in graphs}
    tasks = db.scalars(select(ScientificTask).where(ScientificTask.user_id == user.id, ScientificTask.task_graph_id.in_(graph_ids)).order_by(ScientificTask.created_at.asc())).all() if graph_ids else []
    task_ids = {item.id for item in tasks}
    attempts = db.scalars(select(ExecutionAttempt).where(ExecutionAttempt.user_id == user.id, ExecutionAttempt.scientific_task_id.in_(task_ids)).order_by(ExecutionAttempt.attempt_no.asc())).all() if task_ids else []
    dependencies = db.scalars(select(TaskDependency).where(TaskDependency.task_graph_id.in_(graph_ids))).all() if graph_ids else []
    evidence_clauses = []
    if goal_ids:
        evidence_clauses.append(ScientificEvidence.goal_id.in_(goal_ids))
    evidence_skill_ids = {item.id for item in skills}
    if evidence_skill_ids:
        evidence_clauses.append(ScientificEvidence.skill_execution_id.in_(evidence_skill_ids))
    if task_ids:
        evidence_clauses.append(ScientificEvidence.scientific_task_id.in_(task_ids))
    attempt_ids = {item.id for item in attempts}
    if attempt_ids:
        evidence_clauses.append(ScientificEvidence.attempt_id.in_(attempt_ids))
    evidence = db.scalars(select(ScientificEvidence).where(ScientificEvidence.user_id == user.id, or_(*evidence_clauses))).all() if evidence_clauses else []
    return goals, skills, graphs, tasks, attempts, dependencies, evidence


def _build_session_projection(db: Session, user: User, session: ResearchSession) -> HarnessSessionProjectionResponse:
    goals, skills, graphs, tasks, attempts, dependencies, evidence = _owned_projection_context(db, user, session)
    task_by_graph: dict[UUID, list[ScientificTask]] = {}
    attempt_by_task: dict[UUID, list[ExecutionAttempt]] = {}
    dependency_by_graph: dict[UUID, list[TaskDependency]] = {}
    for item in tasks:
        task_by_graph.setdefault(item.task_graph_id, []).append(item)
    for item in attempts:
        attempt_by_task.setdefault(item.scientific_task_id, []).append(item)
    for item in dependencies:
        dependency_by_graph.setdefault(item.task_graph_id, []).append(item)
    graph_projection = tuple(_project_graph(
        graph, task_by_graph.get(graph.id, []),
        [attempt for task in task_by_graph.get(graph.id, []) for attempt in attempt_by_task.get(task.id, [])],
        dependency_by_graph.get(graph.id, []),
    ) for graph in graphs)
    return HarnessSessionProjectionResponse(
        session=HarnessSessionProjection(id=str(session.id), title=session.title, status=session.status, created_at=session.created_at.isoformat(), updated_at=session.updated_at.isoformat()),
        goals=tuple(_serialize_goal(item) for item in goals),
        skill_executions=tuple(_serialize_skill(item) for item in skills),
        task_graphs=graph_projection,
        evidence=tuple(_serialize_evidence(item) for item in evidence),
    )


def _resolve_projection_reference_or_404(
    db: Session,
    user: User,
    requested_session_id: UUID,
) -> ProjectionSessionRef:
    """解析 ResearchSession/AgentSession；访问失败统一收敛为安全 404。"""

    try:
        reference = resolve_projection_session(db, user, requested_session_id)
    except ProjectionBridgeError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Harness session not found",
        ) from exc
    if reference is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Harness session not found",
        )
    return reference


def _build_unmapped_agent_projection(
    agent_session: AgentSession,
) -> HarnessSessionProjectionResponse:
    """普通 AgentSession 尚未迁移时返回空投影，不伪造 ResearchSession。"""

    return HarnessSessionProjectionResponse(
        session=HarnessSessionProjection(
            id=str(agent_session.id),
            title=agent_session.title,
            status="unmapped",
            created_at=agent_session.created_at.isoformat(),
            updated_at=agent_session.updated_at.isoformat(),
        ),
        goals=(),
        skill_executions=(),
        task_graphs=(),
        evidence=(),
    )


def _build_unmapped_graph_projection(graph: TaskGraph) -> HarnessGraphProjectionResponse:
    """图仍可被安全识别时，给未映射旧会话返回离散空投影。"""

    return HarnessGraphProjectionResponse(
        graph=HarnessTaskGraphProjection(
            id=str(graph.id),
            session_id=str(graph.session_id),
            goal_id=str(graph.goal_id) if graph.goal_id else None,
            skill_execution_id=(
                str(graph.skill_execution_id) if graph.skill_execution_id else None
            ),
            name=graph.name,
            status="unmapped",
            revision=graph.revision,
            valid=False,
            issues=("projection unavailable for unmapped AgentSession",),
            ready_task_ids=(),
            blocked_task_ids=(),
            tasks=(),
        ),
        evidence=(),
    )


@router.get("/sessions/{session_id}/projection", response_model=HarnessSessionProjectionResponse)
def get_session_projection(session_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> HarnessSessionProjectionResponse:
    """返回 session-native 只读事实投影；普通会话不需要 ResearchRun。"""

    reference = _resolve_projection_reference_or_404(db, user, session_id)
    if not reference.available:
        agent_id = reference.agent_session_id or reference.requested_session_id
        agent_session = db.get(AgentSession, agent_id)
        if agent_session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Harness session not found",
            )
        return _build_unmapped_agent_projection(agent_session)
    assert reference.projection_session_id is not None
    session = _load_owned_research_session(db, user, reference.projection_session_id)
    return _build_session_projection(db, user, session)


@router.get("/task-graphs/{graph_id}/projection", response_model=HarnessGraphProjectionResponse)
def get_task_graph_projection(graph_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> HarnessGraphProjectionResponse:
    graph = db.get(TaskGraph, graph_id)
    if graph is None or graph.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task graph not found")
    reference = _resolve_projection_reference_or_404(db, user, graph.session_id)
    if not reference.available:
        return _build_unmapped_graph_projection(graph)
    assert reference.projection_session_id is not None
    session = _load_owned_research_session(db, user, reference.projection_session_id)
    _goals, _skills, graphs, tasks, attempts, dependencies, evidence = _owned_projection_context(db, user, session, graph_id=graph.id)
    graph = graphs[0] if graphs else graph
    task_rows = [item for item in tasks if item.task_graph_id == graph.id]
    task_ids = {item.id for item in task_rows}
    return HarnessGraphProjectionResponse(
        graph=_project_graph(graph, task_rows, [item for item in attempts if item.scientific_task_id in task_ids], [item for item in dependencies if item.task_graph_id == graph.id]),
        evidence=tuple(_serialize_evidence(item) for item in evidence),
    )


__all__ = [
    "AptamerPlanRequest",
    "AptamerPlanResponse",
    "HarnessRouteRequest",
    "HarnessRouteResponse",
    "get_aptamer_skill_service",
    "get_harness_wiring_context",
    "inspect_route",
    "plan_aptamer_closed_loop",
    "router",
]
