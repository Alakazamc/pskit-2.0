"""TaskGraph 的只读状态投影。

投影只消费已持久化的 Graph、Task、Attempt 与 Dependency 事实，不会写回这些
真相表，也不会凭空生成百分比进度。输入不完整、跨图、跨用户、重复依赖或形成
环时，投影以 ``valid=False`` fail-closed，调用方应展示阻塞原因而不是猜测状态。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence


TERMINAL_TASK_STATES = frozenset({"succeeded", "failed", "cancelled"})
SUCCESS_TASK_STATES = frozenset({"succeeded"})
KNOWN_TASK_STATES = frozenset(
    {
        "planned",
        "pending",
        "queued",
        "blocked",
        "running",
        "waiting_for_resource",
        "waiting_for_dependency",
        "waiting_for_input",
        "waiting_for_approval",
        "succeeded",
        "failed",
        "cancelled",
        "timed_out",
        "interrupted",
        "rejected",
    }
)


class ProjectionError(ValueError):
    """图事实不满足投影安全约束。"""


def _value(obj: Any, name: str, *aliases: str, default: Any = None) -> Any:
    names = (name, *aliases)
    if isinstance(obj, Mapping):
        for candidate in names:
            if candidate in obj:
                return obj[candidate]
        return default
    for candidate in names:
        if hasattr(obj, candidate):
            return getattr(obj, candidate)
    return default


def _text(value: Any, field_name: str) -> str:
    if value is None or not str(value).strip():
        raise ProjectionError(f"{field_name} is missing")
    return str(value).strip()


@dataclass(frozen=True, slots=True)
class GraphFact:
    id: str
    user_id: str
    status: str = "planned"
    revision: int = 1


@dataclass(frozen=True, slots=True)
class TaskFact:
    id: str
    graph_id: str
    user_id: str
    task_key: str
    status: str = "planned"


@dataclass(frozen=True, slots=True)
class AttemptFact:
    id: str
    task_id: str
    user_id: str
    status: str
    attempt_no: int = 1


@dataclass(frozen=True, slots=True)
class DependencyFact:
    graph_id: str
    upstream_task_id: str
    downstream_task_id: str
    dependency_type: str = "required"
    user_id: str | None = None


@dataclass(frozen=True, slots=True)
class TodoProjection:
    task_id: str
    task_key: str
    state: str
    reason: str
    dependency_ids: tuple[str, ...] = ()
    attempt_id: str | None = None
    attempt_state: str | None = None


@dataclass(frozen=True, slots=True)
class BranchProjection:
    task_id: str
    task_key: str
    state: str
    blocked_by: tuple[str, ...] = ()
    todo_state: str = "not_started"


@dataclass(frozen=True, slots=True)
class TaskGraphProjection:
    graph_id: str
    user_id: str
    status: str
    valid: bool
    issues: tuple[str, ...] = ()
    todo: tuple[TodoProjection, ...] = ()
    branches: tuple[BranchProjection, ...] = ()
    ready_task_ids: tuple[str, ...] = ()
    blocked_task_ids: tuple[str, ...] = ()

    @property
    def has_false_progress(self) -> bool:
        """兼容审计语义：投影模型没有百分比字段，永远不生成假进度。"""

        return False


def _normalize_graph(value: Any) -> GraphFact:
    return GraphFact(
        id=_text(_value(value, "id", "graph_id"), "graph.id"),
        user_id=_text(_value(value, "user_id"), "graph.user_id"),
        status=str(_value(value, "status", default="planned") or "planned"),
        revision=int(_value(value, "revision", default=1) or 1),
    )


def _normalize_task(value: Any) -> TaskFact:
    return TaskFact(
        id=_text(_value(value, "id", "task_id"), "task.id"),
        graph_id=_text(_value(value, "graph_id", "task_graph_id"), "task.graph_id"),
        user_id=_text(_value(value, "user_id"), "task.user_id"),
        task_key=_text(_value(value, "task_key", "name", default="task"), "task.task_key"),
        status=str(_value(value, "status", default="planned") or "planned"),
    )


def _normalize_attempt(value: Any) -> AttemptFact:
    return AttemptFact(
        id=_text(_value(value, "id", "attempt_id"), "attempt.id"),
        task_id=_text(_value(value, "task_id", "scientific_task_id"), "attempt.task_id"),
        user_id=_text(_value(value, "user_id"), "attempt.user_id"),
        status=str(_value(value, "status", default="queued") or "queued"),
        attempt_no=int(_value(value, "attempt_no", default=1) or 1),
    )


def _normalize_dependency(value: Any) -> DependencyFact:
    return DependencyFact(
        graph_id=_text(_value(value, "graph_id", "task_graph_id"), "dependency.graph_id"),
        upstream_task_id=_text(
            _value(value, "upstream_task_id", "upstream_id"), "dependency.upstream_task_id"
        ),
        downstream_task_id=_text(
            _value(value, "downstream_task_id", "downstream_id"), "dependency.downstream_task_id"
        ),
        dependency_type=str(_value(value, "dependency_type", default="required") or "required"),
        user_id=(str(_value(value, "user_id")) if _value(value, "user_id") is not None else None),
    )


class TaskGraphProjector:
    """根据持久事实计算 Todo、分支和图终态。"""

    def project(
        self,
        graph: GraphFact | Mapping[str, Any] | Any,
        tasks: Iterable[TaskFact | Mapping[str, Any] | Any],
        attempts: Iterable[AttemptFact | Mapping[str, Any] | Any] = (),
        dependencies: Iterable[DependencyFact | Mapping[str, Any] | Any] = (),
    ) -> TaskGraphProjection:
        try:
            graph_fact = _normalize_graph(graph)
            task_facts = tuple(_normalize_task(item) for item in tasks)
            attempt_facts = tuple(_normalize_attempt(item) for item in attempts)
            dependency_facts = tuple(_normalize_dependency(item) for item in dependencies)
        except (ProjectionError, TypeError, ValueError) as exc:
            return TaskGraphProjection(
                graph_id=str(_value(graph, "id", "graph_id", default="unknown") or "unknown"),
                user_id=str(_value(graph, "user_id", default="unknown") or "unknown"),
                status="blocked",
                valid=False,
                issues=(str(exc),),
            )
        issues: list[str] = []
        if graph_fact.revision < 1:
            issues.append("graph revision must be positive")
        if len({task.id for task in task_facts}) != len(task_facts):
            issues.append("duplicate task id")
        by_id = {task.id: task for task in task_facts}
        if len({task.task_key for task in task_facts}) != len(task_facts):
            issues.append("duplicate task key")
        for task in task_facts:
            if task.graph_id != graph_fact.id:
                issues.append(f"task {task.id} belongs to another graph")
            if task.user_id != graph_fact.user_id:
                issues.append(f"task {task.id} ownership mismatch")
            if task.status not in KNOWN_TASK_STATES:
                issues.append(f"task {task.id} has unknown status {task.status}")
        attempts_by_task: dict[str, list[AttemptFact]] = {}
        seen_attempt_ids: set[str] = set()
        seen_task_attempt_numbers: set[tuple[str, int]] = set()
        for attempt in attempt_facts:
            if attempt.id in seen_attempt_ids:
                issues.append(f"duplicate attempt id {attempt.id}")
            seen_attempt_ids.add(attempt.id)
            if attempt.attempt_no < 1:
                issues.append(f"attempt {attempt.id} number must be positive")
            attempt_number_key = (attempt.task_id, attempt.attempt_no)
            if attempt_number_key in seen_task_attempt_numbers:
                issues.append(
                    f"duplicate attempt number {attempt.attempt_no} for task {attempt.task_id}"
                )
            seen_task_attempt_numbers.add(attempt_number_key)
            if attempt.task_id not in by_id:
                issues.append(f"attempt {attempt.id} references missing task {attempt.task_id}")
                continue
            if (
                attempt.user_id != graph_fact.user_id
                or by_id[attempt.task_id].user_id != graph_fact.user_id
            ):
                issues.append(f"attempt {attempt.id} ownership mismatch")
            if attempt.status not in KNOWN_TASK_STATES:
                issues.append(f"attempt {attempt.id} has unknown status {attempt.status}")
            attempts_by_task.setdefault(attempt.task_id, []).append(attempt)
        outgoing: dict[str, list[str]] = {task.id: [] for task in task_facts}
        incoming: dict[str, list[str]] = {task.id: [] for task in task_facts}
        seen_edges: set[tuple[str, str]] = set()
        for dependency in dependency_facts:
            if dependency.graph_id != graph_fact.id:
                issues.append("dependency belongs to another graph")
            if dependency.user_id is not None and dependency.user_id != graph_fact.user_id:
                issues.append("dependency ownership mismatch")
            if dependency.dependency_type != "required":
                issues.append(f"unsupported dependency type {dependency.dependency_type}")
                continue
            edge = (dependency.upstream_task_id, dependency.downstream_task_id)
            if edge in seen_edges:
                issues.append(f"duplicate dependency {edge[0]}->{edge[1]}")
                continue
            seen_edges.add(edge)
            if dependency.upstream_task_id not in by_id:
                issues.append(
                    f"dependency references missing upstream task {dependency.upstream_task_id}"
                )
                continue
            if dependency.downstream_task_id not in by_id:
                issues.append(
                    f"dependency references missing downstream task {dependency.downstream_task_id}"
                )
                continue
            if dependency.upstream_task_id == dependency.downstream_task_id:
                issues.append(f"self dependency {dependency.upstream_task_id}")
                continue
            outgoing[dependency.upstream_task_id].append(dependency.downstream_task_id)
            incoming[dependency.downstream_task_id].append(dependency.upstream_task_id)

        cycle_nodes = _cycle_nodes(tuple(by_id), outgoing)
        if cycle_nodes:
            issues.append("task graph contains cycle: " + ",".join(sorted(cycle_nodes)))
        if issues:
            return TaskGraphProjection(
                graph_id=graph_fact.id,
                user_id=graph_fact.user_id,
                status="blocked",
                valid=False,
                issues=tuple(dict.fromkeys(issues)),
            )

        latest_attempt: dict[str, AttemptFact] = {}
        for task_id, values in attempts_by_task.items():
            latest_attempt[task_id] = max(values, key=lambda item: (item.attempt_no, item.id))
        todo: list[TodoProjection] = []
        branches: list[BranchProjection] = []
        ready: list[str] = []
        blocked: list[str] = []
        projected: dict[str, tuple[str, str, tuple[str, ...]]] = {}
        for task_id in _topological_order(tuple(by_id), outgoing):
            task = by_id[task_id]
            attempt = latest_attempt.get(task.id)
            state = attempt.status if attempt is not None else task.status
            required_upstream = tuple(sorted(incoming[task.id]))
            failed_upstream = tuple(
                upstream
                for upstream in required_upstream
                if projected[upstream][0]
                in {
                    "failed",
                    "cancelled",
                    "timed_out",
                    "interrupted",
                    "blocked",
                    "rejected",
                    "blocked_dependency",
                }
            )
            incomplete_upstream = tuple(
                upstream
                for upstream in required_upstream
                if projected[upstream][0] not in SUCCESS_TASK_STATES
            )
            if failed_upstream:
                display_state = "blocked_dependency"
                reason = "required dependency failed"
            elif state in TERMINAL_TASK_STATES or state in {"timed_out", "interrupted"}:
                display_state = state
                reason = "task terminal state"
            elif incomplete_upstream:
                display_state = "waiting_for_dependency"
                reason = "required dependency not completed"
            elif state in {"planned", "pending"}:
                display_state = "ready"
                reason = "dependencies satisfied"
            else:
                display_state = state
                reason = "persisted task/attempt state"
            projected[task.id] = (display_state, reason, failed_upstream)

        for task in sorted(task_facts, key=lambda item: (item.task_key, item.id)):
            attempt = latest_attempt.get(task.id)
            required_upstream = tuple(sorted(incoming[task.id]))
            display_state, reason, failed_upstream = projected[task.id]
            if display_state == "ready":
                ready.append(task.id)
            if display_state in {
                "blocked",
                "rejected",
                "blocked_dependency",
                "waiting_for_dependency",
            }:
                blocked.append(task.id)
            todo.append(
                TodoProjection(
                    task_id=task.id,
                    task_key=task.task_key,
                    state=display_state,
                    reason=reason,
                    dependency_ids=required_upstream,
                    attempt_id=attempt.id if attempt else None,
                    attempt_state=attempt.status if attempt else None,
                )
            )
            branches.append(
                BranchProjection(
                    task_id=task.id,
                    task_key=task.task_key,
                    state=display_state,
                    blocked_by=failed_upstream,
                    todo_state="ready" if display_state == "ready" else display_state,
                )
            )
        graph_status = _graph_status(todo)
        return TaskGraphProjection(
            graph_id=graph_fact.id,
            user_id=graph_fact.user_id,
            status=graph_status,
            valid=True,
            todo=tuple(todo),
            branches=tuple(branches),
            ready_task_ids=tuple(ready),
            blocked_task_ids=tuple(blocked),
        )


def _effective_state(task: TaskFact, attempt: AttemptFact | None) -> str:
    return attempt.status if attempt is not None else task.status


def _graph_status(todo: Sequence[TodoProjection]) -> str:
    states = {item.state for item in todo}
    if not todo:
        return "planned"
    if states <= {"succeeded"}:
        return "succeeded"
    if states & {"running", "queued", "ready"}:
        return "running"
    if states & {"waiting_for_approval", "waiting_for_resource", "waiting_for_input"}:
        if "waiting_for_approval" in states:
            return "waiting_for_approval"
        return "waiting_for_resource" if "waiting_for_resource" in states else "waiting_for_input"
    if states <= {
        "succeeded",
        "failed",
        "cancelled",
        "timed_out",
        "interrupted",
        "blocked",
        "rejected",
        "blocked_dependency",
        "waiting_for_dependency",
    }:
        if (
            "blocked" in states
            or "rejected" in states
            or "blocked_dependency" in states
            or "waiting_for_dependency" in states
        ):
            return "blocked"
        if "failed" in states or "timed_out" in states:
            return "failed"
        if "interrupted" in states:
            return "interrupted"
        if "cancelled" in states:
            return "cancelled"
        return "succeeded"
    return "running"


def _topological_order(
    nodes: Sequence[str], outgoing: Mapping[str, Sequence[str]]
) -> tuple[str, ...]:
    """返回稳定拓扑序；调用前已完成环检测，因此结果应覆盖全部节点。"""

    indegree = {node: 0 for node in nodes}
    for source in nodes:
        for target in outgoing.get(source, ()):
            indegree[target] = indegree.get(target, 0) + 1
    queue = sorted(node for node, degree in indegree.items() if degree == 0)
    ordered: list[str] = []
    while queue:
        node = queue.pop(0)
        ordered.append(node)
        for target in sorted(outgoing.get(node, ())):
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
        queue.sort()
    return tuple(ordered)


def _cycle_nodes(nodes: Sequence[str], outgoing: Mapping[str, Sequence[str]]) -> set[str]:
    indegree = {node: 0 for node in nodes}
    for source in nodes:
        for target in outgoing.get(source, ()):
            indegree[target] = indegree.get(target, 0) + 1
    queue = [node for node, degree in indegree.items() if degree == 0]
    consumed = 0
    while queue:
        node = queue.pop()
        consumed += 1
        for target in outgoing.get(node, ()):
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    return (
        {node for node, degree in indegree.items() if degree > 0}
        if consumed != len(indegree)
        else set()
    )


def project_task_graph(
    graph: GraphFact | Mapping[str, Any] | Any,
    tasks: Iterable[TaskFact | Mapping[str, Any] | Any],
    attempts: Iterable[AttemptFact | Mapping[str, Any] | Any] = (),
    dependencies: Iterable[DependencyFact | Mapping[str, Any] | Any] = (),
) -> TaskGraphProjection:
    """函数式入口，便于 API projector 适配器使用。"""

    return TaskGraphProjector().project(graph, tasks, attempts, dependencies)


# 领域名别名，方便 ORM adapter 按现有模型命名接入而不复制投影逻辑。
TaskGraphFact = GraphFact
ScientificTaskFact = TaskFact
ExecutionAttemptFact = AttemptFact
TaskDependencyFact = DependencyFact
TaskGraphState = TaskGraphProjection
TaskTodo = TodoProjection
BranchState = BranchProjection
project_graph = project_task_graph


__all__ = [
    "AttemptFact",
    "BranchProjection",
    "DependencyFact",
    "GraphFact",
    "ProjectionError",
    "TaskFact",
    "TaskGraphProjection",
    "TaskGraphProjector",
    "TaskGraphFact",
    "ScientificTaskFact",
    "ExecutionAttemptFact",
    "TaskDependencyFact",
    "TaskGraphState",
    "TaskTodo",
    "BranchState",
    "TodoProjection",
    "project_graph",
    "project_task_graph",
]
