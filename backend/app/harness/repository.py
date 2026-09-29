"""PSKit AI4S Harness 的 SQLAlchemy 持久化适配器。

本模块只负责把 Harness 的不可变执行记录映射到增量 ORM 表，不接入旧
``ResearchRun``。调用方必须用当前用户和 Session 建立一个有界 Repository；
所有读取、幂等检查和写入都在这个边界内执行。SQLAlchemy 是生产依赖，采用
延迟导入使本机缺少数据库依赖时仍可做纯契约检查。

wzf：0004 ORM 列已覆盖 Attempt 的诊断/重试 lineage 与 Evidence 的多 artifact
字段；显式列是当前事实源，metadata/structured_data 仅作为旧数据降级兼容，
发现显式列与兼容字段冲突时必须 fail-closed。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from contextlib import nullcontext
from datetime import datetime, timezone
import re
from typing import Any
from uuid import UUID

from app.harness.contracts import PermissionDecision
from app.harness.execution import (
    EvidenceIntent,
    EvidenceRecord,
    ExecutionAttemptIntent,
    ExecutionAttemptRecord,
    InvocationIntent,
    InvocationRecord,
    OutboxIntent,
    OutboxRecord,
    PersistenceIntent,
    ProvenanceIntent,
    ProvenanceRecord,
    ScientificTaskIntent,
    ScientificTaskRecord,
    TaskGraphIntent,
    TaskGraphRecord,
)


_UUID_SUFFIX = re.compile(
    r"(?P<uuid>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)
_IDENTITY_KEY = "_harness_identity"
_REFERENCES_KEY = "_harness_references"
_ATTEMPT_RUNTIME_KEY = "_harness_attempt_runtime"
_EVIDENCE_ARTIFACTS_KEY = "_harness_artifact_ids"
_OUTBOX_AGGREGATE_KEY = "_harness_aggregate_id"
_MISSING = object()


def _canonical_timestamp(value: Any) -> str:
    """把 SQLite 丢失时区信息的时间与 JSON 回退值归一为同一表示。"""

    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return str(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _timestamps_equal(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is right
    return _canonical_timestamp(left) == _canonical_timestamp(right)

# wzf：规范化 ID 必须带有受支持的实体前缀；否则同一 UUID 可能被伪装成
# 不同实体而绕过边界检查。数据库原生 UUID（用户/Session 等）仍可直接传入。
_ENTITY_PREFIXES: dict[str, tuple[str, ...]] = {
    "user": (),
    "session": (),
    "invocation": ("invocation", "capability-invocation", "capability_invocation"),
    "task_graph": ("task-graph", "task_graph", "graph"),
    "scientific_task": ("scientific-task", "scientific_task", "task"),
    "attempt": ("attempt", "execution-attempt", "execution_attempt"),
    "evidence": ("evidence",),
    "provenance": ("provenance", "provenance-edge", "provenance_edge"),
    "outbox": ("outbox", "outbox-event", "outbox_event"),
    "artifact": ("artifact",),
    "goal": ("goal", "research-goal", "research_goal"),
    "skill_execution": ("skill-execution", "skill_execution"),
    "turn": ("turn", "interaction-turn", "interaction_turn"),
}


class HarnessRepositoryError(RuntimeError):
    """Repository 无法安全完成请求时抛出的基类异常。"""


class OwnershipViolation(HarnessRepositoryError):
    """请求对象不属于当前 Repository 的用户/Session。"""


class IdempotencyConflict(HarnessRepositoryError):
    """相同幂等键对应了不同的规范化请求。"""


class ConcurrentRevisionConflict(HarnessRepositoryError):
    """TaskGraph 的 CAS 更新发现 revision 已被其他事务推进。"""


class PersistenceContractError(HarnessRepositoryError):
    """ORM 映射无法无损表示 Harness 记录。"""


def _db_uuid(value: str | UUID, *, kind: str | None = None) -> UUID:
    """将 UUID 或带 harness 前缀的 UUID 映射到数据库 UUID。

    执行层默认 IDFactory 可能使用 ``kind-<uuid>``。数据库仍以 UUID 外键为
    事实源，同时在 metadata 中保存原始文本，读回时恢复它。没有 UUID 尾部
    的值拒绝落库，避免产生无法关联的伪身份。
    """

    if isinstance(value, UUID):
        return value
    text = str(value).strip()
    try:
        return UUID(text)
    except (ValueError, AttributeError):
        match = _UUID_SUFFIX.search(text)
        if match is None:
            raise PersistenceContractError("Harness identity must contain a UUID") from None
        prefix = text[: match.start()].rstrip("-_: ")
        if kind is not None:
            allowed = _ENTITY_PREFIXES.get(kind)
            if allowed is None:
                raise PersistenceContractError(f"unknown Harness entity kind: {kind}")
            if not prefix or prefix not in allowed:
                raise PersistenceContractError(
                    f"Harness identity prefix does not match entity kind: {kind}"
                )
        return UUID(match.group("uuid"))


def _no_autoflush(session: Any) -> Any:
    """为读查询提供显式 no-autoflush 边界，避免中途隐式 flush 半批数据。"""

    context = getattr(session, "no_autoflush", None)
    if context is None:
        raise HarnessRepositoryError("SQLAlchemy session must expose no_autoflush")
    return context


def _status_transition_allowed(current: str, requested: str) -> bool:
    """允许任务/调用状态单向推进；未知状态 fail-closed。"""

    if current == requested:
        return True
    transitions = {
        "planned": {"pending", "queued", "blocked", "cancelled"},
        "pending": {"queued", "blocked", "cancelled"},
        "queued": {
            "running",
            "waiting_for_approval",
            "waiting_for_resource",
            "waiting_for_dependency",
            "waiting_for_input",
            "blocked",
            "cancelled",
        },
        "waiting_for_approval": {"queued", "running", "blocked", "cancelled", "rejected"},
        "waiting_for_resource": {"queued", "running", "blocked", "cancelled"},
        "waiting_for_dependency": {"queued", "running", "blocked", "cancelled"},
        "waiting_for_input": {"queued", "running", "blocked", "cancelled"},
        "running": {
            "succeeded",
            "failed",
            "timed_out",
            "cancelled",
            "interrupted",
            "blocked",
            "waiting_for_resource",
            "waiting_for_dependency",
            "waiting_for_input",
        },
        "blocked": {"queued", "cancelled", "failed"},
        "rejected": set(),
        "succeeded": set(),
        "failed": set(),
        "timed_out": set(),
        "cancelled": set(),
        "interrupted": set(),
    }
    return requested in transitions.get(current, set())


def _immutable_differences(
    current: Any, requested: Any, fields: tuple[str, ...]
) -> tuple[str, ...]:
    differences: list[str] = []
    for field in fields:
        left = getattr(current, field, None)
        right = getattr(requested, field, None)
        if field.endswith("_at"):
            equal = _timestamps_equal(left, right)
        else:
            equal = _json_ready(left) == _json_ready(right)
        if not equal:
            differences.append(field)
    return tuple(differences)


_INVOCATION_IMMUTABLE_FIELDS = (
    "id",
    "session_id",
    "user_id",
    "capability_id",
    "capability_version",
    "manifest_digest",
    "execution_mode",
    "risk_level",
    "inputs",
    "request_hash",
    "permission_decisions",
    "idempotency_key",
    "approval_reference_hash",
)
_TASK_IMMUTABLE_FIELDS = (
    "id",
    "task_graph_id",
    "user_id",
    "task_key",
    "name",
    "task_type",
    "inputs",
    "completion_contract",
    "resource_request",
    "idempotency_key",
    "skill_execution_id",
)
_ATTEMPT_IMMUTABLE_FIELDS = (
    "id",
    "scientific_task_id",
    "user_id",
    "attempt_no",
    "idempotency_key",
    "inputs",
    "parent_attempt_id",
    "retry_payload_hash",
    "retry_policy_id",
    "retry_evidence_snapshot_hash",
    "created_at",
)


def _text_id(value: Any, metadata: Mapping[str, Any] | None = None) -> str:
    if isinstance(metadata, Mapping):
        identity = metadata.get(_IDENTITY_KEY)
        if isinstance(identity, str) and identity:
            return identity
    return str(value)


def _json_ready(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_json_ready(item) for item in value]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _metadata_with_identity(
    metadata: Mapping[str, Any] | None,
    identity: str,
    references: Mapping[str, str | None] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    result = _json_ready(metadata or {})
    if not isinstance(result, dict):
        raise PersistenceContractError("metadata must serialize as an object")
    result[_IDENTITY_KEY] = identity
    if references:
        result[_REFERENCES_KEY] = {
            str(key): value for key, value in references.items() if value is not None
        }
    result.update({key: _json_ready(value) for key, value in extra.items()})
    return result


def _permission_snapshot(decisions: Iterable[PermissionDecision]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for decision in decisions:
        if not isinstance(decision, PermissionDecision):
            raise PersistenceContractError("permission snapshot contains invalid decision")
        result.append(_json_ready(decision.to_dict()))
    return result


def _canonical_artifact_ids(values: Iterable[str | UUID]) -> tuple[str, ...]:
    """以 artifact 实体类型规范化裸 UUID/前缀 UUID，避免旧列与新请求误判冲突。"""

    return tuple(str(_db_uuid(value, kind="artifact")) for value in values)


def _restore_permissions(value: Any) -> tuple[PermissionDecision, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise PersistenceContractError("permission snapshot is not a list")
    decisions: list[PermissionDecision] = []
    for item in value:
        if not isinstance(item, Mapping) or "permission" not in item:
            raise PersistenceContractError("permission snapshot item is invalid")
        decisions.append(
            PermissionDecision(
                permission=str(item["permission"]),
                granted=bool(item.get("granted", False)),
                reason=str(item.get("reason", "")),
                approval_required=bool(item.get("approval_required", False)),
                policy_id=item.get("policy_id"),
            )
        )
    return tuple(decisions)


def _references(metadata: Any) -> Mapping[str, Any]:
    if not isinstance(metadata, Mapping):
        return {}
    value = metadata.get(_REFERENCES_KEY, {})
    return value if isinstance(value, Mapping) else {}


def _model_modules() -> tuple[Any, Any]:
    try:
        from sqlalchemy import select, update
        from app.db import harness_models as models
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised in deployment
        raise HarnessRepositoryError(
            "SQLAlchemy is required for SQLAlchemyInvocationRepository"
        ) from exc
    return (select, update), models


def _new_or_existing_metadata(row: Any) -> dict[str, Any]:
    value = getattr(row, "metadata_json", {})
    return dict(_json_ready(value or {}))


class SQLAlchemyInvocationRepository:
    """面向单一用户/Session 的生产 Repository。

    ``session`` 是同步 SQLAlchemy Session。Repository 不调用 ``commit``；若
    当前 Session 没有事务，会为整个 intent 集合建立一个事务，否则加入调用方
    已有事务。依赖层之间允许 flush 以满足数据库外键顺序，但全部 flush 始终位于
    同一事务/Savepoint 中，不会产生部分提交或破坏外层 API 的事务边界。
    """

    def __init__(
        self,
        session: Any,
        *,
        user_id: str | UUID,
        session_id: str | UUID,
        owns_transaction: bool = False,
    ) -> None:
        if session is None:
            raise ValueError("session is required")
        self.session = session
        self.user_id = _db_uuid(user_id, kind="user")
        self.session_id = _db_uuid(session_id, kind="session")
        # wzf：外层 Harness UoW 已经拥有唯一提交边界时，Repository 只写入
        # 当前事务，不再建立 SAVEPOINT，避免一次批次触发两次 commit 事实。
        self.owns_transaction = owns_transaction
        # 应用 SessionLocal 关闭 autoflush；缓存本次集合中新建的父记录，
        # 让 Task/Attempt 在最终单次 flush 前仍可完成 ownership 校验。
        self._pending_invocations: dict[UUID, InvocationRecord] = {}
        self._pending_graphs: dict[UUID, TaskGraphRecord] = {}
        self._pending_tasks: dict[UUID, ScientificTaskRecord] = {}
        self._pending_attempts: dict[UUID, ExecutionAttemptRecord] = {}
        self._pending_outboxes: dict[str, OutboxRecord] = {}
        self._retry_context: dict[str, Any] = {
            "task_ids": set(),
            "invocation_ids": set(),
            "invocation_parent_by_id": {},
        }

    def _scalar(self, statement: Any) -> Any:
        # wzf：任何读查询都必须在 no_autoflush 内执行；最终 flush 仅由
        # persist_atomically 的单一事务边界负责。
        with _no_autoflush(self.session):
            return self.session.scalar(statement)

    def _execute(self, statement: Any) -> Any:
        with _no_autoflush(self.session):
            return self.session.execute(statement)

    def _scope_graph(self, statement: Any, models: Any) -> Any:
        return statement.where(
            models.TaskGraph.id.is_not(None),
            models.TaskGraph.user_id == self.user_id,
            models.TaskGraph.session_id == self.session_id,
        )

    def _scoped_invocation(self, invocation_id: str, *, lock: bool = False) -> Any | None:
        database_id = _db_uuid(invocation_id, kind="invocation")
        pending = self._pending_invocations.get(database_id)
        if pending is not None:
            return pending
        (select, _update), models = _model_modules()
        statement = select(models.CapabilityInvocation).where(
            models.CapabilityInvocation.id == database_id,
            models.CapabilityInvocation.user_id == self.user_id,
            models.CapabilityInvocation.session_id == self.session_id,
        )
        if lock:
            statement = statement.with_for_update()
        return self._scalar(statement)

    def _scoped_graph(self, graph_id: str, *, lock: bool = False) -> Any | None:
        database_id = _db_uuid(graph_id, kind="task_graph")
        pending = self._pending_graphs.get(database_id)
        if pending is not None:
            return pending
        (select, _update), models = _model_modules()
        statement = select(models.TaskGraph).where(
            models.TaskGraph.id == database_id,
            models.TaskGraph.user_id == self.user_id,
            models.TaskGraph.session_id == self.session_id,
        )
        if lock:
            statement = statement.with_for_update()
        return self._scalar(statement)

    def _scoped_task(self, task_id: str, *, lock: bool = False) -> Any | None:
        database_id = _db_uuid(task_id, kind="scientific_task")
        pending = self._pending_tasks.get(database_id)
        if pending is not None:
            return pending
        (select, _update), models = _model_modules()
        statement = (
            select(models.ScientificTask)
            .join(models.TaskGraph, models.ScientificTask.task_graph_id == models.TaskGraph.id)
            .where(
                models.ScientificTask.id == database_id,
                models.ScientificTask.user_id == self.user_id,
                models.TaskGraph.user_id == self.user_id,
                models.TaskGraph.session_id == self.session_id,
            )
        )
        if lock:
            statement = statement.with_for_update()
        return self._scalar(statement)

    def _scoped_attempt(self, attempt_id: str, *, lock: bool = False) -> Any | None:
        database_id = _db_uuid(attempt_id, kind="attempt")
        pending = self._pending_attempts.get(database_id)
        if pending is not None:
            return pending
        (select, _update), models = _model_modules()
        statement = (
            select(models.ExecutionAttempt)
            .join(
                models.ScientificTask,
                models.ExecutionAttempt.scientific_task_id == models.ScientificTask.id,
            )
            .join(models.TaskGraph, models.ScientificTask.task_graph_id == models.TaskGraph.id)
            .where(
                models.ExecutionAttempt.id == database_id,
                models.ExecutionAttempt.user_id == self.user_id,
                models.TaskGraph.user_id == self.user_id,
                models.TaskGraph.session_id == self.session_id,
            )
        )
        if lock:
            statement = statement.with_for_update()
        return self._scalar(statement)

    def find_by_idempotency(
        self, user_id: str, session_id: str, idempotency_key: str
    ) -> InvocationRecord | None:
        if (
            _db_uuid(user_id, kind="user") != self.user_id
            or _db_uuid(session_id, kind="session") != self.session_id
        ):
            return None
        pending = next(
            (
                item
                for item in self._pending_invocations.values()
                if item.idempotency_key == idempotency_key
            ),
            None,
        )
        if pending is not None:
            return pending
        (select, _update), models = _model_modules()
        row = self._scalar(
            select(models.CapabilityInvocation).where(
                models.CapabilityInvocation.user_id == self.user_id,
                models.CapabilityInvocation.session_id == self.session_id,
                models.CapabilityInvocation.idempotency_key == idempotency_key,
            )
        )
        return self._invocation_record(row) if row is not None else None

    def find_attempt_by_idempotency(
        self, scientific_task_id: str, idempotency_key: str
    ) -> ExecutionAttemptRecord | None:
        task_id = _db_uuid(scientific_task_id, kind="scientific_task")
        pending = next(
            (
                item
                for item in self._pending_attempts.values()
                if _db_uuid(item.scientific_task_id, kind="scientific_task") == task_id
                and item.idempotency_key == idempotency_key
            ),
            None,
        )
        if pending is not None:
            return pending
        (select, _update), models = _model_modules()
        row = self._scalar(
            select(models.ExecutionAttempt)
            .join(
                models.ScientificTask,
                models.ExecutionAttempt.scientific_task_id == models.ScientificTask.id,
            )
            .join(models.TaskGraph, models.ScientificTask.task_graph_id == models.TaskGraph.id)
            .where(
                models.ExecutionAttempt.scientific_task_id == task_id,
                models.ExecutionAttempt.idempotency_key == idempotency_key,
                models.ExecutionAttempt.user_id == self.user_id,
                models.TaskGraph.user_id == self.user_id,
                models.TaskGraph.session_id == self.session_id,
            )
        )
        return self._attempt_record(row) if row is not None else None

    def get_invocation(self, invocation_id: str) -> InvocationRecord | None:
        row = self._scoped_invocation(invocation_id)
        return self._invocation_record(row) if row is not None else None

    def get_task_graph(self, task_graph_id: str) -> TaskGraphRecord | None:
        row = self._scoped_graph(task_graph_id)
        return self._graph_record(row) if row is not None else None

    def get_scientific_task(self, scientific_task_id: str) -> ScientificTaskRecord | None:
        row = self._scoped_task(scientific_task_id)
        return self._task_record(row) if row is not None else None

    def get_attempt(self, attempt_id: str) -> ExecutionAttemptRecord | None:
        row = self._scoped_attempt(attempt_id)
        return self._attempt_record(row) if row is not None else None

    def persist_atomically(self, intents: tuple[PersistenceIntent, ...]) -> None:
        if not isinstance(intents, tuple):
            raise TypeError("intents must be a tuple")
        if not intents:
            return
        # 先解析依赖再写入，单事务内只 flush 一次；任何校验异常都会回滚。
        transaction = self._transaction_context()
        try:
            with transaction:
                self._validate_intents(intents)
                self._retry_context = self._build_retry_context(intents)
                ordered = self._ordered_intents(intents)
                previous_intent_type: type[Any] | None = None
                for intent in ordered:
                    # wzf：Harness ORM 只声明外键而没有把执行图装配成可变 relationship
                    # 对象。SQLite 会真实执行这些约束，因此必须按 Graph→Task→Attempt
                    # →Invocation→Evidence/Provenance→Outbox 的层级 flush。flush 不是
                    # commit；任一后续失败仍由同一外层事务完整回滚。
                    intent_type = type(intent)
                    if previous_intent_type is not None and intent_type is not previous_intent_type:
                        self.session.flush()
                    previous_intent_type = intent_type
                    if isinstance(intent, TaskGraphIntent):
                        self._persist_graph(intent.task_graph)
                    elif isinstance(intent, ScientificTaskIntent):
                        self._persist_task(intent.task)
                    elif isinstance(intent, ExecutionAttemptIntent):
                        self._persist_attempt(intent.attempt)
                    elif isinstance(intent, InvocationIntent):
                        self._persist_invocation(intent.invocation)
                    elif isinstance(intent, EvidenceIntent):
                        self._persist_evidence(intent.evidence)
                    elif isinstance(intent, ProvenanceIntent):
                        self._persist_provenance(intent.provenance)
                    elif isinstance(intent, OutboxIntent):
                        self._persist_outbox(intent.outbox)
                    else:  # pragma: no cover - protected by _validate_intents
                        raise PersistenceContractError("unsupported persistence intent")
                # SessionLocal 关闭 autoflush；最后一层仍需显式 flush。此前所有依赖
                # 层 flush 与本次 flush 都在同一事务中，其他连接在 commit 前不可见。
                self.session.flush()
        finally:
            self._clear_pending()

    def _clear_pending(self) -> None:
        self._pending_invocations.clear()
        self._pending_graphs.clear()
        self._pending_tasks.clear()
        self._pending_attempts.clear()
        self._pending_outboxes.clear()
        self._retry_context = {
            "task_ids": set(),
            "invocation_ids": set(),
            "invocation_parent_by_id": {},
        }

    def _build_retry_context(self, intents: tuple[PersistenceIntent, ...]) -> dict[str, Any]:
        """只为明确 parent_attempt lineage 的同批重试开放终态 reopen。"""
        attempts = [
            intent.attempt for intent in intents if isinstance(intent, ExecutionAttemptIntent)
        ]
        task_ids: set[UUID] = set()
        invocation_ids: set[UUID] = set()
        invocation_parent_by_id: dict[UUID, UUID] = {}
        invocation_intents = {
            _db_uuid(intent.invocation.id, kind="invocation"): intent.invocation
            for intent in intents
            if isinstance(intent, InvocationIntent)
        }
        for attempt in attempts:
            if not attempt.parent_attempt_id:
                continue
            attempt_uuid = _db_uuid(attempt.id, kind="attempt")
            parent = self._scoped_attempt(attempt.parent_attempt_id)
            if parent is None:
                raise OwnershipViolation("retry parent attempt is not owned by current session")
            if _db_uuid(parent.scientific_task_id, kind="scientific_task") != _db_uuid(
                attempt.scientific_task_id, kind="scientific_task"
            ):
                raise PersistenceContractError("retry parent attempt task mismatch")
            if attempt.attempt_no != parent.attempt_no + 1:
                raise PersistenceContractError(
                    "retry attempt_no must increment parent attempt_no by one"
                )
            if parent.status not in {"failed", "timed_out", "interrupted", "blocked"}:
                raise PersistenceContractError(
                    "retry parent attempt is not a retryable terminal failure"
                )
            task_ids.add(_db_uuid(attempt.scientific_task_id, kind="scientific_task"))
            matched = False
            for invocation_uuid, invocation in invocation_intents.items():
                if not invocation.execution_attempt_id:
                    continue
                if _db_uuid(invocation.execution_attempt_id, kind="attempt") != attempt_uuid:
                    continue
                existing = self._scoped_invocation(invocation.id)
                if existing is None:
                    raise PersistenceContractError(
                        "retry invocation must update an existing invocation"
                    )
                current = (
                    existing
                    if isinstance(existing, InvocationRecord)
                    else self._invocation_record(existing)
                )
                if current.status not in {"failed", "timed_out", "interrupted", "blocked"}:
                    raise PersistenceContractError(
                        "retry invocation must originate from a retryable terminal failure"
                    )
                if current.execution_attempt_id is None or _db_uuid(
                    current.execution_attempt_id, kind="attempt"
                ) != _db_uuid(attempt.parent_attempt_id, kind="attempt"):
                    raise PersistenceContractError(
                        "retry invocation parent attempt does not match lineage"
                    )
                if invocation.status not in {"queued", "running"}:
                    raise PersistenceContractError(
                        "retry invocation must reopen as queued or running"
                    )
                if invocation.scientific_task_id is None or _db_uuid(
                    invocation.scientific_task_id, kind="scientific_task"
                ) != _db_uuid(attempt.scientific_task_id, kind="scientific_task"):
                    raise PersistenceContractError(
                        "retry invocation task does not match attempt task"
                    )
                invocation_ids.add(invocation_uuid)
                invocation_parent_by_id[invocation_uuid] = _db_uuid(
                    attempt.parent_attempt_id, kind="attempt"
                )
                matched = True
            if not matched:
                raise PersistenceContractError(
                    "retry attempt must be bound to its failed invocation in the same batch"
                )
        return {
            "task_ids": task_ids,
            "invocation_ids": invocation_ids,
            "invocation_parent_by_id": invocation_parent_by_id,
        }

    def _transaction_context(self) -> Any:
        in_transaction = getattr(self.session, "in_transaction", None)
        active = bool(in_transaction() if callable(in_transaction) else in_transaction)
        if active:
            if self.owns_transaction:
                return nullcontext()
            # SQLAlchemy 的 begin_nested() 会在建立 SAVEPOINT 前强制 flush 外层
            # Session 的 new/dirty/deleted；为避免把调用方未提交的对象带进本批，
            # 外层 UoW 必须先保持 clean。
            for attr in ("new", "dirty", "deleted"):
                if getattr(self.session, attr, ()):
                    raise HarnessRepositoryError(
                        "active transaction must be clean before begin_nested()"
                    )
            # 查询可能已令 Session 自动开启事务；用 SAVEPOINT 保证本次
            # intent 集合失败时仍可回滚，而不替调用方提交或回滚外层事务。
            begin_nested = getattr(self.session, "begin_nested", None)
            if not callable(begin_nested):
                raise HarnessRepositoryError(
                    "active SQLAlchemy transaction requires begin_nested() for fail-closed atomicity"
                )
            # begin_nested() establishes a SAVEPOINT; the caller's outer transaction
            # remains theirs, while this batch can roll back independently.
            return begin_nested()
        begin = getattr(self.session, "begin", None)
        if begin is None:
            raise HarnessRepositoryError("SQLAlchemy session does not expose begin()")
        return begin()

    @staticmethod
    def _ordered_intents(intents: tuple[PersistenceIntent, ...]) -> tuple[PersistenceIntent, ...]:
        order = {
            TaskGraphIntent: 0,
            ScientificTaskIntent: 1,
            ExecutionAttemptIntent: 2,
            InvocationIntent: 3,
            EvidenceIntent: 4,
            ProvenanceIntent: 5,
            OutboxIntent: 6,
        }
        return tuple(sorted(intents, key=lambda item: order.get(type(item), 99)))

    def _validate_intents(self, intents: tuple[PersistenceIntent, ...]) -> None:
        # wzf：Intent 的领域 kind 不一定等于 dataclass 载荷字段名，例如
        # ScientificTaskIntent.kind 是 ``scientific_task``，载荷字段却是
        # ``task``。显式维护映射，避免合法任务/Attempt 在真正落库前被误拒。
        payload_by_intent: dict[type[Any], tuple[str, str]] = {
            InvocationIntent: ("invocation", "invocation"),
            TaskGraphIntent: ("task_graph", "task_graph"),
            ScientificTaskIntent: ("task", "scientific_task"),
            ExecutionAttemptIntent: ("attempt", "attempt"),
            EvidenceIntent: ("evidence", "evidence"),
            ProvenanceIntent: ("provenance", "provenance"),
            OutboxIntent: ("outbox", "outbox"),
        }
        ids: dict[tuple[type[Any], str], Any] = {}
        graphs: dict[UUID, TaskGraphRecord] = {}
        tasks: dict[UUID, ScientificTaskRecord] = {}
        attempts: dict[UUID, ExecutionAttemptRecord] = {}
        records: list[tuple[PersistenceIntent, Any]] = []
        for intent in intents:
            payload_spec = payload_by_intent.get(type(intent))
            if payload_spec is None:
                raise PersistenceContractError("unsupported persistence intent")
            record = getattr(intent, payload_spec[0], None)
            if record is None:
                raise PersistenceContractError("intent payload does not match its kind")
            records.append((intent, record))
            if isinstance(record, TaskGraphRecord):
                graphs[_db_uuid(record.id, kind="task_graph")] = record
            elif isinstance(record, ScientificTaskRecord):
                tasks[_db_uuid(record.id, kind="scientific_task")] = record
            elif isinstance(record, ExecutionAttemptRecord):
                attempts[_db_uuid(record.id, kind="attempt")] = record
        for intent, record in records:
            identity = str(getattr(record, "id", ""))
            intent_kind = payload_by_intent[type(intent)][1]
            _db_uuid(identity, kind=intent_kind)
            key = (type(intent), str(_db_uuid(identity, kind=intent_kind)))
            previous = ids.get(key)
            if previous is not None and previous != record:
                raise PersistenceContractError("duplicate intent identity has different payload")
            ids[key] = record
            owner = getattr(record, "user_id", None)
            if owner is not None and _db_uuid(owner, kind="user") != self.user_id:
                raise OwnershipViolation("intent user ownership mismatch")
            if isinstance(record, TaskGraphRecord):
                if _db_uuid(record.session_id, kind="session") != self.session_id:
                    raise OwnershipViolation("task graph session ownership mismatch")
            elif isinstance(record, ScientificTaskRecord):
                graph = graphs.get(record.task_graph_id)
                if graph is not None and graph.user_id != record.user_id:
                    raise OwnershipViolation("task graph/task user mismatch")
                _db_uuid(record.task_graph_id, kind="task_graph")
            elif isinstance(record, ExecutionAttemptRecord):
                task = tasks.get(record.scientific_task_id)
                if task is not None and task.user_id != record.user_id:
                    raise OwnershipViolation("attempt/task user mismatch")
                _db_uuid(record.scientific_task_id, kind="scientific_task")
                if record.parent_attempt_id:
                    _db_uuid(record.parent_attempt_id, kind="attempt")
            elif isinstance(record, InvocationRecord):
                if _db_uuid(record.session_id, kind="session") != self.session_id:
                    raise OwnershipViolation("invocation session ownership mismatch")
                for ref, kind in (
                    (record.turn_id, "turn"),
                    (record.goal_id, "goal"),
                    (record.skill_execution_id, "skill_execution"),
                    (record.task_graph_id, "task_graph"),
                    (record.scientific_task_id, "scientific_task"),
                    (record.execution_attempt_id, "attempt"),
                ):
                    if ref:
                        _db_uuid(ref, kind=kind)
                if (
                    record.scientific_task_id
                    and _db_uuid(record.scientific_task_id, kind="scientific_task") in tasks
                ):
                    if _db_uuid(record.task_graph_id, kind="task_graph") != _db_uuid(
                        tasks[
                            _db_uuid(record.scientific_task_id, kind="scientific_task")
                        ].task_graph_id,
                        kind="task_graph",
                    ):
                        raise PersistenceContractError("invocation task graph relation mismatch")
                if (
                    record.execution_attempt_id
                    and _db_uuid(record.execution_attempt_id, kind="attempt") in attempts
                ):
                    if not record.scientific_task_id:
                        raise PersistenceContractError(
                            "invocation attempt relation requires scientific_task_id"
                        )
                    if _db_uuid(record.scientific_task_id, kind="scientific_task") != _db_uuid(
                        attempts[
                            _db_uuid(record.execution_attempt_id, kind="attempt")
                        ].scientific_task_id,
                        kind="scientific_task",
                    ):
                        raise PersistenceContractError("invocation attempt relation mismatch")
            elif isinstance(record, EvidenceRecord):
                _db_uuid(record.scientific_task_id, kind="scientific_task")
                _db_uuid(record.attempt_id, kind="attempt")
                task = tasks.get(_db_uuid(record.scientific_task_id, kind="scientific_task"))
                attempt = attempts.get(_db_uuid(record.attempt_id, kind="attempt"))
                if task is not None and task.user_id != record.user_id:
                    raise OwnershipViolation("evidence/task user mismatch")
                if attempt is not None and attempt.user_id != record.user_id:
                    raise OwnershipViolation("evidence/attempt user mismatch")
                if attempt is not None and _db_uuid(
                    attempt.scientific_task_id, kind="scientific_task"
                ) != _db_uuid(record.scientific_task_id, kind="scientific_task"):
                    raise PersistenceContractError("evidence attempt relation mismatch")
                for artifact_id in record.artifact_ids:
                    _db_uuid(artifact_id, kind="artifact")
            elif isinstance(record, OutboxRecord):
                aggregate_kind = {
                    "execution_attempt": "attempt",
                    "scientific_task": "scientific_task",
                    "task_graph": "task_graph",
                    "capability_invocation": "invocation",
                }.get(record.aggregate_type)
                if aggregate_kind is None:
                    raise PersistenceContractError("outbox aggregate_type is not supported")
                _db_uuid(record.aggregate_id, kind=aggregate_kind)
                payload = _json_ready(record.payload)
                if not isinstance(payload, Mapping):
                    raise PersistenceContractError("outbox payload must be an object")
                if payload.get("user_id") is None or payload.get("session_id") is None:
                    raise OwnershipViolation("outbox payload must identify user and session")
                if _db_uuid(str(payload["user_id"]), kind="user") != self.user_id:
                    raise OwnershipViolation("outbox user ownership mismatch")
                if _db_uuid(str(payload["session_id"]), kind="session") != self.session_id:
                    raise OwnershipViolation("outbox session ownership mismatch")

        # 第二遍关系验证：不依赖传入 intent 顺序，也不先写入任何 ORM 行。
        for _intent, record in records:
            if isinstance(record, ScientificTaskRecord):
                if (
                    _db_uuid(record.task_graph_id, kind="task_graph") not in graphs
                    and self._scoped_graph(record.task_graph_id) is None
                ):
                    raise OwnershipViolation(
                        "scientific task graph is not owned by current session"
                    )
            elif isinstance(record, ExecutionAttemptRecord):
                task = tasks.get(
                    _db_uuid(record.scientific_task_id, kind="scientific_task")
                ) or self._scoped_task(record.scientific_task_id)
                if task is None:
                    raise OwnershipViolation("attempt task is not owned by current session")
                if record.parent_attempt_id:
                    parent = attempts.get(
                        _db_uuid(record.parent_attempt_id, kind="attempt")
                    ) or self._scoped_attempt(record.parent_attempt_id)
                    if parent is None or _db_uuid(
                        parent.scientific_task_id, kind="scientific_task"
                    ) != _db_uuid(record.scientific_task_id, kind="scientific_task"):
                        raise OwnershipViolation("attempt parent lineage mismatch")
            elif isinstance(record, InvocationRecord):
                if (
                    record.task_graph_id
                    and _db_uuid(record.task_graph_id, kind="task_graph") not in graphs
                ):
                    if self._scoped_graph(record.task_graph_id) is None:
                        raise OwnershipViolation(
                            "invocation task graph is not owned by current session"
                        )
                task = None
                if record.scientific_task_id:
                    task = tasks.get(
                        _db_uuid(record.scientific_task_id, kind="scientific_task")
                    ) or self._scoped_task(record.scientific_task_id)
                    if task is None:
                        raise OwnershipViolation("invocation task is not owned by current session")
                    if record.task_graph_id and _db_uuid(
                        task.task_graph_id, kind="task_graph"
                    ) != _db_uuid(record.task_graph_id, kind="task_graph"):
                        raise PersistenceContractError("invocation task graph relation mismatch")
                if record.execution_attempt_id:
                    if not record.scientific_task_id:
                        raise PersistenceContractError(
                            "invocation attempt relation requires scientific_task_id"
                        )
                    attempt = attempts.get(
                        _db_uuid(record.execution_attempt_id, kind="attempt")
                    ) or self._scoped_attempt(record.execution_attempt_id)
                    if attempt is None:
                        raise OwnershipViolation(
                            "invocation attempt is not owned by current session"
                        )
                    if _db_uuid(attempt.scientific_task_id, kind="scientific_task") != _db_uuid(
                        record.scientific_task_id, kind="scientific_task"
                    ):
                        raise PersistenceContractError("invocation attempt relation mismatch")
            elif isinstance(record, EvidenceRecord):
                task = tasks.get(
                    _db_uuid(record.scientific_task_id, kind="scientific_task")
                ) or self._scoped_task(record.scientific_task_id)
                attempt = attempts.get(
                    _db_uuid(record.attempt_id, kind="attempt")
                ) or self._scoped_attempt(record.attempt_id)
                if (
                    task is None
                    or attempt is None
                    or _db_uuid(attempt.scientific_task_id, kind="scientific_task")
                    != _db_uuid(record.scientific_task_id, kind="scientific_task")
                ):
                    raise OwnershipViolation("evidence task/attempt ownership mismatch")
            elif isinstance(record, ProvenanceRecord):
                self._validate_provenance_target(record, attempts)
            elif isinstance(record, OutboxRecord):
                aggregate_maps = {
                    "execution_attempt": (attempts, "attempt", self._scoped_attempt),
                    "scientific_task": (tasks, "scientific_task", self._scoped_task),
                    "task_graph": (graphs, "task_graph", self._scoped_graph),
                    "capability_invocation": (None, "invocation", self._scoped_invocation),
                }
                mapped = aggregate_maps.get(record.aggregate_type)
                if mapped is None:
                    raise PersistenceContractError("outbox aggregate_type is not supported")
                cache, kind, loader = mapped
                aggregate_uuid = _db_uuid(record.aggregate_id, kind=kind)
                aggregate = cache.get(aggregate_uuid) if cache is not None else None
                if aggregate is None:
                    aggregate = loader(record.aggregate_id)
                if aggregate is None:
                    raise OwnershipViolation("outbox aggregate is not owned by current session")

    def _persist_graph(self, record: TaskGraphRecord) -> None:
        (select, update), models = _model_modules()
        graph_id = _db_uuid(record.id, kind="task_graph")
        row = self._scoped_graph(record.id, lock=True)
        if row is None:
            # 同 ID 但不同 owner 时不回显对象，只拒绝写入。
            existing = self._scalar(
                select(models.TaskGraph.id).where(
                    models.TaskGraph.id == graph_id,
                )
            )
            if existing is not None:
                raise OwnershipViolation("task graph ownership mismatch")
            if record.revision != 1:
                raise ConcurrentRevisionConflict("new task graph revision must be 1")
            metadata = _metadata_with_identity(
                {},
                record.id,
                {"goal_id": record.goal_id, "skill_execution_id": record.skill_execution_id},
            )
            self.session.add(
                models.TaskGraph(
                    id=graph_id,
                    session_id=self.session_id,
                    user_id=self.user_id,
                    goal_id=_db_uuid(record.goal_id, kind="goal") if record.goal_id else None,
                    skill_execution_id=_db_uuid(record.skill_execution_id, kind="skill_execution")
                    if record.skill_execution_id
                    else None,
                    name=record.name,
                    status=record.status,
                    revision=record.revision,
                    metadata_json=metadata,
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                )
            )
            self._pending_graphs[graph_id] = record
            return
        current = self._graph_record(row)
        if current.revision == record.revision and current == record:
            return
        expected = record.revision - 1
        if current.revision != expected:
            raise ConcurrentRevisionConflict("task graph revision changed concurrently")
        metadata = _metadata_with_identity(
            _new_or_existing_metadata(row),
            record.id,
            {"goal_id": record.goal_id, "skill_execution_id": record.skill_execution_id},
        )
        result = self._execute(
            update(models.TaskGraph)
            .where(
                models.TaskGraph.id == graph_id,
                models.TaskGraph.user_id == self.user_id,
                models.TaskGraph.session_id == self.session_id,
                models.TaskGraph.revision == expected,
            )
            .values(
                goal_id=_db_uuid(record.goal_id, kind="goal") if record.goal_id else None,
                skill_execution_id=_db_uuid(record.skill_execution_id, kind="skill_execution")
                if record.skill_execution_id
                else None,
                name=record.name,
                status=record.status,
                revision=record.revision,
                metadata_json=metadata,
                updated_at=record.updated_at,
            )
        )
        if getattr(result, "rowcount", 1) != 1:
            raise ConcurrentRevisionConflict("task graph CAS update lost")

    def _persist_invocation(self, record: InvocationRecord) -> None:
        (_select, _update), models = _model_modules()
        row = self._scoped_invocation(record.id, lock=True)
        if row is not None:
            if isinstance(row, InvocationRecord):
                self._assert_invocation_update(
                    row,
                    record,
                    allow_reopen=_db_uuid(record.id, kind="invocation")
                    in self._retry_context["invocation_ids"],
                    retry_parent_attempt_id=self._retry_context["invocation_parent_by_id"].get(
                        _db_uuid(record.id, kind="invocation")
                    ),
                )
                return
            existing = self._invocation_record(row)
            self._assert_invocation_update(
                existing,
                record,
                allow_reopen=_db_uuid(record.id, kind="invocation")
                in self._retry_context["invocation_ids"],
                retry_parent_attempt_id=self._retry_context["invocation_parent_by_id"].get(
                    _db_uuid(record.id, kind="invocation")
                ),
            )
            self._assign_invocation(row, record)
            return
        # 通过唯一键检查同 Session 的其他 ID，避免 IntegrityError 才发现冲突。
        duplicate = self.find_by_idempotency(
            str(self.user_id), str(self.session_id), record.idempotency_key
        )
        if duplicate is not None and _db_uuid(duplicate.id, kind="invocation") != _db_uuid(
            record.id, kind="invocation"
        ):
            raise IdempotencyConflict(
                "invocation idempotency key is already bound to another record id"
            )
        self.session.add(self._invocation_model(record))
        self._pending_invocations[_db_uuid(record.id, kind="invocation")] = record

    @staticmethod
    def _assert_invocation_update(
        current: InvocationRecord,
        requested: InvocationRecord,
        *,
        allow_reopen: bool = False,
        retry_parent_attempt_id: UUID | None = None,
    ) -> None:
        differences = _immutable_differences(current, requested, _INVOCATION_IMMUTABLE_FIELDS)
        if differences:
            raise IdempotencyConflict(
                "invocation immutable fields differ: " + ", ".join(differences)
            )
        legal = _status_transition_allowed(current.status, requested.status)
        if not legal and not (
            allow_reopen
            and current.status in {"failed", "timed_out", "interrupted", "blocked"}
            and requested.status in {"queued", "running"}
        ):
            raise PersistenceContractError(
                f"illegal invocation status transition: {current.status}->{requested.status}"
            )
        for field in ("task_graph_id", "scientific_task_id", "execution_attempt_id"):
            old = getattr(current, field)
            new = getattr(requested, field)
            if old is not None and new not in (None, old):
                if (
                    allow_reopen
                    and field == "execution_attempt_id"
                    and retry_parent_attempt_id is not None
                    and old is not None
                    and _db_uuid(old, kind="attempt") == retry_parent_attempt_id
                ):
                    continue
                raise IdempotencyConflict(f"invocation reference {field} is immutable once bound")

    def _assign_invocation(self, row: Any, record: InvocationRecord) -> None:
        metadata = _metadata_with_identity(
            _new_or_existing_metadata(row),
            record.id,
            {
                "task_graph_id": record.task_graph_id,
                "scientific_task_id": record.scientific_task_id,
                "execution_attempt_id": record.execution_attempt_id,
            },
        )
        row.status = record.status
        row.result_json = _json_ready(record.result)
        row.error_type = record.error_type
        row.error_message = record.error_message
        row.updated_at = record.updated_at
        row.started_at = record.started_at
        row.finished_at = record.finished_at
        for field, kind in (
            ("task_graph_id", "task_graph"),
            ("scientific_task_id", "scientific_task"),
            ("execution_attempt_id", "attempt"),
        ):
            if getattr(row, field, None) is None and getattr(record, field, None):
                setattr(row, field, _db_uuid(getattr(record, field), kind=kind))
        row.metadata_json = metadata

    def _invocation_model(self, record: InvocationRecord) -> Any:
        (_select, _update), models = _model_modules()
        metadata = _metadata_with_identity(
            {},
            record.id,
            {
                "task_graph_id": record.task_graph_id,
                "scientific_task_id": record.scientific_task_id,
                "execution_attempt_id": record.execution_attempt_id,
            },
        )
        return models.CapabilityInvocation(
            **self._invocation_values(record, metadata),
            id=_db_uuid(record.id, kind="invocation"),
            session_id=self.session_id,
            user_id=self.user_id,
        )

    @staticmethod
    def _invocation_values(record: InvocationRecord, metadata: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "turn_id": _db_uuid(record.turn_id, kind="turn") if record.turn_id else None,
            "goal_id": _db_uuid(record.goal_id, kind="goal") if record.goal_id else None,
            "skill_execution_id": _db_uuid(record.skill_execution_id, kind="skill_execution")
            if record.skill_execution_id
            else None,
            "task_graph_id": _db_uuid(record.task_graph_id, kind="task_graph")
            if record.task_graph_id
            else None,
            "scientific_task_id": _db_uuid(record.scientific_task_id, kind="scientific_task")
            if record.scientific_task_id
            else None,
            "execution_attempt_id": _db_uuid(record.execution_attempt_id, kind="attempt")
            if record.execution_attempt_id
            else None,
            "capability_id": record.capability_id,
            "capability_version": record.capability_version,
            "manifest_digest": record.manifest_digest,
            "execution_mode": record.execution_mode,
            "risk_level": record.risk_level,
            "status": record.status,
            "input_json": _json_ready(record.inputs),
            "request_hash": record.request_hash,
            "permission_snapshot_json": _permission_snapshot(record.permission_decisions),
            "approval_reference_hash": record.approval_reference_hash,
            "idempotency_key": record.idempotency_key,
            "result_json": _json_ready(record.result),
            "error_type": record.error_type,
            "error_message": record.error_message,
            "metadata_json": metadata,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
        }

    def _persist_task(self, record: ScientificTaskRecord) -> None:
        (select, _update), models = _model_modules()
        graph = self._scoped_graph(record.task_graph_id)
        if graph is None:
            raise OwnershipViolation("scientific task graph is not owned by current session")
        row = self._scoped_task(record.id, lock=True)
        if row is not None:
            if isinstance(row, ScientificTaskRecord):
                self._assert_task_update(
                    row,
                    record,
                    allow_reopen=_db_uuid(record.id, kind="scientific_task")
                    in self._retry_context["task_ids"],
                )
                return
            self._assert_task_update(
                self._task_record(row),
                record,
                allow_reopen=_db_uuid(record.id, kind="scientific_task")
                in self._retry_context["task_ids"],
            )
            self._assign_task(row, record)
            return
        pending_duplicate = next(
            (
                item
                for item in self._pending_tasks.values()
                if _db_uuid(item.task_graph_id, kind="task_graph")
                == _db_uuid(record.task_graph_id, kind="task_graph")
                and item.task_key == record.task_key
            ),
            None,
        )
        if pending_duplicate is not None and _db_uuid(
            pending_duplicate.id, kind="scientific_task"
        ) != _db_uuid(record.id, kind="scientific_task"):
            raise IdempotencyConflict("task graph/task key is already pending")
        duplicate = self._scalar(
            select(models.ScientificTask)
            .join(models.TaskGraph, models.ScientificTask.task_graph_id == models.TaskGraph.id)
            .where(
                models.ScientificTask.task_graph_id
                == _db_uuid(record.task_graph_id, kind="task_graph"),
                models.ScientificTask.user_id == self.user_id,
                models.TaskGraph.user_id == self.user_id,
                models.TaskGraph.session_id == self.session_id,
                models.ScientificTask.task_key == record.task_key,
            )
        )
        if duplicate is not None:
            current = self._task_record(duplicate)
            self._assert_task_update(
                current,
                record,
                allow_reopen=_db_uuid(record.id, kind="scientific_task")
                in self._retry_context["task_ids"],
            )
            self._assign_task(duplicate, record)
            return
        self.session.add(self._task_model(record))
        self._pending_tasks[_db_uuid(record.id, kind="scientific_task")] = record

    def _task_model(self, record: ScientificTaskRecord) -> Any:
        (_select, _update), models = _model_modules()
        return models.ScientificTask(
            id=_db_uuid(record.id, kind="scientific_task"),
            task_graph_id=_db_uuid(record.task_graph_id, kind="task_graph"),
            skill_execution_id=_db_uuid(record.skill_execution_id, kind="skill_execution")
            if record.skill_execution_id
            else None,
            user_id=self.user_id,
            task_key=record.task_key,
            name=record.name,
            task_type=record.task_type,
            status=record.status,
            input_json=_json_ready(record.inputs),
            completion_contract_json=_json_ready(record.completion_contract),
            resource_request_json=_json_ready(record.resource_request),
            idempotency_key=record.idempotency_key,
            metadata_json=_metadata_with_identity(
                {},
                record.id,
                {
                    "task_graph_id": record.task_graph_id,
                    "skill_execution_id": record.skill_execution_id,
                },
            ),
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    def _assign_task(self, row: Any, record: ScientificTaskRecord) -> None:
        row.status = record.status
        row.updated_at = record.updated_at

    @staticmethod
    def _assert_task_update(
        current: ScientificTaskRecord,
        requested: ScientificTaskRecord,
        *,
        allow_reopen: bool = False,
    ) -> None:
        differences = _immutable_differences(current, requested, _TASK_IMMUTABLE_FIELDS)
        if differences:
            raise IdempotencyConflict(
                "scientific task immutable fields differ: " + ", ".join(differences)
            )
        if not _status_transition_allowed(current.status, requested.status) and not (
            allow_reopen
            and current.status in {"failed", "timed_out", "interrupted", "blocked"}
            and requested.status in {"queued", "running"}
        ):
            raise PersistenceContractError(
                f"illegal scientific task status transition: {current.status}->{requested.status}"
            )

    def _persist_attempt(self, record: ExecutionAttemptRecord) -> None:
        (select, _update), models = _model_modules()
        task = self._scoped_task(record.scientific_task_id)
        if task is None:
            raise OwnershipViolation("attempt task is not owned by current session")
        if record.parent_attempt_id:
            parent = self._scoped_attempt(record.parent_attempt_id)
            if parent is None or _db_uuid(
                parent.scientific_task_id, kind="scientific_task"
            ) != _db_uuid(record.scientific_task_id, kind="scientific_task"):
                raise OwnershipViolation("attempt parent lineage mismatch")
        row = self._scoped_attempt(record.id, lock=True)
        if row is not None:
            if isinstance(row, ExecutionAttemptRecord):
                self._assert_attempt_update(row, record)
                return
            self._assert_attempt_update(self._attempt_record(row), record)
            self._assign_attempt(row, record)
            return
        pending_duplicate = next(
            (
                item
                for item in self._pending_attempts.values()
                if _db_uuid(item.scientific_task_id, kind="scientific_task")
                == _db_uuid(record.scientific_task_id, kind="scientific_task")
                and item.idempotency_key == record.idempotency_key
            ),
            None,
        )
        if pending_duplicate is not None and _db_uuid(
            pending_duplicate.id, kind="attempt"
        ) != _db_uuid(record.id, kind="attempt"):
            raise IdempotencyConflict("attempt idempotency key is already pending")
        duplicate = self._scalar(
            select(models.ExecutionAttempt)
            .join(
                models.ScientificTask,
                models.ExecutionAttempt.scientific_task_id == models.ScientificTask.id,
            )
            .join(models.TaskGraph, models.ScientificTask.task_graph_id == models.TaskGraph.id)
            .where(
                models.ExecutionAttempt.scientific_task_id
                == _db_uuid(record.scientific_task_id, kind="scientific_task"),
                models.ExecutionAttempt.idempotency_key == record.idempotency_key,
                models.ExecutionAttempt.user_id == self.user_id,
                models.TaskGraph.user_id == self.user_id,
                models.TaskGraph.session_id == self.session_id,
            )
        )
        if duplicate is not None:
            self._assert_attempt_update(self._attempt_record(duplicate), record)
            self._assign_attempt(duplicate, record)
            return
        self.session.add(self._attempt_model(record))
        self._pending_attempts[_db_uuid(record.id, kind="attempt")] = record

    @staticmethod
    def _assert_attempt_update(
        current: ExecutionAttemptRecord, requested: ExecutionAttemptRecord
    ) -> None:
        terminal = {
            "succeeded",
            "failed",
            "timed_out",
            "cancelled",
            "interrupted",
            "blocked",
        }
        differences = _immutable_differences(current, requested, _ATTEMPT_IMMUTABLE_FIELDS)
        if differences:
            raise IdempotencyConflict("attempt immutable fields differ: " + ", ".join(differences))
        if current.status in terminal and requested.status != current.status:
            raise PersistenceContractError(
                "terminal attempt cannot reopen; create a child retry attempt"
            )
        if not _status_transition_allowed(current.status, requested.status):
            raise PersistenceContractError(
                f"illegal execution attempt status transition: {current.status}->{requested.status}"
            )
        if current.started_at is not None and not _timestamps_equal(
            requested.started_at, current.started_at
        ):
            raise IdempotencyConflict("attempt started_at is immutable once assigned")
        if current.finished_at is not None and not _timestamps_equal(
            requested.finished_at, current.finished_at
        ):
            raise IdempotencyConflict("attempt finished_at is immutable once assigned")
        current_evidence = tuple(current.diagnostic_evidence_ids)
        requested_evidence = tuple(requested.diagnostic_evidence_ids)
        if len(set(requested_evidence)) != len(requested_evidence):
            raise PersistenceContractError("attempt diagnostic_evidence_ids must be unique")
        if requested_evidence[: len(current_evidence)] != current_evidence:
            raise IdempotencyConflict(
                "attempt diagnostic_evidence_ids may only append to the existing prefix"
            )
        if current.diagnosis is not None and requested.diagnosis != current.diagnosis:
            raise IdempotencyConflict("attempt diagnosis is immutable once assigned")
        if (
            current.failure_fingerprint is not None
            and requested.failure_fingerprint != current.failure_fingerprint
        ):
            raise IdempotencyConflict("attempt failure_fingerprint is immutable once assigned")
        if requested.status in terminal and requested.finished_at is None:
            raise PersistenceContractError("terminal attempt requires finished_at")
        if requested.status not in terminal and requested.finished_at is not None:
            raise PersistenceContractError("non-terminal attempt cannot have finished_at")

    @staticmethod
    def _assign_attempt(row: Any, record: ExecutionAttemptRecord) -> None:
        for field, value in (
            ("status", record.status),
            ("output_json", _json_ready(record.output)),
            ("error_type", record.error_type),
            ("error_message", record.error_message),
            ("failure_fingerprint", record.failure_fingerprint),
            ("available_at", record.available_at),
            ("started_at", record.started_at),
            ("finished_at", record.finished_at),
            # 0004 explicit retry columns are authoritative; metadata remains a legacy fallback.
            ("diagnosis", record.diagnosis),
            ("diagnostic_evidence_ids", list(record.diagnostic_evidence_ids)),
            ("retry_payload_hash", record.retry_payload_hash),
            ("retry_policy_id", record.retry_policy_id),
            ("retry_evidence_snapshot_hash", record.retry_evidence_snapshot_hash),
        ):
            if hasattr(row, field):
                setattr(row, field, value)
        # ExecutionAttemptRecord predates worker lease columns. Preserve ORM lease state
        # unless a future record explicitly carries a value for the mutable lease field.
        for field in ("worker_id", "lease_token", "lease_expires_at", "heartbeat_at", "updated_at"):
            value = getattr(record, field, _MISSING)
            if value is not _MISSING and hasattr(row, field):
                setattr(row, field, value)

    def _attempt_metadata(self, record: ExecutionAttemptRecord) -> dict[str, Any]:
        # 0004 显式列是权威事实源；该结构仅供旧数据库读回与迁移兼容。
        return _metadata_with_identity(
            {},
            record.id,
            {
                "scientific_task_id": record.scientific_task_id,
                "parent_attempt_id": record.parent_attempt_id,
            },
            **{
                _ATTEMPT_RUNTIME_KEY: {
                    "diagnosis": record.diagnosis,
                    "diagnostic_evidence_ids": list(record.diagnostic_evidence_ids),
                    "retry_payload_hash": record.retry_payload_hash,
                    "retry_policy_id": record.retry_policy_id,
                    "retry_evidence_snapshot_hash": record.retry_evidence_snapshot_hash,
                    "available_at": record.available_at.isoformat()
                    if record.available_at
                    else None,
                }
            },
        )

    def _attempt_model(self, record: ExecutionAttemptRecord) -> Any:
        (_select, _update), models = _model_modules()
        return models.ExecutionAttempt(
            id=_db_uuid(record.id, kind="attempt"),
            scientific_task_id=_db_uuid(record.scientific_task_id, kind="scientific_task"),
            user_id=self.user_id,
            attempt_no=record.attempt_no,
            status=record.status,
            idempotency_key=record.idempotency_key,
            input_json=_json_ready(record.inputs),
            output_json=_json_ready(record.output),
            error_type=record.error_type,
            error_message=record.error_message,
            failure_fingerprint=record.failure_fingerprint,
            available_at=record.available_at,
            diagnosis=record.diagnosis,
            diagnostic_evidence_ids=list(record.diagnostic_evidence_ids),
            retry_payload_hash=record.retry_payload_hash,
            retry_policy_id=record.retry_policy_id,
            retry_evidence_snapshot_hash=record.retry_evidence_snapshot_hash,
            parent_attempt_id=_db_uuid(record.parent_attempt_id, kind="attempt")
            if record.parent_attempt_id
            else None,
            metadata_json=self._attempt_metadata(record),
            created_at=record.created_at,
            started_at=record.started_at,
            finished_at=record.finished_at,
        )

    def _persist_evidence(self, record: EvidenceRecord) -> None:
        (select, _update), models = _model_modules()
        task = self._scoped_task(record.scientific_task_id)
        attempt = self._scoped_attempt(record.attempt_id)
        if (
            task is None
            or attempt is None
            or _db_uuid(attempt.scientific_task_id, kind="scientific_task")
            != _db_uuid(record.scientific_task_id, kind="scientific_task")
        ):
            raise OwnershipViolation("evidence task/attempt ownership mismatch")
        canonical_artifact_ids = _canonical_artifact_ids(record.artifact_ids)
        artifact_ids = tuple(UUID(item) for item in canonical_artifact_ids)
        if artifact_ids:
            # Artifact bridge 尚未在本地纯契约环境验证为生产写入链；因此至少要求
            # 当前 user/session 和当前 task 归属同时成立，不能把任意路径或历史产物登记成证据。
            from app.db.models import Artifact

            for artifact_id in artifact_ids:
                artifact = self._scalar(
                    select(Artifact).where(
                        Artifact.id == artifact_id,
                        Artifact.user_id == self.user_id,
                        Artifact.session_id == self.session_id,
                        Artifact.task_id
                        == _db_uuid(record.scientific_task_id, kind="scientific_task"),
                    )
                )
                if artifact is None:
                    raise OwnershipViolation(
                        "evidence artifact is not registered for current user/session/task"
                    )
        artifact_id = artifact_ids[0] if artifact_ids else None
        structured = _json_ready(record.metadata)
        if not isinstance(structured, dict):
            structured = {}
        structured[_EVIDENCE_ARTIFACTS_KEY] = list(canonical_artifact_ids)
        evidence_id = _db_uuid(record.id, kind="evidence")
        existing = self._scalar(
            select(models.ScientificEvidence).where(
                models.ScientificEvidence.id == evidence_id,
                models.ScientificEvidence.user_id == self.user_id,
            )
        )
        if existing is not None:
            if self._evidence_semantics(existing) != self._evidence_semantics(record):
                raise IdempotencyConflict("evidence identity is already bound to different data")
            return
        duplicate = self._scalar(
            select(models.ScientificEvidence).where(
                models.ScientificEvidence.user_id == self.user_id,
                models.ScientificEvidence.scientific_task_id
                == _db_uuid(record.scientific_task_id, kind="scientific_task"),
                models.ScientificEvidence.attempt_id == _db_uuid(record.attempt_id, kind="attempt"),
                models.ScientificEvidence.evidence_type == record.evidence_type,
                models.ScientificEvidence.content_hash == record.content_hash,
            )
        )
        if duplicate is not None:
            if self._evidence_semantics(duplicate) != self._evidence_semantics(record):
                raise IdempotencyConflict(
                    "evidence semantic key is already bound to different data"
                )
            return
        self.session.add(
            models.ScientificEvidence(
                id=evidence_id,
                user_id=self.user_id,
                scientific_task_id=_db_uuid(record.scientific_task_id, kind="scientific_task"),
                attempt_id=_db_uuid(record.attempt_id, kind="attempt"),
                evidence_type=record.evidence_type,
                status=record.status,
                statement=record.statement,
                artifact_id=artifact_id,
                artifact_ids=list(canonical_artifact_ids),
                source_uri=record.source_uri,
                content_hash=record.content_hash,
                reviewer=record.reviewer_id,
                sufficient=record.sufficient,
                structured_data_json=structured,
                metadata_json=_metadata_with_identity({}, record.id),
                created_at=record.created_at,
            )
        )

    @staticmethod
    def _evidence_semantics(value: Any) -> dict[str, Any]:
        if isinstance(value, EvidenceRecord):
            metadata = _json_ready(value.metadata)
            if isinstance(metadata, dict):
                metadata.pop(_EVIDENCE_ARTIFACTS_KEY, None)
            return {
                "task": str(_db_uuid(value.scientific_task_id, kind="scientific_task")),
                "attempt": str(_db_uuid(value.attempt_id, kind="attempt")),
                "type": value.evidence_type,
                "status": value.status,
                "statement": value.statement,
                "artifacts": _canonical_artifact_ids(value.artifact_ids),
                "source_uri": value.source_uri,
                "content_hash": value.content_hash,
                "reviewer": value.reviewer_id,
                "sufficient": value.sufficient,
                "metadata": metadata,
            }
        structured = _json_ready(getattr(value, "structured_data_json", {}) or {})
        row_metadata = dict(structured) if isinstance(structured, Mapping) else {}
        row_metadata.pop(_EVIDENCE_ARTIFACTS_KEY, None)
        row_artifacts = getattr(value, "artifact_ids", _MISSING)
        structured_artifacts = (
            structured.get(_EVIDENCE_ARTIFACTS_KEY, _MISSING)
            if isinstance(structured, Mapping)
            else _MISSING
        )
        if row_artifacts is not _MISSING and structured_artifacts is not _MISSING:
            if _canonical_artifact_ids(row_artifacts or ()) != _canonical_artifact_ids(
                structured_artifacts or ()
            ):
                raise PersistenceContractError(
                    "evidence artifact_ids explicit column conflicts with structured metadata"
                )
        if row_artifacts is _MISSING:
            row_artifacts = structured_artifacts if structured_artifacts is not _MISSING else ()
        return {
            "task": str(_db_uuid(value.scientific_task_id, kind="scientific_task")),
            "attempt": str(_db_uuid(value.attempt_id, kind="attempt")),
            "type": value.evidence_type,
            "status": value.status,
            "statement": value.statement,
            "artifacts": _canonical_artifact_ids(row_artifacts),
            "source_uri": value.source_uri,
            "content_hash": value.content_hash,
            "reviewer": value.reviewer,
            "sufficient": value.sufficient,
            "metadata": row_metadata,
        }

    def _validate_provenance_target(
        self,
        record: ProvenanceRecord,
        pending_attempts: Mapping[UUID, ExecutionAttemptRecord] | None = None,
    ) -> None:
        if record.target_type != "execution_attempt":
            raise PersistenceContractError(
                "provenance target_type must be execution_attempt in the current slice"
            )
        target_uuid = _db_uuid(record.target_id, kind="attempt")
        target = pending_attempts.get(target_uuid) if pending_attempts is not None else None
        if target is None:
            target = self._scoped_attempt(record.target_id)
        if target is None:
            raise OwnershipViolation("provenance target attempt is not owned by current session")

    def _persist_provenance(self, record: ProvenanceRecord) -> None:
        (select, _update), models = _model_modules()
        self._validate_provenance_target(record)
        edge_id = _db_uuid(record.id, kind="provenance")
        existing = self._scalar(
            select(models.ProvenanceEdge).where(
                models.ProvenanceEdge.id == edge_id,
                models.ProvenanceEdge.user_id == self.user_id,
            )
        )
        if existing is not None:
            if self._provenance_semantics(existing) != self._provenance_semantics(record):
                raise IdempotencyConflict("provenance identity is already bound to different data")
            return
        duplicate = self._scalar(
            select(models.ProvenanceEdge).where(
                models.ProvenanceEdge.user_id == self.user_id,
                models.ProvenanceEdge.source_type == record.source_type,
                models.ProvenanceEdge.source_id == record.source_id,
                models.ProvenanceEdge.target_type == record.target_type,
                models.ProvenanceEdge.target_id == record.target_id,
                models.ProvenanceEdge.relation_type == record.relation_type,
            )
        )
        if duplicate is not None:
            if self._provenance_semantics(duplicate) != self._provenance_semantics(record):
                raise IdempotencyConflict(
                    "provenance semantic key is already bound to different data"
                )
            return
        self.session.add(
            models.ProvenanceEdge(
                id=edge_id,
                user_id=self.user_id,
                source_type=record.source_type,
                source_id=record.source_id,
                target_type=record.target_type,
                target_id=record.target_id,
                relation_type=record.relation_type,
                metadata_json=_metadata_with_identity(record.metadata, record.id),
                created_at=record.created_at,
            )
        )

    @staticmethod
    def _provenance_semantics(value: Any) -> dict[str, Any]:
        if isinstance(value, ProvenanceRecord):
            metadata = _json_ready(value.metadata)
            if isinstance(metadata, dict):
                metadata.pop(_IDENTITY_KEY, None)
            return {
                "source_type": value.source_type,
                "source_id": value.source_id,
                "target_type": value.target_type,
                "target_id": value.target_id,
                "relation_type": value.relation_type,
                "metadata": metadata,
            }
        row_metadata = _json_ready(getattr(value, "metadata_json", {}) or {})
        if isinstance(row_metadata, dict):
            row_metadata.pop(_IDENTITY_KEY, None)
        return {
            "source_type": value.source_type,
            "source_id": value.source_id,
            "target_type": value.target_type,
            "target_id": value.target_id,
            "relation_type": value.relation_type,
            "metadata": row_metadata,
        }

    def _persist_outbox(self, record: OutboxRecord) -> None:
        (select, _update), models = _model_modules()
        payload = _json_ready(record.payload)
        if not isinstance(payload, dict):
            raise PersistenceContractError("outbox payload must be an object")
        stored_user = payload.get("user_id")
        stored_session = payload.get("session_id")
        if stored_user is None or stored_session is None:
            raise OwnershipViolation("outbox payload must identify user and session")
        if (
            _db_uuid(str(stored_user), kind="user") != self.user_id
            or _db_uuid(str(stored_session), kind="session") != self.session_id
        ):
            raise OwnershipViolation("outbox intent ownership mismatch")
        payload.setdefault(_OUTBOX_AGGREGATE_KEY, record.aggregate_id)
        aggregate_kind = {
            "execution_attempt": "attempt",
            "scientific_task": "scientific_task",
            "task_graph": "task_graph",
            "capability_invocation": "invocation",
        }.get(record.aggregate_type)
        if aggregate_kind is None:
            raise PersistenceContractError("outbox aggregate_type is not supported")
        aggregate_id = _db_uuid(record.aggregate_id, kind=aggregate_kind)
        pending = self._pending_outboxes.get(record.dedupe_key)
        if pending is not None:
            if self._outbox_semantics(pending) != self._outbox_semantics(record):
                raise IdempotencyConflict(
                    "outbox dedupe key is already bound to different event semantics"
                )
            return
        # 0003 lacks user/session columns on outbox rows; inspect payload ownership
        # immediately after this global dedupe lookup and fail closed on ambiguity.
        duplicate = self._scalar(
            select(models.HarnessOutboxEvent).where(
                models.HarnessOutboxEvent.dedupe_key == record.dedupe_key
            )
        )
        if duplicate is not None:
            duplicate_payload = _json_ready(duplicate.payload_json)
            if not isinstance(duplicate_payload, Mapping):
                raise OwnershipViolation("outbox payload ownership is unavailable")
            stored_user = duplicate_payload.get("user_id")
            stored_session = duplicate_payload.get("session_id")
            if stored_user is None or stored_session is None:
                raise OwnershipViolation("outbox payload lacks user/session ownership")
            if (
                _db_uuid(str(stored_user), kind="user") != self.user_id
                or _db_uuid(str(stored_session), kind="session") != self.session_id
            ):
                raise OwnershipViolation("outbox dedupe ownership mismatch")
            if self._outbox_semantics(duplicate) != self._outbox_semantics(
                record, payload=payload, aggregate_id=aggregate_id
            ):
                raise IdempotencyConflict(
                    "outbox dedupe key is already bound to different event semantics"
                )
            return
        self.session.add(
            models.HarnessOutboxEvent(
                id=_db_uuid(record.id, kind="outbox"),
                dedupe_key=record.dedupe_key,
                event_type=record.event_type,
                aggregate_type=record.aggregate_type,
                aggregate_id=aggregate_id,
                payload_json=payload,
                status=record.status,
                pending_at=record.pending_at,
                available_at=record.available_at,
            )
        )
        self._pending_outboxes[record.dedupe_key] = record

    @staticmethod
    def _outbox_semantics(
        value: Any,
        *,
        payload: Mapping[str, Any] | None = None,
        aggregate_id: UUID | None = None,
    ) -> dict[str, Any]:
        if isinstance(value, OutboxRecord):
            aggregate_kind = {
                "execution_attempt": "attempt",
                "scientific_task": "scientific_task",
                "task_graph": "task_graph",
                "capability_invocation": "invocation",
            }.get(value.aggregate_type)
            if aggregate_kind is None:
                raise PersistenceContractError("outbox aggregate_type is not supported")
            event_payload = _json_ready(payload if payload is not None else value.payload)
            if not isinstance(event_payload, dict):
                raise PersistenceContractError("outbox payload must be an object")
            event_payload.setdefault(_OUTBOX_AGGREGATE_KEY, value.aggregate_id)
            event_aggregate_id = (
                aggregate_id
                if aggregate_id is not None
                else _db_uuid(value.aggregate_id, kind=aggregate_kind)
            )
            return {
                "id": str(_db_uuid(value.id, kind="outbox")),
                "dedupe_key": value.dedupe_key,
                "event_type": value.event_type,
                "aggregate_type": value.aggregate_type,
                "aggregate_id": str(event_aggregate_id),
                "payload": event_payload,
                "status": value.status,
                "pending_at": value.pending_at.isoformat(),
                "available_at": value.available_at.isoformat(),
            }
        return {
            "id": str(value.id),
            "dedupe_key": value.dedupe_key,
            "event_type": value.event_type,
            "aggregate_type": value.aggregate_type,
            "aggregate_id": str(aggregate_id if aggregate_id is not None else value.aggregate_id),
            "payload": _json_ready(payload if payload is not None else value.payload_json),
            "status": value.status,
            "pending_at": value.pending_at.isoformat(),
            "available_at": value.available_at.isoformat(),
        }

    def _invocation_record(self, row: Any) -> InvocationRecord:
        if isinstance(row, InvocationRecord):
            return row
        metadata = getattr(row, "metadata_json", {}) or {}
        refs = _references(metadata)
        return InvocationRecord(
            id=_text_id(row.id, metadata),
            session_id=str(row.session_id),
            user_id=str(row.user_id),
            turn_id=str(row.turn_id) if row.turn_id else None,
            goal_id=str(row.goal_id) if row.goal_id else None,
            skill_execution_id=str(row.skill_execution_id) if row.skill_execution_id else None,
            task_graph_id=refs.get("task_graph_id")
            or (str(row.task_graph_id) if row.task_graph_id else None),
            scientific_task_id=refs.get("scientific_task_id")
            or (str(row.scientific_task_id) if row.scientific_task_id else None),
            execution_attempt_id=refs.get("execution_attempt_id")
            or (str(row.execution_attempt_id) if row.execution_attempt_id else None),
            capability_id=row.capability_id,
            capability_version=row.capability_version,
            manifest_digest=row.manifest_digest,
            execution_mode=row.execution_mode,
            risk_level=row.risk_level,
            status=row.status,
            inputs=row.input_json or {},
            request_hash=row.request_hash,
            permission_decisions=_restore_permissions(row.permission_snapshot_json),
            idempotency_key=row.idempotency_key,
            approval_reference_hash=row.approval_reference_hash,
            result=row.result_json or {},
            error_type=row.error_type,
            error_message=row.error_message,
            created_at=row.created_at,
            updated_at=row.updated_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
        )

    def _graph_record(self, row: Any) -> TaskGraphRecord:
        if isinstance(row, TaskGraphRecord):
            return row
        metadata = getattr(row, "metadata_json", {}) or {}
        refs = _references(metadata)
        return TaskGraphRecord(
            id=_text_id(row.id, metadata),
            session_id=str(row.session_id),
            user_id=str(row.user_id),
            name=row.name,
            status=row.status,
            revision=row.revision,
            goal_id=refs.get("goal_id") or (str(row.goal_id) if row.goal_id else None),
            skill_execution_id=refs.get("skill_execution_id")
            or (str(row.skill_execution_id) if row.skill_execution_id else None),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _task_record(self, row: Any) -> ScientificTaskRecord:
        if isinstance(row, ScientificTaskRecord):
            return row
        metadata = getattr(row, "metadata_json", {}) or {}
        refs = _references(metadata)
        return ScientificTaskRecord(
            id=_text_id(row.id, metadata),
            task_graph_id=refs.get("task_graph_id") or str(row.task_graph_id),
            user_id=str(row.user_id),
            task_key=row.task_key,
            name=row.name,
            task_type=row.task_type,
            status=row.status,
            inputs=row.input_json or {},
            completion_contract=row.completion_contract_json or {},
            resource_request=row.resource_request_json or {},
            idempotency_key=row.idempotency_key or "",
            skill_execution_id=refs.get("skill_execution_id")
            or (str(row.skill_execution_id) if row.skill_execution_id else None),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _attempt_record(self, row: Any) -> ExecutionAttemptRecord:
        if isinstance(row, ExecutionAttemptRecord):
            return row
        metadata = getattr(row, "metadata_json", {}) or {}
        refs = _references(metadata)
        runtime = metadata.get(_ATTEMPT_RUNTIME_KEY, {}) if isinstance(metadata, Mapping) else {}
        if not isinstance(runtime, Mapping):
            raise PersistenceContractError("attempt runtime metadata is invalid")

        def read_runtime_field(field: str, default: Any = None) -> Any:
            explicit = getattr(row, field, _MISSING)
            legacy = runtime.get(field, _MISSING)
            if explicit is not _MISSING and legacy is not _MISSING:
                explicit_normalized = _json_ready(explicit)
                legacy_normalized = _json_ready(legacy)
                if field == "diagnostic_evidence_ids":
                    explicit_normalized = list(explicit or ())
                    legacy_normalized = list(legacy or ())
                elif field == "available_at":
                    explicit_normalized = _canonical_timestamp(explicit)
                    legacy_normalized = _canonical_timestamp(legacy)
                if explicit_normalized != legacy_normalized:
                    raise PersistenceContractError(
                        f"attempt {field} explicit column conflicts with runtime metadata"
                    )
            selected = explicit if explicit is not _MISSING else legacy
            if selected is _MISSING:
                selected = default
            if (
                field == "available_at"
                and selected is not None
                and not isinstance(selected, datetime)
            ):
                try:
                    selected = datetime.fromisoformat(str(selected))
                except ValueError as exc:
                    raise PersistenceContractError(
                        "attempt available_at metadata is invalid"
                    ) from exc
            if field == "available_at" and isinstance(selected, datetime):
                if selected.tzinfo is None:
                    selected = selected.replace(tzinfo=timezone.utc)
            if field == "diagnostic_evidence_ids":
                selected = tuple(selected or ())
            return selected

        available_at = read_runtime_field("available_at")
        return ExecutionAttemptRecord(
            id=_text_id(row.id, metadata),
            scientific_task_id=refs.get("scientific_task_id") or str(row.scientific_task_id),
            user_id=str(row.user_id),
            attempt_no=row.attempt_no,
            status=row.status,
            idempotency_key=row.idempotency_key or "",
            inputs=row.input_json or {},
            output=row.output_json or {},
            error_type=row.error_type,
            error_message=row.error_message,
            failure_fingerprint=row.failure_fingerprint,
            parent_attempt_id=refs.get("parent_attempt_id")
            or (str(row.parent_attempt_id) if row.parent_attempt_id else None),
            diagnosis=read_runtime_field("diagnosis"),
            diagnostic_evidence_ids=read_runtime_field("diagnostic_evidence_ids", ()),
            retry_payload_hash=read_runtime_field("retry_payload_hash"),
            retry_policy_id=read_runtime_field("retry_policy_id"),
            retry_evidence_snapshot_hash=read_runtime_field("retry_evidence_snapshot_hash"),
            available_at=available_at,
            created_at=row.created_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
        )


__all__ = [
    "ConcurrentRevisionConflict",
    "HarnessRepositoryError",
    "IdempotencyConflict",
    "OwnershipViolation",
    "PersistenceContractError",
    "SQLAlchemyInvocationRepository",
    "_canonical_artifact_ids",
    "_db_uuid",
    "_permission_snapshot",
    "_restore_permissions",
]
