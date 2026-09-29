"""旧 PSKit 数据到 AI4S Harness 的只读规划与显式应用。

本模块刻意不导入 ``app.config``、``app.db.session`` 或任何默认数据库连接。
调用方必须传入已经创建的数据库 Session；``inspect_legacy`` 与
``plan_legacy_migration`` 只读，只有明确调用 ``apply_legacy_migration`` 才会
向调用方的事务增加对象。应用阶段只 ``flush``，不替调用方 commit 或 rollback。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import inspect as pyinspect
import json
import math
import re
from types import MappingProxyType
from typing import Any, Mapping, MutableMapping, Sequence
from uuid import UUID, NAMESPACE_URL, uuid5

from .enums import (
    CandidateScoreSelectionStatus,
    CandidateSetStatus,
    EvidenceStatus,
    ExecutionAttemptStatus,
    InteractionTurnStatus,
    ResearchGoalStatus,
    ResearchSessionStatus,
    ScoreRunStatus,
    ScientificCandidateStatus,
    ScientificTaskStatus,
    SkillExecutionStatus,
    StrategyFeedbackStatus,
)


# UUID5 命名空间固定在源码中，确保多次审计、不同进程和重放迁移得到相同键。
MIGRATION_NAMESPACE = uuid5(NAMESPACE_URL, "https://pskit.local/ai4s-harness/migration/v1")
SKILL_NAMESPACE = uuid5(MIGRATION_NAMESPACE, "skill-execution")
ATTEMPT_NAMESPACE = uuid5(MIGRATION_NAMESPACE, "execution-attempt")
CHECKPOINT_NAMESPACE = uuid5(MIGRATION_NAMESPACE, "workflow-checkpoint")
EVIDENCE_NAMESPACE = uuid5(MIGRATION_NAMESPACE, "scientific-evidence")
PROVENANCE_NAMESPACE = uuid5(MIGRATION_NAMESPACE, "provenance-edge")
TARGET_NAMESPACE = uuid5(MIGRATION_NAMESPACE, "scientific-target")
# wzf：第二批领域对象使用独立 UUID5 命名空间。候选仍尽量复用旧 Candidate
# 主键；其余对象以旧事实和分组键生成稳定身份，重复 apply 不会产生新行。
CANDIDATE_SET_NAMESPACE = uuid5(MIGRATION_NAMESPACE, "candidate-set")
SCORE_RUN_NAMESPACE = uuid5(MIGRATION_NAMESPACE, "score-run")
STRUCTURE_PREDICTION_NAMESPACE = uuid5(MIGRATION_NAMESPACE, "structure-prediction-run")


LEGACY_MODEL_NAMES = (
    "User",
    "AgentSession",
    "AgentMessage",
    "AgentTurn",
    "ResearchRun",
    "CandidateTrack",
    "Candidate",
    "StrategyPolicy",
    "StrategyTransition",
    "ResearchTaskLink",
    "Task",
    "TaskExecutionLease",
    "TaskRetry",
    "Artifact",
)

TARGET_MODEL_NAMES = (
    "ResearchSession",
    "SessionGoalLink",
    "SessionMessage",
    "InteractionTurn",
    "ResearchGoal",
    "ScientificTarget",
    "SkillExecution",
    "ScientificTask",
    "TaskDependency",
    "ExecutionAttempt",
    "WorkflowCheckpoint",
    "ScientificEvidence",
    "ProvenanceEdge",
    "HarnessOutboxEvent",
    "CandidateSet",
    "ScientificCandidate",
    "ScoreRun",
    "CandidateScoreResult",
    "StructurePredictionRun",
    "DecisionPolicy",
    "StrategyFeedback",
)


class MigrationConfigurationError(RuntimeError):
    """模型注册表或调用方 Session 不满足迁移接口时抛出。"""


class MigrationApplyError(RuntimeError):
    """应用阶段 flush 失败；事务边界仍由调用方负责。"""


def _copy_mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if value is None:
        return MappingProxyType({})
    return MappingProxyType(deepcopy(dict(value)))


def _as_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _id_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _row_value(row: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(row, Mapping) and name in row:
            return row[name]
        try:
            value = getattr(row, name)
        except AttributeError:
            continue
        if value is not None:
            return value
    return default


def _safe_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _json_value(value: Any) -> Any:
    """把旧 JSON 和 UUID 等值转换成可放入迁移计划的独立对象。"""

    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return _safe_iso(value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return deepcopy(value)


# wzf：显式规定新 Harness 对象的最小写入拓扑；不能依赖旧库查询顺序或
# SQLAlchemy 对同表自引用行的隐式排序，否则 Attempt 重试血缘可能偶发失败。
_APPLY_TARGET_PRIORITY = {
    "ResearchSession": 10,
    "SessionMessage": 20,
    "InteractionTurn": 30,
    "ResearchGoal": 40,
    "ScientificTarget": 45,
    "SessionGoalLink": 50,
    "SkillExecution": 60,
    "WorkflowCheckpoint": 70,
    "ScientificTask": 80,
    "TaskDependency": 85,
    "ExecutionAttempt": 90,
    "CandidateSet": 100,
    "DecisionPolicy": 105,
    "ScientificCandidate": 110,
    "ScoreRun": 120,
    "CandidateScoreResult": 130,
    "ScientificEvidence": 140,
    "StructurePredictionRun": 150,
    "StrategyFeedback": 160,
    "ProvenanceEdge": 170,
    "HarnessOutboxEvent": 180,
}

_IDEMPOTENCY_IDENTITY_FIELDS = {
    "id",
    "user_id",
    "session_id",
    "goal_id",
    "skill_execution_id",
    "scientific_task_id",
    "attempt_id",
    "artifact_id",
    "legacy_agent_session_id",
    "legacy_agent_message_id",
    "legacy_agent_turn_id",
    "legacy_research_run_id",
    "legacy_task_id",
    "client_turn_id",
    "sequence_no",
    "task_key",
    "attempt_no",
    "checkpoint_no",
    "idempotency_key",
    "identity_hash",
    "identifier",
    "skill_id",
    "skill_version",
    "source_type",
    "source_id",
    "target_type",
    "target_id",
    "relation_type",
    "candidate_set_id",
    "candidate_id",
    "score_run_id",
    "scientific_target_id",
    "generation_task_id",
    "generation_attempt_id",
    "parent_candidate_id",
    "parent_set_id",
    "generation_config_hash",
    "representation_hash",
    "input_membership_hash",
    "config_hash",
    "legacy_candidate_track_id",
    "legacy_candidate_id",
    "legacy_snapshot_key",
    "legacy_af3_task_id",
    "scope_type",
    "scope_key",
    "decision_policy_id",
    "dedupe_key",
    "legacy_strategy_policy_id",
    "legacy_policy_id",
    "legacy_transition_id",
}


@dataclass(frozen=True, slots=True)
class MigrationIssue:
    """迁移审计问题；阻断问题会禁止 ``apply`` 写入。"""

    code: str
    message: str
    blocking: bool = True
    source_type: str | None = None
    source_id: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", _copy_mapping(self.details))

    def as_dict(self, *, redact_ids: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "blocking": self.blocking,
            "source_type": self.source_type,
            "target_type": self.target_type,
            "details": _json_value(self.details),
        }
        if not redact_ids:
            result["source_id"] = self.source_id
            result["target_id"] = self.target_id
        return result


# 约定中同时出现简写和完整名称，两个名称指向同一类型。
LegacyMigrationIssue = MigrationIssue


@dataclass(frozen=True, slots=True)
class MigrationOperation:
    """计划中的一个目标对象操作；``payload`` 不含文件内容。"""

    operation: str
    source_type: str
    source_id: str
    target_type: str
    target_id: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    stable_key: str | None = None

    def __post_init__(self) -> None:
        if self.operation not in {"create", "ensure"}:
            raise ValueError("迁移操作只能是 create 或 ensure")
        object.__setattr__(self, "payload", _copy_mapping(self.payload))

    def as_dict(self, *, redact_ids: bool = False) -> dict[str, Any]:
        result = {
            "operation": self.operation,
            "source_type": self.source_type,
            "target_type": self.target_type,
            "payload": _json_value(self.payload),
            "stable_key": self.stable_key,
        }
        if not redact_ids:
            result["source_id"] = self.source_id
            result["target_id"] = self.target_id
        return result


@dataclass(frozen=True, slots=True)
class LegacyMigrationInspection:
    """只读审计结果，不包含任何待写入对象。"""

    source_counts: Mapping[str, int] = field(default_factory=dict)
    issues: tuple[MigrationIssue, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_counts", _copy_mapping(self.source_counts))
        object.__setattr__(self, "issues", tuple(self.issues))

    @property
    def blocking_issues(self) -> tuple[MigrationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.blocking)

    @property
    def can_apply(self) -> bool:
        return not self.blocking_issues

    def as_dict(self, *, redact_ids: bool = False) -> dict[str, Any]:
        return {
            "source_counts": dict(self.source_counts),
            "issues": [issue.as_dict(redact_ids=redact_ids) for issue in self.issues],
            "blocking_issue_count": len(self.blocking_issues),
        }


@dataclass(frozen=True, slots=True)
class LegacyMigrationPlan:
    """只读生成的迁移计划；不会因构造而触碰数据库。"""

    operations: tuple[MigrationOperation, ...] = ()
    issues: tuple[MigrationIssue, ...] = ()
    source_counts: Mapping[str, int] = field(default_factory=dict)
    target_counts: Mapping[str, int] = field(default_factory=dict)
    # 保存显式传入的注册表仅为支持 ``plan.apply(session)`` 便捷写法，不连接数据库。
    registry: Any = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "operations", tuple(self.operations))
        object.__setattr__(self, "issues", tuple(self.issues))
        object.__setattr__(self, "source_counts", _copy_mapping(self.source_counts))
        object.__setattr__(self, "target_counts", _copy_mapping(self.target_counts))

    @property
    def blocking_issues(self) -> tuple[MigrationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.blocking)

    @property
    def can_apply(self) -> bool:
        return not self.blocking_issues

    @property
    def operation_count(self) -> int:
        return len(self.operations)

    def as_dict(self, *, redact_ids: bool = False) -> dict[str, Any]:
        return {
            "source_counts": dict(self.source_counts),
            "target_counts": dict(self.target_counts),
            "operation_count": len(self.operations),
            "operations": [
                operation.as_dict(redact_ids=redact_ids) for operation in self.operations
            ],
            "issues": [issue.as_dict(redact_ids=redact_ids) for issue in self.issues],
            "blocking_issue_count": len(self.blocking_issues),
            "can_apply": self.can_apply,
        }

    def apply(self, session: Any, *, model_registry: Any = None) -> "LegacyMigrationResult":
        """显式应用本计划；仅 flush，不 commit。"""

        return apply_legacy_migration(
            session,
            self,
            model_registry=model_registry or self.registry,
        )


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """应用阶段结果；``applied`` 只表示 flush 成功，不代表事务已提交。"""

    applied: bool
    created_count: int = 0
    skipped_count: int = 0
    issues: tuple[MigrationIssue, ...] = ()
    errors: tuple[str, ...] = ()
    operation_results: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "issues", tuple(self.issues))
        object.__setattr__(self, "errors", tuple(self.errors))
        object.__setattr__(self, "operation_results", tuple(self.operation_results))

    @property
    def blocking_issues(self) -> tuple[MigrationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.blocking)

    def as_dict(self, *, redact_ids: bool = False) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "created_count": self.created_count,
            "skipped_count": self.skipped_count,
            "issues": [issue.as_dict(redact_ids=redact_ids) for issue in self.issues],
            "errors": list(self.errors),
            "operation_results": [
                _json_value(item) for item in self.operation_results
            ],
        }


LegacyMigrationResult = MigrationResult


@dataclass(frozen=True, slots=True)
class MigrationVerification:
    """迁移执行后的可审计核对结果。

    该核对器只读取迁移计划、结果和目标 Session；不会读取或修改旧表，
    也不会提交事务。调用方可把同一快照用于提交前核对与 rollback 证明。
    """

    passed: bool
    source_counts: Mapping[str, int] = field(default_factory=dict)
    target_counts: Mapping[str, int] = field(default_factory=dict)
    relationship_checks: Mapping[str, bool] = field(default_factory=dict)
    ownership_checks: Mapping[str, bool] = field(default_factory=dict)
    artifact_hashes: Mapping[str, str] = field(default_factory=dict)
    idempotency: Mapping[str, Any] = field(default_factory=dict)
    rollback: Mapping[str, Any] = field(default_factory=dict)
    issues: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_counts", _copy_mapping(self.source_counts))
        object.__setattr__(self, "target_counts", _copy_mapping(self.target_counts))
        object.__setattr__(self, "relationship_checks", _copy_mapping(self.relationship_checks))
        object.__setattr__(self, "ownership_checks", _copy_mapping(self.ownership_checks))
        object.__setattr__(self, "artifact_hashes", _copy_mapping(self.artifact_hashes))
        object.__setattr__(self, "idempotency", _copy_mapping(self.idempotency))
        object.__setattr__(self, "rollback", _copy_mapping(self.rollback))
        object.__setattr__(self, "issues", tuple(self.issues))

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "source_counts": dict(self.source_counts),
            "target_counts": dict(self.target_counts),
            "relationship_checks": dict(self.relationship_checks),
            "ownership_checks": dict(self.ownership_checks),
            "artifact_hashes": dict(self.artifact_hashes),
            "idempotency": _json_value(self.idempotency),
            "rollback": _json_value(self.rollback),
            "issues": list(self.issues),
        }


def _verification_rows(session: Any, registry: ModelRegistry | None, target_type: str) -> list[Any]:
    if registry is None or registry.target.get(target_type) is None:
        return []
    return _rows_for(session, registry.target[target_type])


def _verification_count(session: Any, registry: ModelRegistry | None) -> dict[str, int]:
    if registry is None:
        return {}
    return {
        name: len(_verification_rows(session, registry, name))
        for name in TARGET_MODEL_NAMES
        if registry.target.get(name) is not None
    }


def _verification_id(row: Any) -> str | None:
    return _id_text(_row_value(row, "id"))


def verify_legacy_migration(
    session: Any,
    plan: LegacyMigrationPlan,
    result: MigrationResult,
    *,
    model_registry: Any = None,
    expected_artifact_hashes: Mapping[str, str] | None = None,
    before_target_counts: Mapping[str, int] | None = None,
    repeated_result: MigrationResult | None = None,
    rollback_target_counts: Mapping[str, int] | None = None,
) -> MigrationVerification:
    """对一次迁移执行做只读四重核对。

    ``before_target_counts`` 与 ``rollback_target_counts`` 由真实临时库事务
    在 commit/rollback 前后采集；本函数不主动 rollback，避免改变调用方事务。
    ``repeated_result`` 应是同一计划第二次 apply 的结果，用于证明重复 apply
    没有新增对象。Artifact/报告摘要只接受已登记的 ``content_hash`` 或
    ``metadata_json.sha256``，缺失或不匹配会 fail-closed。
    """

    registry = _registry_from(model_registry or plan.registry)
    issues: list[str] = []
    source_counts = dict(plan.source_counts)
    target_counts = _verification_count(session, registry)
    expected_target = dict(plan.target_counts)
    relationship_checks: dict[str, bool] = {}
    ownership_checks: dict[str, bool] = {}
    artifact_hashes: dict[str, str] = {}

    if not result.applied:
        issues.append("migration_result_not_applied")
    if result.errors:
        issues.extend(f"migration_error:{item}" for item in result.errors)
    if before_target_counts is not None:
        for target_type, count in expected_target.items():
            actual_delta = target_counts.get(target_type, 0) - before_target_counts.get(target_type, 0)
            if actual_delta != count:
                issues.append(f"target_count_mismatch:{target_type}:{actual_delta}!={count}")

    target_ids = {
        name: {_verification_id(row) for row in _verification_rows(session, registry, name)}
        for name in TARGET_MODEL_NAMES
    }
    for operation in plan.operations:
        row_id = operation.target_id
        present = row_id in target_ids.get(operation.target_type, set())
        relationship_checks[f"{operation.target_type}:{row_id}"] = present
        if not present:
            issues.append(f"missing_target:{operation.target_type}:{row_id}")

    owners: dict[str, str] = {}
    for target_type in TARGET_MODEL_NAMES:
        for row in _verification_rows(session, registry, target_type):
            row_id = _verification_id(row)
            owner = _id_text(_row_value(row, "user_id"))
            if row_id is not None and owner is not None:
                owners[f"{target_type}:{row_id}"] = owner
    for operation in plan.operations:
        expected_owner = _id_text(operation.payload.get("user_id"))
        key = f"{operation.target_type}:{operation.target_id}"
        ok = expected_owner is None or owners.get(key) == expected_owner
        ownership_checks[key] = ok
        if not ok:
            issues.append(f"ownership_mismatch:{key}")

    for operation in plan.operations:
        if operation.target_type != "ScientificEvidence":
            continue
        payload = operation.payload
        content_hash = _row_value(payload, "content_hash", default=None)
        metadata = payload.get("metadata_json") or payload.get("metadata") or {}
        if content_hash is None and isinstance(metadata, Mapping):
            content_hash = metadata.get("sha256") or metadata.get("content_hash")
        if content_hash:
            artifact_hashes[str(payload.get("artifact_id") or operation.target_id)] = str(content_hash)
        else:
            issues.append(f"artifact_hash_missing:{operation.target_id}")
    if expected_artifact_hashes:
        for artifact_id, expected_hash in expected_artifact_hashes.items():
            actual = artifact_hashes.get(str(artifact_id))
            if actual != str(expected_hash):
                issues.append(f"artifact_hash_mismatch:{artifact_id}")

    idempotency: dict[str, Any] = {}
    if repeated_result is not None:
        idempotency = {
            "checked": True,
            "created_count": repeated_result.created_count,
            "skipped_count": repeated_result.skipped_count,
            "passed": repeated_result.applied and repeated_result.created_count == 0,
        }
        if not idempotency["passed"]:
            issues.append("repeated_apply_not_idempotent")
    else:
        idempotency = {"checked": False, "passed": False}

    rollback: dict[str, Any] = {"checked": rollback_target_counts is not None}
    if rollback_target_counts is not None and before_target_counts is not None:
        rollback["restored"] = dict(rollback_target_counts) == dict(before_target_counts)
        if not rollback["restored"]:
            issues.append("rollback_target_counts_not_restored")
    else:
        rollback["restored"] = False

    return MigrationVerification(
        passed=not issues,
        source_counts=source_counts,
        target_counts=target_counts,
        relationship_checks=relationship_checks,
        ownership_checks=ownership_checks,
        artifact_hashes=artifact_hashes,
        idempotency=idempotency,
        rollback=rollback,
        issues=tuple(issues),
    )


# 更明确的批次二命名；旧名称保持兼容。
verify_migration_execution = verify_legacy_migration


@dataclass(frozen=True, slots=True)
class ModelRegistry:
    """允许测试或离线审计显式注入旧/新模型，避免隐式 settings 连接。"""

    legacy: Mapping[str, Any]
    target: Mapping[str, Any]


def _registry_from(value: Any = None) -> ModelRegistry:
    if isinstance(value, ModelRegistry):
        return value
    if value is not None:
        if isinstance(value, Mapping):
            legacy = value.get("legacy", value.get("old", {}))
            target = value.get("target", value.get("new", {}))
            if legacy or target:
                return ModelRegistry(dict(legacy), dict(target))
            return ModelRegistry(
                {name: value[name] for name in LEGACY_MODEL_NAMES if name in value},
                {name: value[name] for name in TARGET_MODEL_NAMES if name in value},
            )
        legacy = getattr(value, "legacy", getattr(value, "old", None))
        target = getattr(value, "target", getattr(value, "new", None))
        if legacy is not None or target is not None:
            return ModelRegistry(dict(legacy or {}), dict(target or {}))

    try:
        from app.db import models as legacy_module
    except Exception as exc:  # pragma: no cover - 由 CLI 转成脱敏 JSON 错误
        raise MigrationConfigurationError(f"无法导入旧模型：{exc}") from exc
    try:
        from app.db import harness_models as target_module
    except Exception as exc:  # pragma: no cover - 第一批未注册模型时的明确错误
        raise MigrationConfigurationError(f"无法导入 Harness 模型：{exc}") from exc

    legacy = {
        name: getattr(legacy_module, name)
        for name in LEGACY_MODEL_NAMES
        if hasattr(legacy_module, name)
    }
    target = {
        name: getattr(target_module, name)
        for name in TARGET_MODEL_NAMES
        if hasattr(target_module, name)
    }
    missing = [name for name in TARGET_MODEL_NAMES if name not in target]
    if missing:
        raise MigrationConfigurationError("Harness 模型注册不完整：" + ", ".join(missing))
    return ModelRegistry(legacy=legacy, target=target)


def _rows_for(session: Any, model: Any) -> list[Any]:
    """用不绑定 SQLAlchemy 的适配顺序读取一张表。"""

    if model is None:
        return []
    if hasattr(session, "rows_for"):
        return list(session.rows_for(model))
    if hasattr(session, "all_for"):
        return list(session.all_for(model))
    if hasattr(session, "query"):
        return list(session.query(model).all())
    if hasattr(session, "scalars"):
        try:
            from sqlalchemy import select  # type: ignore

            return list(session.scalars(select(model)).all())
        except ImportError as exc:
            raise MigrationConfigurationError("读取 SQLAlchemy Session 需要安装 SQLAlchemy") from exc
    raise MigrationConfigurationError("Session 必须提供 rows_for/all_for/query/scalars 读取接口")


def _collect(registry: ModelRegistry, session: Any) -> dict[str, list[Any]]:
    return {
        name: _rows_for(session, registry.legacy.get(name))
        for name in LEGACY_MODEL_NAMES
        if registry.legacy.get(name) is not None
    }


def _index(
    rows: Sequence[Any],
    kind: str,
    issues: list[MigrationIssue],
    *,
    id_names: tuple[str, ...] = ("id",),
) -> dict[UUID, Any]:
    result: dict[UUID, Any] = {}
    for row in rows:
        row_id = _as_uuid(_row_value(row, *id_names))
        if row_id is None:
            issues.append(
                MigrationIssue(
                    "INVALID_SOURCE_ID",
                    f"{kind} 缺少合法 UUID 主键",
                    source_type=kind,
                    details={"raw_id": _id_text(_row_value(row, *id_names))},
                )
            )
            continue
        if row_id in result:
            issues.append(
                MigrationIssue(
                    "DUPLICATE_SOURCE_ID",
                    f"{kind} 出现重复稳定主键，无法确定迁移事实",
                    source_type=kind,
                    source_id=str(row_id),
                    details={"stable_key": str(row_id)},
                )
            )
            continue
        result[row_id] = row
    return result


def _model_fields(model: Any) -> set[str] | None:
    if model is None:
        return set()
    mapper = getattr(model, "__mapper__", None)
    if mapper is not None:
        try:
            return {attribute.key for attribute in mapper.attrs}
        except Exception:
            pass
    table = getattr(model, "__table__", None)
    if table is not None:
        try:
            return {column.name for column in table.columns}
        except Exception:
            pass
    try:
        from dataclasses import fields as dataclass_fields, is_dataclass

        if is_dataclass(model):
            return {item.name for item in dataclass_fields(model)}
    except (ImportError, TypeError):
        pass
    annotations = getattr(model, "__annotations__", None)
    if annotations:
        return set(annotations)
    try:
        signature = pyinspect.signature(model)
        names = {
            name
            for name, parameter in signature.parameters.items()
            if name != "self" and parameter.kind
            in {parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY}
        }
        if names:
            return names
    except (TypeError, ValueError):
        pass
    return None


_UUID_VALUE_FIELDS = frozenset(
    {
        "id",
        "user_id",
        "session_id",
        "goal_id",
        "skill_execution_id",
        "scientific_task_id",
        "attempt_id",
        "artifact_id",
        "legacy_agent_session_id",
        "legacy_agent_message_id",
        "legacy_research_run_id",
        "legacy_task_id",
        "user_message_id",
        "assistant_message_id",
        "legacy_agent_turn_id",
        "parent_attempt_id",
        "upstream_task_id",
        "downstream_task_id",
        "candidate_set_id",
        "candidate_id",
        "score_run_id",
        "target_id",
        "scientific_target_id",
        "generation_task_id",
        "generation_attempt_id",
        "parent_candidate_id",
        "parent_set_id",
        "legacy_candidate_track_id",
        "legacy_candidate_id",
        "legacy_af3_task_id",
        "decision_policy_id",
        "policy_id",
        "turn_id",
        "message_id",
        "evidence_id",
        "legacy_policy_id",
        "legacy_strategy_policy_id",
        "legacy_transition_id",
    }
)


def _coerce_model_values(model: Any, values: Mapping[str, Any]) -> dict[str, Any]:
    """按 SQLAlchemy mapper 列类型转换 UUID；无 mapper 时保留旧替身回退。"""

    coerced = dict(values)
    mapper = getattr(model, "__mapper__", None)
    if mapper is None:
        for key in _UUID_VALUE_FIELDS:
            if key not in coerced:
                continue
            converted = _as_uuid(coerced[key])
            if converted is not None:
                coerced[key] = converted
        return coerced
    try:
        columns = mapper.columns
    except (AttributeError, TypeError):
        columns = None
    if columns is None:
        for key in _UUID_VALUE_FIELDS:
            if key not in coerced:
                continue
            converted = _as_uuid(coerced[key])
            if converted is not None:
                coerced[key] = converted
        return coerced
    for key in _UUID_VALUE_FIELDS:
        if key not in coerced:
            continue
        try:
            column = columns[key]
            python_type = column.type.python_type
        except (AttributeError, KeyError, NotImplementedError, TypeError):
            continue
        value = coerced[key]
        if python_type is UUID:
            converted = _as_uuid(value)
            if converted is not None:
                coerced[key] = converted
        elif python_type is str and value is not None and not isinstance(value, str):
            coerced[key] = str(value)
    return coerced


def _build_model(model: Any, values: Mapping[str, Any]) -> Any:
    fields = _model_fields(model)
    filtered = dict(values) if fields is None else {
        key: value for key, value in values.items() if key in fields
    }
    try:
        return model(**filtered)
    except TypeError:
        # 少数测试替身只支持无参构造；为其提供同样的字段语义。
        instance = model()
        for key, value in filtered.items():
            try:
                setattr(instance, key, value)
            except (AttributeError, TypeError):
                continue
        return instance


def _target_model(registry: ModelRegistry, name: str) -> Any:
    model = registry.target.get(name)
    if model is None:
        raise MigrationConfigurationError(f"目标模型未注册：{name}")
    return model


def _payload_for_target(
    registry: ModelRegistry,
    target_type: str,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    """按目标模型实际声明过滤可选字段，避免迁移计划静默写入未知列。

    Schema 子任务可能把 JSON/时间字段命名为 ``*_json`` 或短名；迁移器
    只在目标模型声明该字段时保留它，因此静态契约测试和不同 ORM 替身都能
    复用同一份映射逻辑。
    """

    fields = _model_fields(registry.target.get(target_type))
    if fields is None:
        # 测试替身通常只有 ``__init__(**values)``，没有 ORM mapper；使用当前
        # 第二批 Schema 的字段白名单，仍能防止别名字段进入迁移计划。真实
        # SQLAlchemy 模型有 mapper 时优先采用其最新字段集合。
        fields = _FALLBACK_TARGET_FIELDS.get(target_type)
    if fields is None:
        return dict(values)
    return {key: value for key, value in values.items() if key in fields}


_FALLBACK_TARGET_FIELDS: dict[str, set[str]] = {
    "CandidateSet": {
        "id", "goal_id", "skill_execution_id", "user_id", "molecule_class", "iteration",
        "generation_config_json", "generation_config_hash", "generator", "generator_version",
        "generation_task_id", "parent_set_id", "legacy_candidate_track_id", "summary_json", "status",
        "created_at", "updated_at",
    },
    "ScientificCandidate": {
        "id", "candidate_set_id", "goal_id", "skill_execution_id", "user_id", "target_id",
        "representation", "representation_hash", "length", "generator", "generator_version",
        "generation_task_id", "generation_attempt_id", "raw_artifact_id", "parent_candidate_id", "seed",
        "parameter_hash", "generation_metrics_json", "metadata_json", "status", "legacy_candidate_id",
        "created_at", "updated_at",
    },
    "ScoreRun": {
        "id", "candidate_set_id", "goal_id", "skill_execution_id", "user_id", "config_version",
        "config_json", "config_hash", "input_membership_hash", "threshold", "status", "candidate_count",
        "scored_count", "selected_count", "failed_count", "error_type", "error_message", "started_at",
        "finished_at", "legacy_snapshot_key", "created_at",
    },
    "CandidateScoreResult": {
        "id", "score_run_id", "candidate_id", "user_id", "raw_metrics_json", "normalized_metrics_json",
        "total_score", "rank", "selection_status", "selection_reason", "evidence_id", "created_at",
    },
    "StructurePredictionRun": {
        "id", "candidate_id", "target_id", "goal_id", "skill_execution_id", "user_id", "scientific_task_id",
        "attempt_id", "evidence_id", "request_config_json", "request_config_hash", "model", "model_version",
        "seed", "structure_artifact_id", "result_artifact_id", "resource_json", "resource_usage_json", "status",
        "error_type", "error_message", "legacy_af3_task_id", "idempotency_key", "created_at", "started_at",
        "finished_at",
    },
    "DecisionPolicy": {
        "id", "scope_type", "scope_key", "goal_id", "skill_id", "skill_execution_id", "user_id", "algorithm",
        "family", "version", "enabled", "status", "parameters_json", "q_table_json", "action_mask_json",
        "config_json", "metrics_json", "config_hash", "legacy_policy_id", "created_at",
    },
    "StrategyFeedback": {
        "id", "policy_id", "policy_version", "goal_id", "skill_id", "skill_execution_id", "user_id", "turn_id",
        "message_id", "scientific_task_id", "attempt_id", "evidence_id", "state_key", "action", "reward", "reason",
        "next_state_key", "q_before", "q_after", "outcome_json", "tool_call_id", "status", "dedupe_key",
        "legacy_transition_id", "created_at",
    },
}


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _molecule_class(track_row: Any, candidate_row: Any | None = None) -> str:
    raw = _row_value(candidate_row, "molecule_class", "track", default=None)
    if raw is None:
        raw = _row_value(track_row, "molecule_class", "track", default="unknown")
    text = str(raw or "unknown").strip().lower()
    return text or "unknown"


_RNA_ALPHABET = set("ACGUTN")
_DNA_ALPHABET = set("ACGTN")
_PEPTIDE_ALPHABET = set("ACDEFGHIKLMNPQRSTVWYBXZJUO")


def _representation_validation(
    sequence: Any,
    molecule_class: str,
) -> tuple[bool, str, str]:
    """返回验证结果、原因和规范表示；无效原值由调用方另行保留。"""

    if not isinstance(sequence, str) or not sequence.strip():
        return False, "representation_missing", ""
    normalized = re.sub(r"\s+", "", sequence.strip().upper())
    if not normalized or not re.fullmatch(r"[A-Z]+", normalized):
        return False, "representation_non_alphabetic", normalized
    kind = molecule_class.lower()
    if "rna" in kind:
        allowed = _RNA_ALPHABET
    elif "dna" in kind:
        allowed = _DNA_ALPHABET
    elif any(token in kind for token in ("pep", "protein", "vhh", "antibody", "aa")):
        allowed = _PEPTIDE_ALPHABET
    else:
        return False, "molecule_class_unknown", normalized
    if set(normalized) <= allowed:
        return True, "", normalized
    return False, "representation_alphabet_mismatch", normalized


def _candidate_set_status(value: Any) -> tuple[str, bool]:
    """把旧轨道生命周期映射为 CandidateSet 状态，并报告是否可核验。"""

    raw = str(value or "").strip().lower()
    aliases = {
        "pending": CandidateSetStatus.PENDING.value,
        "generating": CandidateSetStatus.RUNNING.value,
        "evaluating": CandidateSetStatus.RUNNING.value,
        "af3_running": CandidateSetStatus.RUNNING.value,
        "ready": CandidateSetStatus.COMPLETED.value,
        "ranked": CandidateSetStatus.COMPLETED.value,
        "completed": CandidateSetStatus.COMPLETED.value,
        "failed": CandidateSetStatus.FAILED.value,
        "cancelled": CandidateSetStatus.CANCELLED.value,
        "canceled": CandidateSetStatus.CANCELLED.value,
    }
    if not raw:
        return CandidateSetStatus.PENDING.value, True
    if raw in aliases:
        return aliases[raw], True
    return CandidateSetStatus.UNVERIFIED.value, False


def _scientific_candidate_status(value: Any) -> tuple[str, bool]:
    """从旧筛选状态派生候选生命周期，不把未知值伪装成已评分。"""

    raw = str(value or "").strip().lower()
    aliases = {
        "": ScientificCandidateStatus.GENERATED.value,
        "pending": ScientificCandidateStatus.GENERATED.value,
        "generated": ScientificCandidateStatus.GENERATED.value,
        "active": ScientificCandidateStatus.ACTIVE.value,
        "qualified": ScientificCandidateStatus.SCORED.value,
        "scored": ScientificCandidateStatus.SCORED.value,
        "selected": ScientificCandidateStatus.SELECTED.value,
        "accepted": ScientificCandidateStatus.SELECTED.value,
        "rejected": ScientificCandidateStatus.REJECTED.value,
        "superseded": ScientificCandidateStatus.SUPERSEDED.value,
    }
    if raw in aliases:
        return aliases[raw], True
    return ScientificCandidateStatus.UNVERIFIED.value, False


def _score_selection_status(value: Any) -> tuple[str, bool]:
    """保留旧评分筛选语义；未知值显式降级。"""

    raw = str(value or "").strip().lower()
    aliases = {
        "": CandidateScoreSelectionStatus.PENDING.value,
        "pending": CandidateScoreSelectionStatus.PENDING.value,
        "qualified": CandidateScoreSelectionStatus.QUALIFIED.value,
        "selected": CandidateScoreSelectionStatus.SELECTED.value,
        "accepted": CandidateScoreSelectionStatus.SELECTED.value,
        "rejected": CandidateScoreSelectionStatus.REJECTED.value,
        "superseded": CandidateScoreSelectionStatus.SUPERSEDED.value,
    }
    if raw in aliases:
        return aliases[raw], True
    return CandidateScoreSelectionStatus.UNVERIFIED.value, False


def _legacy_status(value: Any, *, default: str = "unverified") -> tuple[str, bool]:
    text = str(value).strip().lower() if value is not None else ""
    if not text:
        return default, False
    aliases = {
        "success": "succeeded",
        "completed": "succeeded",
        "complete": "succeeded",
        "canceled": "cancelled",
        "pending": "queued",
    }
    return aliases.get(text, text), True


def _config_key(config_version: Any, config_json: Any) -> tuple[str | None, str, bool]:
    """返回版本、规范哈希和配置是否足够完整；不推断缺失权重。"""

    config = config_json if isinstance(config_json, Mapping) else {}
    version = str(config_version).strip() if config_version is not None else None
    config_hash = _canonical_hash(config)
    complete = bool(config) and bool(version or config.get("version"))
    return version, config_hash, complete


def _new_id(namespace: UUID, value: Any) -> UUID:
    return uuid5(namespace, str(value))


def skill_execution_id(research_run_id: UUID | str) -> UUID:
    """旧 ResearchRun 到 aptamer SkillExecution 的稳定 UUID5。"""

    return _new_id(SKILL_NAMESPACE, f"aptamer_closed_loop:{research_run_id}")


def execution_attempt_id(task_id: UUID | str, attempt_no: int = 1) -> UUID:
    return _new_id(ATTEMPT_NAMESPACE, f"{task_id}:{attempt_no}")


def workflow_checkpoint_id(research_run_id: UUID | str, checkpoint_no: int = 0) -> UUID:
    return _new_id(CHECKPOINT_NAMESPACE, f"{research_run_id}:{checkpoint_no}")


def scientific_evidence_id(artifact_id: UUID | str) -> UUID:
    return _new_id(EVIDENCE_NAMESPACE, str(artifact_id))


def provenance_edge_id(source_id: UUID | str, target_id: UUID | str, relation: str) -> UUID:
    return _new_id(PROVENANCE_NAMESPACE, f"{source_id}:{target_id}:{relation}")


def scientific_target_id(research_run_id: UUID | str, identity_hash: str) -> UUID:
    return _new_id(TARGET_NAMESPACE, f"{research_run_id}:{identity_hash}")


def candidate_set_id(
    candidate_track_id: UUID | str,
    iteration: int,
    molecule_class: str,
) -> UUID:
    """按旧轨道、迭代和分子类别生成 CandidateSet 稳定主键。"""

    return _new_id(
        CANDIDATE_SET_NAMESPACE,
        f"{candidate_track_id}:{int(iteration)}:{str(molecule_class).strip().lower()}",
    )


def score_run_id(candidate_set: UUID | str, config_key: str) -> UUID:
    """为一个候选集和一个不可变评分配置生成稳定 ScoreRun 主键。"""

    return _new_id(SCORE_RUN_NAMESPACE, f"{candidate_set}:{config_key}")


def score_result_id(score_run: UUID | str, candidate_id: UUID | str) -> UUID:
    """为 ScoreRun 中的候选结果生成稳定主键。"""

    return _new_id(SCORE_RUN_NAMESPACE, f"result:{score_run}:{candidate_id}")


def structure_prediction_run_id(candidate_id: UUID | str, task_id: UUID | str | None) -> UUID:
    """为候选的旧 AF3 任务（或缺失任务标识）生成稳定结构预测运行键。"""

    return _new_id(
        STRUCTURE_PREDICTION_NAMESPACE,
        f"{candidate_id}:{task_id if task_id is not None else 'missing-task'}",
    )


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _status(
    value: Any,
    enum_type: Any,
    *,
    aliases: Mapping[str, str] | None = None,
    default: str | None = None,
) -> str | None:
    raw = str(value).strip().lower() if value is not None else ""
    if aliases and raw in aliases:
        raw = aliases[raw]
    if not raw and default is not None:
        raw = default
    try:
        return enum_type(raw).value
    except ValueError:
        return None


def _issue(
    issues: list[MigrationIssue],
    code: str,
    message: str,
    *,
    source_type: str | None = None,
    source_id: UUID | str | None = None,
    target_type: str | None = None,
    target_id: UUID | str | None = None,
    details: Mapping[str, Any] | None = None,
    blocking: bool = True,
) -> None:
    issues.append(
        MigrationIssue(
            code=code,
            message=message,
            blocking=blocking,
            source_type=source_type,
            source_id=_id_text(source_id),
            target_type=target_type,
            target_id=_id_text(target_id),
            details=details or {},
        )
    )


def _append_operation(
    operations: list[MigrationOperation],
    stable_seen: MutableMapping[tuple[str, str], MigrationOperation],
    issues: list[MigrationIssue],
    *,
    source_type: str,
    source_id: UUID | str,
    target_type: str,
    target_id: UUID | str,
    payload: Mapping[str, Any],
    stable_key: str | None = None,
) -> None:
    key = (target_type, str(target_id))
    if key in stable_seen:
        _issue(
            issues,
            "DUPLICATE_STABLE_KEY",
            "多个旧对象生成同一新对象稳定键，迁移被阻断",
            source_type=source_type,
            source_id=source_id,
            target_type=target_type,
            target_id=target_id,
            details={"stable_key": stable_key or str(target_id)},
        )
        return
    operation = MigrationOperation(
        operation="ensure",
        source_type=source_type,
        source_id=str(source_id),
        target_type=target_type,
        target_id=str(target_id),
        # 保留 UUID、datetime 等 ORM 原生值；仅在 as_dict() 时转成 JSON，
        # 否则 DateTime 列会收到字符串而在 flush 阶段失败。
        payload=deepcopy(dict(payload)),
        stable_key=stable_key or str(target_id),
    )
    stable_seen[key] = operation
    operations.append(operation)


def _source_owner(row: Any) -> UUID | None:
    return _as_uuid(_row_value(row, "user_id"))


def _target_payload_common(row: Any) -> dict[str, Any]:
    return {
        "created_at": _row_value(row, "created_at"),
        "updated_at": _row_value(row, "updated_at", default=_row_value(row, "created_at")),
    }


def _build_plan(session: Any, registry: ModelRegistry) -> LegacyMigrationPlan:
    issues: list[MigrationIssue] = []
    operations: list[MigrationOperation] = []
    stable_seen: dict[tuple[str, str], MigrationOperation] = {}
    rows = _collect(registry, session)
    source_counts = {name: len(rows.get(name, ())) for name in LEGACY_MODEL_NAMES}
    user_model_available = registry.legacy.get("User") is not None

    users = _index(rows.get("User", []), "User", issues)
    sessions = _index(rows.get("AgentSession", []), "AgentSession", issues)
    messages = _index(rows.get("AgentMessage", []), "AgentMessage", issues)
    turns = _index(rows.get("AgentTurn", []), "AgentTurn", issues)
    runs = _index(rows.get("ResearchRun", []), "ResearchRun", issues)
    tracks = _index(rows.get("CandidateTrack", []), "CandidateTrack", issues)
    candidates = _index(rows.get("Candidate", []), "Candidate", issues)
    policies = _index(rows.get("StrategyPolicy", []), "StrategyPolicy", issues)
    transitions = _index(rows.get("StrategyTransition", []), "StrategyTransition", issues)
    links = _index(rows.get("ResearchTaskLink", []), "ResearchTaskLink", issues)
    tasks = _index(rows.get("Task", []), "Task", issues)
    leases = _index(
        rows.get("TaskExecutionLease", []),
        "TaskExecutionLease",
        issues,
        id_names=("task_id",),
    )
    retries = _index(
        rows.get("TaskRetry", []),
        "TaskRetry",
        issues,
        id_names=("parent_task_id",),
    )
    artifacts = _index(rows.get("Artifact", []), "Artifact", issues)

    # wzf：第二批已为候选、评分和策略建立目标表；这里不再以
    # ``UNMAPPED_LEGACY_ENTITY`` 阻断，而是在下方执行完整关系核对和映射。

    # AgentSession -> ResearchSession（保留 UUID），并先建立所有权索引供后续检查。
    for session_id, row in sessions.items():
        user_id = _as_uuid(_row_value(row, "user_id"))
        if user_id is None or (user_model_available and user_id not in users):
            _issue(
                issues,
                "ORPHAN_USER_FK",
                "AgentSession 的 user_id 不存在",
                source_type="AgentSession",
                source_id=session_id,
                details={"user_id": _id_text(user_id)},
            )
            continue
        archived_at = _row_value(row, "archived_at")
        status = (
            ResearchSessionStatus.ARCHIVED.value
            if archived_at is not None
            else ResearchSessionStatus.ACTIVE.value
        )
        payload = {
            "id": session_id,
            "user_id": user_id,
            "title": _row_value(row, "title"),
            "status": status,
            "created_at": _row_value(row, "created_at"),
            "updated_at": _row_value(row, "updated_at", default=_row_value(row, "created_at")),
            "archived_at": archived_at,
            "legacy_agent_session_id": session_id,
            "metadata_json": {"migration_source": "agent_sessions"},
        }
        _append_operation(
            operations,
            stable_seen,
            issues,
            source_type="AgentSession",
            source_id=session_id,
            target_type="ResearchSession",
            target_id=session_id,
            payload=payload,
        )

    # AgentMessage -> SessionMessage（UUID 和会话归属均保留）。
    sequence_by_session: defaultdict[UUID, int] = defaultdict(int)
    ordered_messages = sorted(
        messages.items(),
        key=lambda item: (
            _id_text(_row_value(item[1], "session_id")) or "",
            _safe_iso(_row_value(item[1], "created_at")) or "",
            str(item[0]),
        ),
    )
    for message_id, row in ordered_messages:
        session_id = _as_uuid(_row_value(row, "session_id"))
        user_id = _as_uuid(_row_value(row, "user_id"))
        if session_id is None or session_id not in sessions:
            _issue(
                issues,
                "MISSING_SESSION",
                "AgentMessage 引用的会话不存在",
                source_type="AgentMessage",
                source_id=message_id,
                details={"session_id": _id_text(session_id)},
            )
            continue
        owner_id = _source_owner(sessions[session_id])
        if owner_id is not None and user_id != owner_id:
            _issue(
                issues,
                "CROSS_USER_OWNERSHIP",
                "AgentMessage 与 AgentSession 所有者不一致",
                source_type="AgentMessage",
                source_id=message_id,
                details={"message_user_id": _id_text(user_id), "session_user_id": _id_text(owner_id)},
            )
            continue
        sequence_by_session[session_id] += 1
        payload = {
            "id": message_id,
            "session_id": session_id,
            "user_id": user_id,
            "role": _row_value(row, "role"),
            "content": _row_value(row, "content", default=""),
            "sequence_no": sequence_by_session[session_id],
            "metadata_json": _row_value(row, "metadata_json", "metadata", default={}) or {},
            "created_at": _row_value(row, "created_at"),
            "legacy_agent_message_id": message_id,
        }
        _append_operation(
            operations,
            stable_seen,
            issues,
            source_type="AgentMessage",
            source_id=message_id,
            target_type="SessionMessage",
            target_id=message_id,
            payload=payload,
        )

    # AgentTurn -> InteractionTurn；明确不把 research_run_id 写入新模型。
    turn_status_aliases = {"completed": "succeeded", "success": "succeeded", "canceled": "cancelled"}
    for turn_id, row in turns.items():
        session_id = _as_uuid(_row_value(row, "session_id"))
        user_id = _as_uuid(_row_value(row, "user_id"))
        user_message_id = _as_uuid(_row_value(row, "user_message_id"))
        assistant_message_id = _as_uuid(_row_value(row, "assistant_message_id"))
        research_run_id = _as_uuid(_row_value(row, "research_run_id"))
        if session_id is None or session_id not in sessions:
            _issue(
                issues,
                "MISSING_SESSION",
                "AgentTurn 引用的会话不存在",
                source_type="AgentTurn",
                source_id=turn_id,
                details={"session_id": _id_text(session_id)},
            )
            continue
        owner_id = _source_owner(sessions[session_id])
        if owner_id is not None and user_id != owner_id:
            _issue(
                issues,
                "CROSS_USER_OWNERSHIP",
                "AgentTurn 与 AgentSession 所有者不一致",
                source_type="AgentTurn",
                source_id=turn_id,
            )
            continue
        if user_message_id is None or user_message_id not in messages:
            _issue(
                issues,
                "ORPHAN_MESSAGE_FK",
                "AgentTurn 的 user_message_id 不存在",
                source_type="AgentTurn",
                source_id=turn_id,
                details={"user_message_id": _id_text(user_message_id)},
            )
            continue
        if assistant_message_id is not None and assistant_message_id not in messages:
            _issue(
                issues,
                "ORPHAN_MESSAGE_FK",
                "AgentTurn 的 assistant_message_id 不存在",
                source_type="AgentTurn",
                source_id=turn_id,
                details={"assistant_message_id": _id_text(assistant_message_id)},
            )
            continue
        if research_run_id is not None and research_run_id not in runs:
            _issue(
                issues,
                "ORPHAN_RESEARCH_RUN_FK",
                "AgentTurn 的 research_run_id 不存在",
                source_type="AgentTurn",
                source_id=turn_id,
                details={"research_run_id": _id_text(research_run_id)},
            )
            continue
        if research_run_id is not None:
            run_user_id = _as_uuid(_row_value(runs[research_run_id], "user_id"))
            if run_user_id is not None and user_id != run_user_id:
                _issue(
                    issues,
                    "CROSS_USER_OWNERSHIP",
                    "AgentTurn 与 ResearchRun 所有者不一致",
                    source_type="AgentTurn",
                    source_id=turn_id,
                )
                continue
        status = _status(
            _row_value(row, "status"),
            InteractionTurnStatus,
            aliases=turn_status_aliases,
            default=InteractionTurnStatus.QUEUED.value,
        )
        if status is None:
            _issue(
                issues,
                "UNKNOWN_STATUS",
                "AgentTurn.status 不在 InteractionTurnStatus 合法集合中",
                source_type="AgentTurn",
                source_id=turn_id,
                details={"status": _row_value(row, "status")},
            )
            continue
        payload = {
            "id": turn_id,
            "session_id": session_id,
            "user_id": user_id,
            "client_turn_id": _row_value(row, "client_turn_id", default=turn_id),
            "request_hash": _row_value(row, "request_hash", default="legacy"),
            "status": status,
            "user_message_id": user_message_id,
            "assistant_message_id": assistant_message_id,
            "active_session_key": _row_value(row, "active_session_key"),
            "error_code": _row_value(row, "error_code"),
            "lease_token": _row_value(row, "lease_token"),
            "lease_owner": _row_value(row, "lease_owner"),
            "lease_expires_at": _row_value(row, "lease_expires_at"),
            "attempt_no": _row_value(row, "attempt_no", default=0),
            "created_at": _row_value(row, "created_at"),
            "updated_at": _row_value(row, "updated_at", default=_row_value(row, "created_at")),
            "started_at": _row_value(row, "started_at"),
            "finished_at": _row_value(row, "finished_at"),
            "legacy_agent_turn_id": turn_id,
            # 重要：payload 中不存在 research_run_id，防止新模型重新依赖旧运行。
        }
        _append_operation(
            operations,
            stable_seen,
            issues,
            source_type="AgentTurn",
            source_id=turn_id,
            target_type="InteractionTurn",
            target_id=turn_id,
            payload=payload,
        )

    # ResearchRun -> ResearchGoal + aptamer_closed_loop SkillExecution + checkpoint。
    run_status_aliases = {
        "success": "completed",
        "succeeded": "completed",
        # Goal 没有 failed 终态；一次旧运行失败只能保留为可恢复 paused。
        "failed": "paused",
        "cancelled": "abandoned",
        "canceled": "abandoned",
        "archived": "abandoned",
    }
    valid_run_statuses = {item.value for item in ResearchGoalStatus}
    skill_status_aliases = {
        "draft": "planned",
        "active": "running",
        "completed": "completed",
        "success": "completed",
        "succeeded": "completed",
        "failed": "paused",
        "cancelled": "cancelled",
        "canceled": "cancelled",
        "abandoned": "cancelled",
        "archived": "cancelled",
    }
    for run_id, row in runs.items():
        session_id = _as_uuid(_row_value(row, "session_id"))
        user_id = _as_uuid(_row_value(row, "user_id"))
        if user_id is None or (user_model_available and user_id not in users):
            _issue(
                issues,
                "ORPHAN_USER_FK",
                "ResearchRun 的 user_id 不存在",
                source_type="ResearchRun",
                source_id=run_id,
            )
            continue
        if session_id is not None and session_id not in sessions:
            _issue(
                issues,
                "MISSING_SESSION",
                "ResearchRun 的 session_id 不存在",
                source_type="ResearchRun",
                source_id=run_id,
                details={"session_id": _id_text(session_id)},
            )
            continue
        raw_status = str(_row_value(row, "status", default="draft")).lower()
        goal_status = _status(
            raw_status,
            ResearchGoalStatus,
            aliases=run_status_aliases,
            default=ResearchGoalStatus.DRAFT.value,
        )
        if goal_status is None:
            _issue(
                issues,
                "UNKNOWN_STATUS",
                "ResearchRun.status 无法映射为 ResearchGoalStatus",
                source_type="ResearchRun",
                source_id=run_id,
                details={"status": raw_status, "allowed": sorted(valid_run_statuses)},
            )
            continue
        skill_id = skill_execution_id(run_id)
        target_json = _row_value(row, "target_json", "target", default={}) or {}
        metadata_json = _row_value(row, "metadata_json", "metadata", default={}) or {}
        legacy_objective = (
            metadata_json.get("objective")
            if isinstance(metadata_json, Mapping)
            else None
        )
        if not isinstance(legacy_objective, str) or not legacy_objective.strip():
            legacy_objective = _row_value(row, "title") or "迁移自旧 ResearchRun 的适配体科研目标"
        goal_payload = {
            "id": run_id,
            "user_id": user_id,
            "title": _row_value(row, "title", default="Legacy research goal"),
            "objective": str(legacy_objective),
            "status": goal_status,
            "scope_json": {"target": _json_value(target_json)},
            "constraints_json": {},
            "success_criteria_json": {},
            "legacy_research_run_id": run_id,
            "metadata_json": {
                "migration_source": "research_runs",
                "current_stage": _row_value(row, "current_stage"),
                "legacy_metadata": _json_value(metadata_json),
            },
            "created_at": _row_value(row, "created_at"),
            "updated_at": _row_value(row, "updated_at", default=_row_value(row, "created_at")),
        }
        _append_operation(
            operations,
            stable_seen,
            issues,
            source_type="ResearchRun",
            source_id=run_id,
            target_type="ResearchGoal",
            target_id=run_id,
            payload=goal_payload,
        )
        if target_json:
            identity_hash = None
            if isinstance(metadata_json, Mapping):
                raw_identity_hash = metadata_json.get("target_hash")
                if isinstance(raw_identity_hash, str) and raw_identity_hash.strip():
                    identity_hash = raw_identity_hash.strip()
            if identity_hash is None:
                identity_hash = _canonical_hash(target_json)
            pdb_id = str(target_json.get("pdb_id") or "").strip().upper()
            chain_id = str(
                target_json.get("chain") or target_json.get("chain_id") or ""
            ).strip()
            sequence = target_json.get("sequence") or target_json.get("protein_sequence")
            uniprot_id = str(
                target_json.get("uniprot_id") or target_json.get("accession") or ""
            ).strip()
            if pdb_id:
                identifier = f"pdb:{pdb_id}" + (f"/{chain_id}" if chain_id else "")
                structure_source = f"pdb:{pdb_id}"
            elif uniprot_id:
                identifier = f"uniprot:{uniprot_id}"
                structure_source = None
            elif sequence:
                identifier = f"sha256:{hashlib.sha256(str(sequence).encode('utf-8')).hexdigest()}"
                structure_source = None
            else:
                identifier = f"legacy-research-run:{run_id}"
                structure_source = None
            target_id = scientific_target_id(run_id, identity_hash)
            target_payload = {
                "id": target_id,
                "goal_id": run_id,
                "user_id": user_id,
                "name": str(target_json.get("name") or identifier),
                "target_type": str(target_json.get("target_type") or "protein"),
                "identifier": identifier,
                "chain_id": chain_id or None,
                "sequence": str(sequence) if sequence else None,
                "structure_source": structure_source,
                "structure_version": target_json.get("structure_version"),
                "identity_hash": identity_hash,
                "status": "active",
                "source_json": _json_value(target_json),
                "metadata_json": {"migration_source": "research_runs.target"},
                "created_at": _row_value(row, "created_at"),
                "updated_at": _row_value(
                    row,
                    "updated_at",
                    default=_row_value(row, "created_at"),
                ),
            }
            _append_operation(
                operations,
                stable_seen,
                issues,
                source_type="ResearchRun",
                source_id=run_id,
                target_type="ScientificTarget",
                target_id=target_id,
                payload=target_payload,
            )
        else:
            _issue(
                issues,
                "MISSING_TARGET_SNAPSHOT",
                "ResearchRun 没有靶标快照，未创建 ScientificTarget",
                source_type="ResearchRun",
                source_id=run_id,
                blocking=False,
            )
        if session_id is not None:
            # ResearchGoal 可跨多个会话；会话归属通过独立链接表达。
            link_id = uuid5(
                CHECKPOINT_NAMESPACE,
                f"session-goal:{session_id}:{run_id}",
            )
            link_payload = {
                "id": link_id,
                "session_id": session_id,
                "goal_id": run_id,
                "user_id": user_id,
                "role": "legacy_research_run",
                "created_at": _row_value(row, "created_at"),
            }
            _append_operation(
                operations,
                stable_seen,
                issues,
                source_type="ResearchRun",
                source_id=run_id,
                target_type="SessionGoalLink",
                target_id=link_id,
                payload=link_payload,
            )
        skill_raw_status = _status(
            raw_status,
            SkillExecutionStatus,
            aliases=skill_status_aliases,
            default=SkillExecutionStatus.PLANNED.value,
        )
        if skill_raw_status is None:
            _issue(
                issues,
                "UNKNOWN_STATUS",
                "ResearchRun.status 无法映射为 SkillExecutionStatus",
                source_type="ResearchRun",
                source_id=run_id,
                target_type="SkillExecution",
                target_id=skill_id,
                details={"status": raw_status},
            )
            continue
        skill_payload = {
            "id": skill_id,
            "goal_id": run_id,
            "user_id": user_id,
            "skill_id": "aptamer_closed_loop",
            "skill_version": "legacy-migration-v1",
            "status": skill_raw_status,
            "plan_version": 1,
            "input_json": {"target": _json_value(target_json)},
            "config_json": {"current_stage": _row_value(row, "current_stage")},
            "evidence_contract_json": {"migration": "legacy_artifact_references_only"},
            "legacy_research_run_id": run_id,
            "started_at": _row_value(row, "created_at") if skill_raw_status == "running" else None,
            "finished_at": _row_value(row, "updated_at")
            if skill_raw_status in {"completed", "cancelled"}
            else None,
            "metadata_json": {"migration_source": "research_runs"},
        }
        _append_operation(
            operations,
            stable_seen,
            issues,
            source_type="ResearchRun",
            source_id=run_id,
            target_type="SkillExecution",
            target_id=skill_id,
            payload=skill_payload,
        )
        stage_state = _row_value(row, "stage_state_json", "stage_state", default={}) or {}
        checkpoint_id = workflow_checkpoint_id(run_id, 0)
        checkpoint_payload = {
            "id": checkpoint_id,
            "skill_execution_id": skill_id,
            "checkpoint_no": 0,
            "state_json": _json_value(stage_state),
            "ready_task_ids_json": [],
            "blocked_dependency_ids_json": [],
            "plan_version": 1,
            "status": skill_raw_status,
            "digest": None,
            "metadata_json": {"migration_source": "research_runs"},
        }
        _append_operation(
            operations,
            stable_seen,
            issues,
            source_type="ResearchRun",
            source_id=run_id,
            target_type="WorkflowCheckpoint",
            target_id=checkpoint_id,
            payload=checkpoint_payload,
        )

    # CandidateTrack/Candidate 先核对归属和孤儿 FK，随后在第二批领域段落生成
    # CandidateSet、ScientificCandidate、评分、AF3 和策略反馈对象。
    for track_id, row in tracks.items():
        run_id = _as_uuid(_row_value(row, "research_run_id"))
        user_id = _as_uuid(_row_value(row, "user_id"))
        if run_id is None or run_id not in runs:
            _issue(
                issues,
                "ORPHAN_RESEARCH_RUN_FK",
                "CandidateTrack 的 research_run_id 不存在",
                source_type="CandidateTrack",
                source_id=track_id,
                details={"research_run_id": _id_text(run_id)},
            )
        elif user_id != _as_uuid(_row_value(runs[run_id], "user_id")):
            _issue(
                issues,
                "CROSS_USER_OWNERSHIP",
                "CandidateTrack 与 ResearchRun 所有者不一致",
                source_type="CandidateTrack",
                source_id=track_id,
            )
    for candidate_id, row in candidates.items():
        run_id = _as_uuid(_row_value(row, "research_run_id"))
        track_id = _as_uuid(_row_value(row, "candidate_track_id"))
        user_id = _as_uuid(_row_value(row, "user_id"))
        if run_id is None or run_id not in runs:
            _issue(
                issues,
                "ORPHAN_RESEARCH_RUN_FK",
                "Candidate 的 research_run_id 不存在",
                source_type="Candidate",
                source_id=candidate_id,
                details={"research_run_id": _id_text(run_id)},
            )
        if track_id is None or track_id not in tracks:
            _issue(
                issues,
                "ORPHAN_CANDIDATE_TRACK_FK",
                "Candidate 的 candidate_track_id 不存在",
                source_type="Candidate",
                source_id=candidate_id,
                details={"candidate_track_id": _id_text(track_id)},
            )
        if run_id in runs and user_id != _as_uuid(_row_value(runs[run_id], "user_id")):
            _issue(
                issues,
                "CROSS_USER_OWNERSHIP",
                "Candidate 与 ResearchRun 所有者不一致",
                source_type="Candidate",
                source_id=candidate_id,
            )
        if track_id in tracks:
            track_user_id = _as_uuid(_row_value(tracks[track_id], "user_id"))
            if track_user_id is not None and user_id != track_user_id:
                _issue(
                    issues,
                    "CROSS_USER_OWNERSHIP",
                    "Candidate 与 CandidateTrack 所有者不一致",
                    source_type="Candidate",
                    source_id=candidate_id,
                )
            track_run_id = _as_uuid(_row_value(tracks[track_id], "research_run_id"))
            if run_id is not None and track_run_id is not None and run_id != track_run_id:
                _issue(
                    issues,
                    "CROSS_TRACK_OWNERSHIP",
                    "Candidate 的 ResearchRun 与 CandidateTrack 不一致",
                    source_type="Candidate",
                    source_id=candidate_id,
                )
        parent_id = _as_uuid(_row_value(row, "parent_candidate_id"))
        if parent_id is not None and parent_id not in candidates:
            _issue(
                issues,
                "ORPHAN_PARENT_CANDIDATE",
                "Candidate 的 parent_candidate_id 不存在",
                source_type="Candidate",
                source_id=candidate_id,
                details={"parent_candidate_id": _id_text(parent_id)},
            )
        if parent_id is not None and parent_id in candidates:
            parent_row = candidates[parent_id]
            parent_user_id = _as_uuid(_row_value(parent_row, "user_id"))
            parent_track_id = _as_uuid(_row_value(parent_row, "candidate_track_id"))
            if parent_user_id is not None and user_id != parent_user_id:
                _issue(
                    issues,
                    "CROSS_USER_OWNERSHIP",
                    "Candidate 父候选与子候选所有者不一致",
                    source_type="Candidate",
                    source_id=candidate_id,
                )
            if track_id is not None and parent_track_id is not None and track_id != parent_track_id:
                _issue(
                    issues,
                    "CROSS_TRACK_PARENT",
                    "Candidate 父候选来自不同 CandidateTrack",
                    source_type="Candidate",
                    source_id=candidate_id,
                )
            if track_id in tracks and parent_track_id in tracks:
                if _molecule_class(tracks[track_id], row) != _molecule_class(
                    tracks[parent_track_id], parent_row
                ):
                    _issue(
                        issues,
                        "CROSS_MOLECULE_PARENT",
                        "Candidate 父候选与子候选分子类别不一致",
                        source_type="Candidate",
                        source_id=candidate_id,
                    )

    # 父候选链必须闭合且无环；否则 ORM 在新候选表上会形成不可排序的自引用。
    parent_by_candidate = {
        candidate_id: _as_uuid(_row_value(row, "parent_candidate_id"))
        for candidate_id, row in candidates.items()
        if _row_value(row, "parent_candidate_id") is not None
    }
    visit_state: dict[UUID, int] = {}
    visit_path: list[UUID] = []

    def _visit_candidate_parent(candidate_id: UUID) -> None:
        state = visit_state.get(candidate_id, 0)
        if state == 2:
            return
        if state == 1:
            try:
                start = visit_path.index(candidate_id)
            except ValueError:
                start = 0
            cycle = visit_path[start:] + [candidate_id]
            _issue(
                issues,
                "CYCLIC_CANDIDATE_PARENT",
                "Candidate 父候选链形成循环，无法建立血缘",
                source_type="Candidate",
                source_id=candidate_id,
                details={"candidate_cycle": [str(item) for item in cycle]},
            )
            return
        visit_state[candidate_id] = 1
        visit_path.append(candidate_id)
        parent_id = parent_by_candidate.get(candidate_id)
        if parent_id is not None and parent_id in parent_by_candidate:
            _visit_candidate_parent(parent_id)
        visit_path.pop()
        visit_state[candidate_id] = 2

    for candidate_id in sorted(parent_by_candidate, key=str):
        _visit_candidate_parent(candidate_id)

    # 策略与反馈必须先通过旧所有权/FK 核对，再生成不可变快照。
    policy_scope_seen: dict[tuple[str, int], UUID] = {}
    for policy_id, row in policies.items():
        run_id = _as_uuid(_row_value(row, "research_run_id"))
        user_id = _as_uuid(_row_value(row, "user_id"))
        if run_id is None or run_id not in runs:
            _issue(
                issues,
                "ORPHAN_RESEARCH_RUN_FK",
                "StrategyPolicy 的 research_run_id 不存在",
                source_type="StrategyPolicy",
                source_id=policy_id,
            )
            continue
        run_user_id = _as_uuid(_row_value(runs[run_id], "user_id"))
        if run_user_id is not None and user_id != run_user_id:
            _issue(
                issues,
                "CROSS_USER_OWNERSHIP",
                "StrategyPolicy 与 ResearchRun 所有者不一致",
                source_type="StrategyPolicy",
                source_id=policy_id,
            )

    for transition_id, row in transitions.items():
        policy_id = _as_uuid(_row_value(row, "policy_id"))
        run_id = _as_uuid(_row_value(row, "research_run_id"))
        user_id = _as_uuid(_row_value(row, "user_id"))
        if policy_id is None or policy_id not in policies:
            _issue(
                issues,
                "ORPHAN_STRATEGY_POLICY_FK",
                "StrategyTransition 的 policy_id 不存在",
                source_type="StrategyTransition",
                source_id=transition_id,
            )
        if run_id is None or run_id not in runs:
            _issue(
                issues,
                "ORPHAN_RESEARCH_RUN_FK",
                "StrategyTransition 的 research_run_id 不存在",
                source_type="StrategyTransition",
                source_id=transition_id,
            )
        if run_id in runs:
            run_user_id = _as_uuid(_row_value(runs[run_id], "user_id"))
            if run_user_id is not None and user_id != run_user_id:
                _issue(
                    issues,
                    "CROSS_USER_OWNERSHIP",
                    "StrategyTransition 与 ResearchRun 所有者不一致",
                    source_type="StrategyTransition",
                    source_id=transition_id,
                )
        if policy_id in policies:
            policy_user_id = _as_uuid(_row_value(policies[policy_id], "user_id"))
            policy_run_id = _as_uuid(_row_value(policies[policy_id], "research_run_id"))
            if policy_user_id is not None and user_id != policy_user_id:
                _issue(
                    issues,
                    "CROSS_USER_OWNERSHIP",
                    "StrategyTransition 与 StrategyPolicy 所有者不一致",
                    source_type="StrategyTransition",
                    source_id=transition_id,
                )
            if run_id is not None and policy_run_id is not None and run_id != policy_run_id:
                _issue(
                    issues,
                    "CROSS_POLICY_SCOPE",
                    "StrategyTransition 的 ResearchRun 与 StrategyPolicy 不一致",
                    source_type="StrategyTransition",
                    source_id=transition_id,
                )

    # Link 是旧任务到 ResearchRun/候选的事实源，按 task_id 建立唯一映射。
    link_by_task: dict[UUID, Any] = {}
    for link_id, row in links.items():
        run_id = _as_uuid(_row_value(row, "research_run_id"))
        task_id = _as_uuid(_row_value(row, "task_id"))
        candidate_id = _as_uuid(_row_value(row, "candidate_id"))
        user_id = _as_uuid(_row_value(row, "user_id"))
        if run_id is None or run_id not in runs:
            _issue(
                issues,
                "ORPHAN_RESEARCH_RUN_FK",
                "ResearchTaskLink 的 research_run_id 不存在",
                source_type="ResearchTaskLink",
                source_id=link_id,
            )
        if task_id is None or task_id not in tasks:
            _issue(
                issues,
                "ORPHAN_TASK_FK",
                "ResearchTaskLink 的 task_id 不存在",
                source_type="ResearchTaskLink",
                source_id=link_id,
            )
        if candidate_id is not None and candidate_id not in candidates:
            _issue(
                issues,
                "ORPHAN_CANDIDATE_FK",
                "ResearchTaskLink 的 candidate_id 不存在",
                source_type="ResearchTaskLink",
                source_id=link_id,
            )
        if run_id in runs and user_id != _as_uuid(_row_value(runs[run_id], "user_id")):
            _issue(
                issues,
                "CROSS_USER_OWNERSHIP",
                "ResearchTaskLink 与 ResearchRun 所有者不一致",
                source_type="ResearchTaskLink",
                source_id=link_id,
            )
        if task_id is not None:
            if task_id in link_by_task:
                _issue(
                    issues,
                    "DUPLICATE_STABLE_KEY",
                    "同一旧 Task 有多个 ResearchTaskLink，无法选择唯一科学任务归属",
                    source_type="ResearchTaskLink",
                    source_id=link_id,
                    details={"task_id": _id_text(task_id)},
                )
            else:
                link_by_task[task_id] = row

    task_status_aliases = {"success": "succeeded", "completed": "succeeded", "canceled": "cancelled"}
    attempt_aliases = {
        "success": ExecutionAttemptStatus.SUCCEEDED.value,
        "completed": ExecutionAttemptStatus.SUCCEEDED.value,
        "timedout": ExecutionAttemptStatus.TIMED_OUT.value,
        "timeout": ExecutionAttemptStatus.TIMED_OUT.value,
        "canceled": ExecutionAttemptStatus.CANCELLED.value,
        "pending": ExecutionAttemptStatus.QUEUED.value,
    }
    retry_parent_by_child: dict[UUID, UUID] = {}
    retry_row_by_child: dict[UUID, Any] = {}
    for retry_id, row in retries.items():
        parent_task_id = _as_uuid(_row_value(row, "parent_task_id"))
        child_task_id = _as_uuid(_row_value(row, "child_task_id"))
        if parent_task_id is None or parent_task_id not in tasks:
            _issue(
                issues,
                "ORPHAN_TASK_FK",
                "TaskRetry 的 parent_task_id 不存在",
                source_type="TaskRetry",
                source_id=retry_id,
            )
        if child_task_id is None or child_task_id not in tasks:
            _issue(
                issues,
                "ORPHAN_TASK_FK",
                "TaskRetry 的 child_task_id 不存在",
                source_type="TaskRetry",
                source_id=retry_id,
            )
        if parent_task_id in tasks and child_task_id in tasks:
            parent_user = _as_uuid(_row_value(tasks[parent_task_id], "user_id"))
            child_user = _as_uuid(_row_value(tasks[child_task_id], "user_id"))
            if parent_user != child_user:
                _issue(
                    issues,
                    "CROSS_USER_OWNERSHIP",
                    "TaskRetry 父子任务所有者不一致",
                    source_type="TaskRetry",
                    source_id=retry_id,
                )
            if child_task_id in retry_parent_by_child:
                _issue(
                    issues,
                    "DUPLICATE_STABLE_KEY",
                    "一个子 Task 对应多个重试父级，Attempt lineage 不唯一",
                    source_type="TaskRetry",
                    source_id=retry_id,
                )
            else:
                retry_parent_by_child[child_task_id] = parent_task_id
                retry_row_by_child[child_task_id] = row

    task_contexts: dict[UUID, dict[str, Any]] = {}
    for task_id, row in tasks.items():
        task_session_id = _as_uuid(_row_value(row, "session_id"))
        task_user_id = _as_uuid(_row_value(row, "user_id"))
        if task_session_id is not None and task_session_id not in sessions:
            _issue(
                issues,
                "MISSING_SESSION",
                "Task 的 session_id 不存在",
                source_type="Task",
                source_id=task_id,
                details={"session_id": _id_text(task_session_id)},
            )
            continue
        if task_session_id is not None and task_session_id in sessions:
            session_user_id = _as_uuid(_row_value(sessions[task_session_id], "user_id"))
            if session_user_id is not None and task_user_id != session_user_id:
                _issue(
                    issues,
                    "CROSS_USER_OWNERSHIP",
                    "Task 与 AgentSession 所有者不一致",
                    source_type="Task",
                    source_id=task_id,
                )
                continue
        if task_user_id is None or (user_model_available and task_user_id not in users):
            _issue(
                issues,
                "ORPHAN_USER_FK",
                "Task 的 user_id 不存在",
                source_type="Task",
                source_id=task_id,
            )
            continue
        link = link_by_task.get(task_id)
        if link is None:
            # 普通非科研 Task 不强行伪造 SkillExecution；只在审计中提示，不阻断旧系统继续运行。
            _issue(
                issues,
                "TASK_WITHOUT_RESEARCH_LINK",
                "Task 没有 ResearchTaskLink，本批不建立科学任务映射",
                source_type="Task",
                source_id=task_id,
                blocking=False,
            )
            continue
        run_id = _as_uuid(_row_value(link, "research_run_id"))
        if run_id is None or run_id not in runs:
            continue
        user_id = _as_uuid(_row_value(row, "user_id"))
        link_user_id = _as_uuid(_row_value(link, "user_id"))
        run_user_id = _as_uuid(_row_value(runs[run_id], "user_id"))
        if user_id != run_user_id or link_user_id != run_user_id:
            _issue(
                issues,
                "CROSS_USER_OWNERSHIP",
                "Task、ResearchTaskLink 与 ResearchRun 所有者不一致",
                source_type="Task",
                source_id=task_id,
            )
            continue
        raw_task_status = str(_row_value(row, "status", default="queued")).lower()
        task_status = _status(
            raw_task_status,
            ScientificTaskStatus,
            aliases=task_status_aliases,
            default=ScientificTaskStatus.QUEUED.value,
        )
        if task_status is None:
            _issue(
                issues,
                "UNKNOWN_STATUS",
                "Task.status 不在 ScientificTaskStatus 合法集合中",
                source_type="Task",
                source_id=task_id,
                details={"status": raw_task_status},
            )
            continue
        attempt_status = _status(
            raw_task_status,
            ExecutionAttemptStatus,
            aliases=attempt_aliases,
            default=ExecutionAttemptStatus.INTERRUPTED.value,
        )
        if attempt_status is None:
            _issue(
                issues,
                "UNKNOWN_STATUS",
                "Task.status 无法映射为 ExecutionAttemptStatus",
                source_type="Task",
                source_id=task_id,
                details={"status": raw_task_status},
            )
            continue
        task_contexts[task_id] = {
            "row": row,
            "link": link,
            "run_id": run_id,
            "user_id": user_id,
            "task_status": task_status,
            "attempt_status": attempt_status,
            "raw_status": raw_task_status,
        }

    # 旧系统每次重试都会新建 Task；新 Harness 必须把整条链聚合为一个
    # ScientificTask，并把每个旧 Task 还原为顺序明确的 ExecutionAttempt。
    lineage_by_task: dict[UUID, tuple[UUID, int]] = {}
    for task_id, context in task_contexts.items():
        reverse_lineage = [task_id]
        seen = {task_id}
        current_id = task_id
        cyclic = False
        while current_id in retry_parent_by_child:
            parent_id = retry_parent_by_child[current_id]
            if parent_id in seen:
                _issue(
                    issues,
                    "CYCLIC_ATTEMPT_LINEAGE",
                    "TaskRetry 形成循环，无法计算 Attempt 顺序",
                    source_type="Task",
                    source_id=task_id,
                    details={"cycle_entry_task_id": str(parent_id)},
                )
                cyclic = True
                break
            reverse_lineage.append(parent_id)
            seen.add(parent_id)
            current_id = parent_id
        if cyclic:
            continue
        lineage = tuple(reversed(reverse_lineage))
        missing = [item for item in lineage if item not in task_contexts]
        if missing:
            _issue(
                issues,
                "ORPHAN_ATTEMPT_LINEAGE",
                "TaskRetry 父任务未进入本次 Harness 迁移，不能建立完整 Attempt 血缘",
                source_type="Task",
                source_id=task_id,
                details={"unmigrated_task_ids": [str(item) for item in missing]},
            )
            continue

        root_context = task_contexts[lineage[0]]
        context_signature = (
            root_context["run_id"],
            _row_value(root_context["link"], "role"),
            _as_uuid(_row_value(root_context["link"], "candidate_id")),
            _row_value(root_context["row"], "task_type"),
        )
        if any(
            (
                task_contexts[item]["run_id"],
                _row_value(task_contexts[item]["link"], "role"),
                _as_uuid(_row_value(task_contexts[item]["link"], "candidate_id")),
                _row_value(task_contexts[item]["row"], "task_type"),
            )
            != context_signature
            for item in lineage[1:]
        ):
            _issue(
                issues,
                "RETRY_CONTEXT_MISMATCH",
                "TaskRetry 链跨越 ResearchRun、角色、候选或任务类型，不能聚合为一个 ScientificTask",
                source_type="Task",
                source_id=task_id,
                details={"lineage_task_ids": [str(item) for item in lineage]},
            )
            continue
        lineage_by_task[task_id] = (lineage[0], len(lineage))

    task_ids_by_root: defaultdict[UUID, list[UUID]] = defaultdict(list)
    for task_id, (root_task_id, _attempt_no) in lineage_by_task.items():
        task_ids_by_root[root_task_id].append(task_id)

    for root_task_id in sorted(task_ids_by_root, key=str):
        chain_task_ids = sorted(
            task_ids_by_root[root_task_id],
            key=lambda item: lineage_by_task[item][1],
        )
        root_context = task_contexts[root_task_id]
        latest_context = task_contexts[chain_task_ids[-1]]
        root_row = root_context["row"]
        root_link = root_context["link"]
        task_payload = {
            "id": root_task_id,
            "skill_execution_id": skill_execution_id(root_context["run_id"]),
            "user_id": root_context["user_id"],
            "task_key": f"legacy:{root_task_id}",
            "name": _row_value(
                root_link,
                "role",
                default=_row_value(root_row, "task_type", default="legacy_task"),
            ),
            "task_type": _row_value(root_row, "task_type", default="legacy_task"),
            "status": latest_context["task_status"],
            "priority": 0,
            "queue_name": None,
            "available_at": _row_value(latest_context["row"], "created_at"),
            "scheduled_at": _row_value(root_row, "created_at"),
            "input_json": _row_value(root_row, "input_json", "input", default={}) or {},
            "completion_contract_json": {},
            "resource_request_json": {},
            "metadata_json": {
                "migration_source": "tasks",
                "legacy_role": _row_value(root_link, "role"),
                "legacy_task_ids": [str(item) for item in chain_task_ids],
                "latest_legacy_task_id": str(chain_task_ids[-1]),
            },
            "legacy_task_id": root_task_id,
            "idempotency_key": f"legacy-task-chain:{root_task_id}",
            "created_at": _row_value(root_row, "created_at"),
            "updated_at": _row_value(
                latest_context["row"],
                "updated_at",
                default=_row_value(latest_context["row"], "created_at"),
            ),
        }
        _append_operation(
            operations,
            stable_seen,
            issues,
            source_type="Task",
            source_id=root_task_id,
            target_type="ScientificTask",
            target_id=root_task_id,
            payload=task_payload,
        )

    task_attempt_ids: dict[UUID, UUID] = {}
    task_scientific_ids: dict[UUID, UUID] = {}
    ordered_attempt_tasks = sorted(
        lineage_by_task,
        key=lambda item: (str(lineage_by_task[item][0]), lineage_by_task[item][1]),
    )
    for task_id in ordered_attempt_tasks:
        context = task_contexts[task_id]
        row = context["row"]
        root_task_id, attempt_no = lineage_by_task[task_id]
        attempt_id = execution_attempt_id(task_id, attempt_no)
        task_attempt_ids[task_id] = attempt_id
        task_scientific_ids[task_id] = root_task_id
        retry_row = retry_row_by_child.get(task_id)
        lease = leases.get(task_id)
        parent_task_id = retry_parent_by_child.get(task_id)
        attempt_payload = {
            "id": attempt_id,
            "scientific_task_id": root_task_id,
            "user_id": context["user_id"],
            "attempt_no": attempt_no,
            "status": context["attempt_status"],
            "idempotency_key": f"legacy-attempt:{task_id}:{attempt_no}",
            "input_json": _row_value(row, "input_json", "input", default={}) or {},
            "output_json": _row_value(row, "output_json", "output", default={}) or {},
            "error_type": _row_value(row, "error_type"),
            "error_message": _row_value(row, "error_message"),
            "worker_id": _row_value(lease, "lease_owner") if lease is not None else None,
            "lease_token": _row_value(lease, "lease_token") if lease is not None else _row_value(row, "lease_token"),
            "lease_expires_at": _row_value(lease, "expires_at") if lease is not None else None,
            "available_at": _row_value(row, "created_at"),
            "failure_fingerprint": None,
            "created_at": _row_value(row, "created_at"),
            "updated_at": _row_value(row, "updated_at", default=_row_value(row, "created_at")),
            "started_at": _row_value(row, "started_at"),
            "finished_at": _row_value(row, "finished_at"),
            "parent_attempt_id": execution_attempt_id(parent_task_id, attempt_no - 1)
            if parent_task_id is not None
            else None,
            "metadata_json": {
                "migration_source": "tasks",
                "legacy_task_id": str(task_id),
                "legacy_status": context["raw_status"],
                "retry_reason": _row_value(retry_row, "reason"),
                "client_retry_id": _id_text(_row_value(retry_row, "client_retry_id")),
            },
        }
        _append_operation(
            operations,
            stable_seen,
            issues,
            source_type="Task",
            source_id=task_id,
            target_type="ExecutionAttempt",
            target_id=attempt_id,
            payload=attempt_payload,
        )

    # Lease/Retry 的 FK 仍需独立审计，即使对应 Task 未进入新映射。
    for lease_id, row in leases.items():
        task_id = _as_uuid(_row_value(row, "task_id"))
        if task_id is None or task_id not in tasks:
            _issue(
                issues,
                "ORPHAN_TASK_FK",
                "TaskExecutionLease 的 task_id 不存在",
                source_type="TaskExecutionLease",
                source_id=lease_id,
            )

    # wzf：TaskRetry 只有在父子 Task 都实际迁移时才能成为 Attempt 血缘。
    # 旧行“存在”不等于新目标行“会创建”；缺父或循环必须在 apply 前阻断。
    _validate_attempt_lineage(operations, issues)

    # Artifact 只生成证据/来源引用，不读取或复制 object_key 指向的文件。
    for artifact_id, row in artifacts.items():
        user_id = _as_uuid(_row_value(row, "user_id"))
        session_id = _as_uuid(_row_value(row, "session_id"))
        task_id = _as_uuid(_row_value(row, "task_id"))
        if user_id is None or (user_model_available and user_id not in users):
            _issue(
                issues,
                "ORPHAN_USER_FK",
                "Artifact 的 user_id 不存在",
                source_type="Artifact",
                source_id=artifact_id,
            )
            continue
        if session_id is not None and session_id not in sessions:
            _issue(
                issues,
                "MISSING_SESSION",
                "Artifact 的 session_id 不存在",
                source_type="Artifact",
                source_id=artifact_id,
            )
            continue
        if task_id is not None and task_id not in tasks:
            _issue(
                issues,
                "ORPHAN_TASK_FK",
                "Artifact 的 task_id 不存在",
                source_type="Artifact",
                source_id=artifact_id,
            )
            continue
        if task_id is not None and task_id in tasks:
            task_user_id = _as_uuid(_row_value(tasks[task_id], "user_id"))
            if task_user_id != user_id:
                _issue(
                    issues,
                    "CROSS_USER_OWNERSHIP",
                    "Artifact 与 Task 所有者不一致",
                    source_type="Artifact",
                    source_id=artifact_id,
                )
                continue
        if session_id is not None and session_id in sessions:
            session_user_id = _as_uuid(_row_value(sessions[session_id], "user_id"))
            if session_user_id != user_id:
                _issue(
                    issues,
                    "CROSS_USER_OWNERSHIP",
                    "Artifact 与 AgentSession 所有者不一致",
                    source_type="Artifact",
                    source_id=artifact_id,
                )
                continue
        run_id = None
        skill_id = None
        attempt_id = task_attempt_ids.get(task_id) if task_id is not None else None
        migrated_task_id = task_scientific_ids.get(task_id) if task_id is not None else None
        if task_id is not None and task_id in link_by_task:
            run_id = _as_uuid(_row_value(link_by_task[task_id], "research_run_id"))
            if run_id is not None:
                skill_id = skill_execution_id(run_id)
        evidence_id = scientific_evidence_id(artifact_id)
        metadata_json = _row_value(row, "metadata_json", "metadata", default={}) or {}
        content_hash = _row_value(row, "content_hash", "sha256")
        if content_hash is None and isinstance(metadata_json, Mapping):
            content_hash = metadata_json.get("sha256")
        evidence_payload = {
            "id": evidence_id,
            "user_id": user_id,
            "goal_id": run_id,
            "skill_execution_id": skill_id,
            # 仅当旧 Task 已通过 ResearchTaskLink 迁移为 ScientificTask 时建立新 FK。
            "scientific_task_id": migrated_task_id,
            "attempt_id": attempt_id,
            "artifact_id": artifact_id,
            "evidence_type": _row_value(row, "kind", default="legacy_artifact"),
            "status": EvidenceStatus.UNREVIEWED.value,
            "title": _row_value(row, "filename", default="legacy artifact"),
            "statement": "旧 Artifact 引用已迁移；文件内容未复制，需通过 EvidenceGate 重新核验。",
            "structured_data_json": _json_value(metadata_json),
            "source_uri": _row_value(row, "object_key"),
            "content_hash": content_hash,
            "sufficient": False,
            "verified_at": None,
            "reviewer": None,
            "metadata_json": {"migration_source": "artifacts", "file_not_copied": True},
        }
        _append_operation(
            operations,
            stable_seen,
            issues,
            source_type="Artifact",
            source_id=artifact_id,
            target_type="ScientificEvidence",
            target_id=evidence_id,
            payload=evidence_payload,
        )
        edge_id = provenance_edge_id(artifact_id, evidence_id, "migrated_reference")
        edge_payload = {
            "id": edge_id,
            "user_id": user_id,
            "source_type": "artifact",
            "source_id": str(artifact_id),
            "target_type": "scientific_evidence",
            "target_id": str(evidence_id),
            "relation_type": "migrated_reference",
            "metadata_json": {"file_copied": False},
        }
        _append_operation(
            operations,
            stable_seen,
            issues,
            source_type="Artifact",
            source_id=artifact_id,
            target_type="ProvenanceEdge",
            target_id=edge_id,
            payload=edge_payload,
        )

    # ------------------------------------------------------------------
    # 第二批：CandidateTrack/Candidate -> CandidateSet/ScientificCandidate，
    # 评分事实拆为不可变 ScoreRun/Result，AF3 与策略事实分别迁移到新领域表。
    # 所有 payload 先按当前目标模型字段过滤，避免并行 Schema 的命名细微差异
    # 让历史事实在 ORM 构造器中静默丢失。
    # ------------------------------------------------------------------
    goal_ids = {
        _as_uuid(operation.target_id)
        for operation in operations
        if operation.target_type == "ResearchGoal"
    }
    goal_ids.discard(None)
    skill_ids = {
        _as_uuid(operation.target_id)
        for operation in operations
        if operation.target_type == "SkillExecution"
    }
    skill_ids.discard(None)
    target_by_run: dict[UUID, UUID] = {}
    for operation in operations:
        if operation.target_type != "ScientificTarget":
            continue
        target_by_run[_as_uuid(operation.source_id) or UUID(int=0)] = (
            _as_uuid(operation.target_id) or UUID(int=0)
        )
    attempt_ids = {
        operation.source_id: (_as_uuid(operation.target_id) or operation.target_id)
        for operation in operations
        if operation.target_type == "ExecutionAttempt"
    }
    evidence_ids = {
        operation.source_id: (_as_uuid(operation.target_id) or operation.target_id)
        for operation in operations
        if operation.target_type == "ScientificEvidence"
    }
    turn_ids = {
        operation.source_id
        for operation in operations
        if operation.target_type == "InteractionTurn"
    }
    message_ids = {
        operation.source_id
        for operation in operations
        if operation.target_type == "SessionMessage"
    }

    # 每个旧轨道按 iteration 和 molecule_class 分组；空轨道也保留
    # current_iteration 对应的空 CandidateSet，不能因没有候选而丢失生成事实。
    grouped_candidates: defaultdict[tuple[UUID, int, str], list[tuple[UUID, Any]]] = defaultdict(list)
    for candidate_id, row in candidates.items():
        track_id = _as_uuid(_row_value(row, "candidate_track_id"))
        if track_id is None or track_id not in tracks:
            continue
        track_row = tracks[track_id]
        iteration = _safe_int(
            _row_value(row, "iteration", default=_row_value(track_row, "current_iteration", default=0)),
            _safe_int(_row_value(track_row, "current_iteration", default=0)),
        )
        grouped_candidates[(track_id, iteration, _molecule_class(track_row, row))].append(
            (candidate_id, row)
        )
    for track_id, track_row in tracks.items():
        if not any(key[0] == track_id for key in grouped_candidates):
            iteration = _safe_int(_row_value(track_row, "current_iteration", default=0))
            grouped_candidates[(track_id, iteration, _molecule_class(track_row))] = []

    candidate_set_for_candidate: dict[UUID, UUID] = {}
    candidate_row_by_id: dict[UUID, Any] = {}
    candidate_set_members: dict[UUID, list[tuple[UUID, Any]]] = {}
    set_context: dict[UUID, tuple[UUID, UUID, str, int, UUID]] = {}

    for (track_id, iteration, molecule_class), members in sorted(
        grouped_candidates.items(), key=lambda item: (str(item[0][0]), item[0][1], item[0][2])
    ):
        track_row = tracks[track_id]
        run_id = _as_uuid(_row_value(track_row, "research_run_id"))
        user_id = _as_uuid(_row_value(track_row, "user_id"))
        if run_id is None or run_id not in runs:
            # 早期 CandidateTrack 核对已经登记阻断问题；这里避免再造悬空 FK。
            continue
        goal_id = run_id
        skill_id = skill_execution_id(run_id)
        if goal_id not in goal_ids or skill_id not in skill_ids:
            _issue(
                issues,
                "MISSING_CANDIDATE_SCOPE",
                "CandidateTrack 缺少可迁移的 ResearchGoal 或 SkillExecution",
                source_type="CandidateTrack",
                source_id=track_id,
                details={"goal_id": str(goal_id), "skill_execution_id": str(skill_id)},
            )
            continue
        scientific_target = target_by_run.get(run_id)
        config: Mapping[str, Any] = {}
        generation_config_verified = True
        raw_track_config = _row_value(
            track_row,
            "generation_config_json",
            "generation_config",
            default={},
        )
        if isinstance(raw_track_config, Mapping):
            config = raw_track_config
        elif raw_track_config:
            generation_config_verified = False
            _issue(
                issues,
                "GENERATION_CONFIG_UNVERIFIED",
                "CandidateTrack generation 配置不是 JSON 对象，按原值保留并降级",
                source_type="CandidateTrack",
                source_id=track_id,
                blocking=False,
            )
        candidate_configs: dict[str, Mapping[str, Any]] = {}
        for candidate_id, candidate_row in members:
            metadata = _row_value(candidate_row, "metadata_json", "metadata", default={})
            candidate_config = (
                metadata.get("generation_config")
                if isinstance(metadata, Mapping)
                else None
            )
            if isinstance(candidate_config, Mapping) and candidate_config:
                candidate_configs[_canonical_hash(candidate_config)] = candidate_config
            elif candidate_config is not None:
                generation_config_verified = False
                _issue(
                    issues,
                    "GENERATION_CONFIG_UNVERIFIED",
                    "Candidate generation_config 不是 JSON 对象，原 metadata 保留并降级",
                    source_type="Candidate",
                    source_id=candidate_id,
                    blocking=False,
                )
        if config:
            track_config_hash = _canonical_hash(config)
            if any(item_hash != track_config_hash for item_hash in candidate_configs):
                _issue(
                    issues,
                    "CANDIDATE_SET_GENERATION_CONFLICT",
                    "同一候选集合包含与轨道配置不一致的 generation_config，禁止静默合并",
                    source_type="CandidateTrack",
                    source_id=track_id,
                    details={
                        "track_config_hash": track_config_hash,
                        "candidate_config_hashes": sorted(candidate_configs),
                    },
                )
        elif len(candidate_configs) == 1:
            config = next(iter(candidate_configs.values()))
        elif len(candidate_configs) > 1:
            _issue(
                issues,
                "CANDIDATE_SET_GENERATION_CONFLICT",
                "同一候选集合包含多套 generation_config，禁止选择第一条覆盖其他事实",
                source_type="CandidateTrack",
                source_id=track_id,
                details={"candidate_config_hashes": sorted(candidate_configs)},
            )
            config = candidate_configs[sorted(candidate_configs)[0]]
        if not config:
            generation_config_verified = False
            _issue(
                issues,
                "GENERATION_CONFIG_UNVERIFIED",
                "候选集合缺少可核验 generation_config；未推断生成参数",
                source_type="CandidateTrack",
                source_id=track_id,
                blocking=False,
            )
        config_hash = _canonical_hash(config)
        generator_names = {
            str(_row_value(row, "generator_name", "generator", default="legacy"))
            for _candidate_id, row in members
            if _row_value(row, "generator_name", "generator", default=None) is not None
        }
        if len(generator_names) > 1:
            _issue(
                issues,
                "CANDIDATE_SET_GENERATOR_CONFLICT",
                "同一候选集合包含多个生成器，禁止选择第一条覆盖来源事实",
                source_type="CandidateTrack",
                source_id=track_id,
                details={"generators": sorted(generator_names)},
            )
        generator = sorted(generator_names)[0] if generator_names else "legacy-unknown"
        generator_versions = {
            str(_row_value(row, "generator_version", default=""))
            for _candidate_id, row in members
            if _row_value(row, "generator_version", default=None) is not None
        }
        if len(generator_versions) > 1:
            _issue(
                issues,
                "CANDIDATE_SET_GENERATOR_CONFLICT",
                "同一候选集合包含多个生成器版本，禁止静默合并",
                source_type="CandidateTrack",
                source_id=track_id,
                details={"generator_versions": sorted(generator_versions)},
            )
        generator_version = sorted(generator_versions)[0] if generator_versions else None
        generation_tasks = {
            _as_uuid(_row_value(row, "generation_task_id"))
            for _candidate_id, row in members
            if _row_value(row, "generation_task_id") is not None
        }
        generation_tasks.discard(None)
        mapped_generation_tasks = {
            task_scientific_ids.get(task_id)
            for task_id in generation_tasks
            if task_id in task_scientific_ids
        }
        mapped_generation_tasks.discard(None)
        set_generation_task = next(iter(mapped_generation_tasks), None) if len(mapped_generation_tasks) == 1 else None
        if len(generation_tasks) > 1:
            _issue(
                issues,
                "CANDIDATE_SET_GENERATION_TASK_AMBIGUOUS",
                "同一 CandidateSet 引用多个 generation task，集合级任务归属留空，候选级事实保留",
                source_type="CandidateTrack",
                source_id=track_id,
                blocking=False,
            )
        for task_id in generation_tasks:
            if task_id not in tasks:
                _issue(
                    issues,
                    "ORPHAN_GENERATION_TASK_FK",
                    "Candidate 的 generation_task_id 不存在",
                    source_type="Candidate",
                    details={"generation_task_id": _id_text(task_id)},
                )
            elif task_id not in task_scientific_ids:
                _issue(
                    issues,
                    "ORPHAN_GENERATION_TASK_FK",
                    "Candidate 的 generation task 未迁移为 ScientificTask",
                    source_type="Candidate",
                    details={"generation_task_id": _id_text(task_id)},
                )
        legacy_track_status = str(_row_value(track_row, "status", default="") or "").strip().lower()
        set_status, status_verified = _candidate_set_status(legacy_track_status)
        if not status_verified:
            _issue(
                issues,
                "CANDIDATE_SET_STATUS_UNVERIFIED",
                "CandidateTrack.status 无法确认，保留轨道事实并降级",
                source_type="CandidateTrack",
                source_id=track_id,
                blocking=False,
            )
        if not generation_config_verified and set_status not in {
            CandidateSetStatus.FAILED.value,
            CandidateSetStatus.CANCELLED.value,
        }:
            set_status = CandidateSetStatus.UNVERIFIED.value
        raw_summary = _row_value(track_row, "summary_json", "summary", default={}) or {}
        summary = dict(raw_summary) if isinstance(raw_summary, Mapping) else {
            "legacy_summary_raw": _json_value(raw_summary)
        }
        summary.update(
            {
                "legacy_track_status": legacy_track_status or None,
                "legacy_score_config": _json_value(
                    _row_value(track_row, "score_config_json", "score_config", default={}) or {}
                ),
            }
        )
        if raw_track_config and not isinstance(raw_track_config, Mapping):
            summary["legacy_generation_config_raw"] = _json_value(raw_track_config)
        set_id = candidate_set_id(track_id, iteration, molecule_class)
        candidate_set_for_candidate.update({candidate_id: set_id for candidate_id, _row in members})
        candidate_set_members[set_id] = members
        set_context[set_id] = (track_id, goal_id, molecule_class, iteration, user_id or UUID(int=0))
        set_payload = _payload_for_target(
            registry,
            "CandidateSet",
            {
                "id": set_id,
                "goal_id": goal_id,
                "skill_execution_id": skill_id,
                "user_id": user_id,
                "scientific_target_id": scientific_target,
                "molecule_class": molecule_class,
                "iteration": iteration,
                "generation_config_json": _json_value(config),
                "generation_config_hash": config_hash,
                "generator": generator,
                "generator_name": generator,
                "generator_version": generator_version,
                "generation_task_id": set_generation_task,
                "parent_set_id": None,
                "legacy_candidate_track_id": track_id,
                "summary_json": _json_value(summary),
                "status": set_status,
                "created_at": _row_value(track_row, "created_at"),
                "updated_at": _row_value(
                    track_row,
                    "updated_at",
                    default=_row_value(track_row, "created_at"),
                ),
            },
        )
        _append_operation(
            operations,
            stable_seen,
            issues,
            source_type="CandidateTrack",
            source_id=track_id,
            target_type="CandidateSet",
            target_id=set_id,
            payload=set_payload,
            stable_key=f"candidate-set:{track_id}:{iteration}:{molecule_class}",
        )

    # Candidate 旧 ID 直接复用；合法序列写入规范表示，原始写法与旧 Schema
    # 版本保留在 metadata。无法验证的表示不被“修正”，并显式降级。
    for set_id, members in candidate_set_members.items():
        track_id, goal_id, molecule_class, _iteration, set_user_id = set_context[set_id]
        run_id = _as_uuid(_row_value(tracks[track_id], "research_run_id"))
        skill_id = skill_execution_id(run_id) if run_id is not None else None
        scientific_target = target_by_run.get(run_id) if run_id is not None else None
        for candidate_id, row in sorted(members, key=lambda item: str(item[0])):
            candidate_row_by_id[candidate_id] = row
            user_id = _as_uuid(_row_value(row, "user_id")) or set_user_id
            sequence = _row_value(row, "sequence", "representation")
            valid_sequence, sequence_issue, normalized_sequence = _representation_validation(
                sequence,
                molecule_class,
            )
            raw_selection_status = _row_value(row, "selection_status", "status")
            candidate_status, candidate_status_verified = _scientific_candidate_status(
                raw_selection_status
            )
            if not candidate_status_verified:
                _issue(
                    issues,
                    "CANDIDATE_STATUS_UNVERIFIED",
                    "Candidate.selection_status 无法映射到新候选生命周期，原值保留并降级",
                    source_type="Candidate",
                    source_id=candidate_id,
                    details={"legacy_selection_status": _json_value(raw_selection_status)},
                    blocking=False,
                )
            if not valid_sequence:
                candidate_status = ScientificCandidateStatus.UNVERIFIED.value
                _issue(
                    issues,
                    "CANDIDATE_REPRESENTATION_UNVERIFIED",
                    "Candidate 序列已保留，但分子字母表无法确认",
                    source_type="Candidate",
                    source_id=candidate_id,
                    details={"reason": sequence_issue, "molecule_class": molecule_class},
                    blocking=sequence_issue == "representation_missing",
                )
            representation = (
                normalized_sequence
                if valid_sequence
                else str(sequence if sequence is not None else "")
            )
            raw_metadata = _row_value(row, "metadata_json", "metadata", default={}) or {}
            metadata = (
                dict(_json_value(raw_metadata))
                if isinstance(raw_metadata, Mapping)
                else {"legacy_metadata_raw": _json_value(raw_metadata)}
            )
            metadata["legacy_schema_version"] = _row_value(row, "schema_version")
            metadata["legacy_selection_status"] = _json_value(raw_selection_status)
            legacy_sequence_length = _safe_int(
                _row_value(row, "sequence_length", "length", default=len(representation)),
                len(representation),
            )
            if valid_sequence and representation != sequence:
                metadata["legacy_representation"] = _json_value(sequence)
            if legacy_sequence_length != len(representation):
                metadata["legacy_sequence_length"] = legacy_sequence_length
            parent_id = _as_uuid(_row_value(row, "parent_candidate_id"))
            if parent_id is not None and parent_id not in candidate_set_for_candidate:
                _issue(
                    issues,
                    "ORPHAN_PARENT_CANDIDATE",
                    "Candidate 父候选没有生成目标 CandidateSet",
                    source_type="Candidate",
                    source_id=candidate_id,
                )
            generation_task_id = _as_uuid(_row_value(row, "generation_task_id"))
            scientific_generation_task = (
                task_scientific_ids.get(generation_task_id)
                if generation_task_id is not None
                else None
            )
            generation_attempt = (
                attempt_ids.get(str(generation_task_id))
                if generation_task_id is not None
                else None
            )
            raw_artifact_id = _as_uuid(
                _row_value(row, "raw_artifact_id", "artifact_id")
            )
            raw_artifact_row = artifacts.get(raw_artifact_id) if raw_artifact_id is not None else None
            if raw_artifact_row is not None:
                raw_artifact_user = _as_uuid(_row_value(raw_artifact_row, "user_id"))
                if raw_artifact_user is not None and user_id is not None and raw_artifact_user != user_id:
                    _issue(
                        issues,
                        "CROSS_USER_OWNERSHIP",
                        "Candidate 的 raw_artifact 与候选所有者不一致",
                        source_type="Candidate",
                        source_id=candidate_id,
                    )
            if raw_artifact_id is not None and raw_artifact_id not in artifacts:
                _issue(
                    issues,
                    "ORPHAN_RAW_ARTIFACT_FK",
                    "Candidate 的 raw_artifact_id 不存在",
                    source_type="Candidate",
                    source_id=candidate_id,
                    details={"raw_artifact_id": _id_text(raw_artifact_id)},
                )
                raw_artifact_id = None
            if generation_task_id is not None and scientific_generation_task is None:
                _issue(
                    issues,
                    "ORPHAN_GENERATION_TASK_FK",
                    "Candidate 的 generation_task_id 未形成新 ScientificTask 引用",
                    source_type="Candidate",
                    source_id=candidate_id,
                )
            representation_hash = hashlib.sha256(
                representation.encode("utf-8")
            ).hexdigest()
            candidate_payload = _payload_for_target(
                registry,
                "ScientificCandidate",
                {
                    "id": candidate_id,
                    "candidate_set_id": set_id,
                    "goal_id": goal_id,
                    "skill_execution_id": skill_id,
                    "user_id": user_id,
                    "target_id": scientific_target,
                    "scientific_target_id": scientific_target,
                    "representation": representation,
                    "representation_hash": representation_hash,
                    "length": len(representation),
                    "sequence_length": _safe_int(
                        _row_value(row, "sequence_length", "length", default=len(representation))
                    ),
                    "generator": _row_value(row, "generator_name", "generator", default="legacy-candidate"),
                    "generator_name": _row_value(row, "generator_name", "generator", default="legacy-candidate"),
                    "generator_version": _row_value(row, "generator_version"),
                    "generation_task_id": scientific_generation_task,
                    "generation_attempt_id": generation_attempt,
                    "raw_artifact_id": raw_artifact_id,
                    "parent_candidate_id": parent_id,
                    "seed": _row_value(row, "seed"),
                    "parameter_hash": _row_value(row, "parameters_hash", "parameter_hash"),
                    "parameters_hash": _row_value(row, "parameters_hash", "parameter_hash"),
                    "generation_metrics_json": _json_value(
                        _row_value(row, "generation_metrics_json", "raw_metrics_json", "raw_metrics", default={}) or {}
                    ),
                    "metadata_json": _json_value(metadata),
                    "status": candidate_status,
                    "legacy_candidate_id": candidate_id,
                    "created_at": _row_value(row, "created_at"),
                    "updated_at": _row_value(row, "updated_at", default=_row_value(row, "created_at")),
                },
            )
            _append_operation(
                operations,
                stable_seen,
                issues,
                source_type="Candidate",
                source_id=candidate_id,
                target_type="ScientificCandidate",
                target_id=candidate_id,
                payload=candidate_payload,
                stable_key=f"scientific-candidate:{candidate_id}",
            )

    # 评分事实不再留在 Candidate：按 CandidateSet、config version/hash 拆不可变
    # ScoreRun，避免同一候选集的多套历史配置相互覆盖。
    for set_id, members in candidate_set_members.items():
        track_id, goal_id, molecule_class, iteration, set_user_id = set_context[set_id]
        run_id = _as_uuid(_row_value(tracks[track_id], "research_run_id"))
        skill_id = skill_execution_id(run_id) if run_id is not None else None
        scored_members: defaultdict[tuple[str | None, str], list[tuple[UUID, Any, Mapping[str, Any], bool]]] = defaultdict(list)
        for candidate_id, row in members:
            raw_metrics = _row_value(row, "raw_metrics_json", "raw_metrics", default={}) or {}
            normalized_metrics = _row_value(
                row,
                "normalized_metrics_json",
                "normalized_metrics",
                default={},
            ) or {}
            has_score_fact = bool(raw_metrics or normalized_metrics)
            selection_status = str(_row_value(row, "selection_status", default="") or "").strip().lower()
            has_score_fact = has_score_fact or any(
                _row_value(row, key) is not None
                for key in ("total_score", "rank", "score_config_version", "selection_reason")
            )
            has_score_fact = has_score_fact or selection_status not in {"", "pending"}
            if not has_score_fact:
                continue
            track_config = _row_value(
                tracks[track_id],
                "score_config_json",
                "score_config",
                default={},
            )
            config: Mapping[str, Any] = track_config if isinstance(track_config, Mapping) else {}
            metadata = _row_value(row, "metadata_json", "metadata", default={})
            if not config and isinstance(metadata, Mapping) and isinstance(metadata.get("score_config"), Mapping):
                config = metadata["score_config"]
            version = _row_value(row, "score_config_version")
            if version is None and isinstance(config, Mapping):
                version = config.get("version")
            version_text, config_hash, complete = _config_key(version, config)
            scored_members[(version_text, config_hash)].append(
                (candidate_id, row, config, complete)
            )
        for (version_text, config_hash), score_members in sorted(scored_members.items(), key=lambda item: (str(item[0][0]), item[0][1])):
            complete = all(item[3] for item in score_members)
            if not complete:
                _issue(
                    issues,
                    "SCORE_CONFIG_UNVERIFIED",
                    "旧评分配置缺少版本或完整 JSON；原评分值保留但 ScoreRun 降级",
                    source_type="CandidateTrack",
                    source_id=track_id,
                    details={"config_hash": config_hash, "config_version": version_text},
                    blocking=False,
                )
            config_version = version_text or "legacy-unversioned"
            config_json = _json_value(score_members[0][2])
            candidate_ids = [str(item[0]) for item in score_members]
            membership_hash = _canonical_hash(sorted(candidate_ids))
            # 版本标签不是配置身份：同一标签可能被历史代码重复用于不同权重。
            # 稳定 ID 必须同时包含版本、配置哈希和成员快照，避免静默覆盖。
            score_id = score_run_id(
                set_id,
                f"{version_text or 'legacy-unversioned'}:{config_hash}:{membership_hash}",
            )
            score_payload = _payload_for_target(
                registry,
                "ScoreRun",
                {
                    "id": score_id,
                    "candidate_set_id": set_id,
                    "goal_id": goal_id,
                    "skill_execution_id": skill_id,
                    "user_id": set_user_id,
                    "config_version": config_version,
                    "config_json": config_json,
                    "config_hash": config_hash,
                    "input_membership_hash": membership_hash,
                    "threshold": (
                        _safe_float(score_members[0][2].get("threshold"))
                        if isinstance(score_members[0][2], Mapping)
                        else None
                    ),
                    "minimum_total_score": (
                        _safe_float(score_members[0][2].get("minimum_total_score"))
                        if isinstance(score_members[0][2], Mapping)
                        else None
                    ),
                    "status": (
                        ScoreRunStatus.COMPLETED.value
                        if complete
                        else ScoreRunStatus.UNVERIFIED.value
                    ),
                    "candidate_count": len(score_members),
                    "scored_count": sum(
                        1
                        for _candidate_id, row, _config, _complete in score_members
                        if _row_value(row, "total_score") is not None
                    ),
                    "selected_count": sum(
                        1
                        for _candidate_id, row, _config, _complete in score_members
                        if str(_row_value(row, "selection_status", default="")).lower()
                        in {"selected", "accepted"}
                    ),
                    "failed_count": sum(
                        1
                        for _candidate_id, row, _config, _complete in score_members
                        if str(_row_value(row, "selection_status", default="")).lower()
                        in {"failed", "rejected"}
                    ),
                    "counts": {
                        "candidate_count": len(score_members),
                        "scored_count": sum(
                            1
                            for _candidate_id, row, _config, _complete in score_members
                            if _row_value(row, "total_score") is not None
                        ),
                        "selected_count": sum(
                            1
                            for _candidate_id, row, _config, _complete in score_members
                            if str(_row_value(row, "selection_status", default="")).lower()
                            in {"selected", "accepted"}
                        ),
                    },
                    "counts_json": {
                        "candidate_count": len(score_members),
                        "scored_count": sum(
                            1
                            for _candidate_id, row, _config, _complete in score_members
                            if _row_value(row, "total_score") is not None
                        ),
                    },
                    "error_type": None if complete else "legacy_score_config_incomplete",
                    "error_code": None if complete else "legacy_score_config_incomplete",
                    "error_message": None
                    if complete
                    else "历史评分配置缺失或未版本化；未推断权重和阈值",
                    "error": None
                    if complete
                    else {
                        "type": "legacy_score_config_incomplete",
                        "message": "历史评分配置缺失或未版本化；未推断权重和阈值",
                    },
                    "error_json": None
                    if complete
                    else {
                        "type": "legacy_score_config_incomplete",
                        "message": "历史评分配置缺失或未版本化；未推断权重和阈值",
                    },
                    "started_at": _row_value(tracks[track_id], "created_at"),
                    "finished_at": _row_value(tracks[track_id], "updated_at", default=_row_value(tracks[track_id], "created_at")),
                    "legacy_snapshot_key": f"candidate-track:{track_id}:iteration:{iteration}:molecule:{molecule_class}:config:{config_version}",
                    "created_at": _row_value(tracks[track_id], "created_at"),
                },
            )
            _append_operation(
                operations,
                stable_seen,
                issues,
                source_type="CandidateTrack",
                source_id=track_id,
                target_type="ScoreRun",
                target_id=score_id,
                payload=score_payload,
                stable_key=f"score-run:{score_id}",
            )
            for candidate_id, row, _config, _complete in score_members:
                result_id = score_result_id(score_id, candidate_id)
                raw_selection_status = _row_value(row, "selection_status", default="pending")
                selection_status, selection_status_verified = _score_selection_status(
                    raw_selection_status
                )
                if not selection_status_verified:
                    _issue(
                        issues,
                        "SCORE_SELECTION_STATUS_UNVERIFIED",
                        "旧候选筛选状态无法确认，评分原值保留并降级",
                        source_type="Candidate",
                        source_id=candidate_id,
                        details={
                            "legacy_selection_status": _json_value(raw_selection_status),
                            "score_run_id": str(score_id),
                        },
                        blocking=False,
                    )
                raw_rank = _row_value(row, "rank")
                rank = _safe_int(raw_rank, 0) if raw_rank is not None else None
                if rank is not None and rank <= 0:
                    _issue(
                        issues,
                        "SCORE_RANK_UNVERIFIED",
                        "旧候选排名不是正整数，原值留在迁移问题中且新排名置空",
                        source_type="Candidate",
                        source_id=candidate_id,
                        details={"legacy_rank": _json_value(raw_rank)},
                        blocking=False,
                    )
                    rank = None
                result_payload = _payload_for_target(
                    registry,
                    "CandidateScoreResult",
                    {
                        "id": result_id,
                        "score_run_id": score_id,
                        "candidate_id": candidate_id,
                        "user_id": _as_uuid(_row_value(row, "user_id")) or set_user_id,
                        "raw_metrics_json": _json_value(
                            _row_value(row, "raw_metrics_json", "raw_metrics", default={}) or {}
                        ),
                        "normalized_metrics_json": _json_value(
                            _row_value(row, "normalized_metrics_json", "normalized_metrics", default={}) or {}
                        ),
                        "total_score": _safe_float(_row_value(row, "total_score")),
                        "rank": rank,
                        "selection_status": selection_status,
                        "selection_reason": _row_value(row, "selection_reason"),
                        "evidence_id": None,
                        "created_at": _row_value(row, "updated_at", default=_row_value(row, "created_at")),
                    },
                )
                _append_operation(
                    operations,
                    stable_seen,
                    issues,
                    source_type="Candidate",
                    source_id=candidate_id,
                    target_type="CandidateScoreResult",
                    target_id=result_id,
                    payload=result_payload,
                    stable_key=f"score-result:{score_id}:{candidate_id}",
                )

    # AF3 事实迁移为 StructurePredictionRun；缺 task/artifact 仅降级，不丢旧 Candidate。
    for candidate_id, row in candidate_row_by_id.items():
        af3_task_id = _as_uuid(_row_value(row, "af3_task_id", "structure_prediction_task_id"))
        af3_artifact_id = _as_uuid(_row_value(row, "af3_artifact_id", "result_artifact_id"))
        af3_status_raw = _row_value(row, "af3_status", "structure_prediction_status")
        if af3_task_id is None and af3_artifact_id is None and af3_status_raw is None:
            continue
        candidate_set_id_value = candidate_set_for_candidate[candidate_id]
        _track_id, goal_id, _molecule_class_value, _iteration, user_id = set_context[candidate_set_id_value]
        run_id = _as_uuid(_row_value(tracks[_track_id], "research_run_id"))
        skill_id = skill_execution_id(run_id) if run_id is not None else None
        target_id = target_by_run.get(run_id) if run_id is not None else None
        if target_id is None:
            _issue(
                issues,
                "MISSING_SCIENTIFIC_TARGET",
                "AF3 事实缺少可引用的 ScientificTarget",
                source_type="Candidate",
                source_id=candidate_id,
            )
            continue
        task_row = tasks.get(af3_task_id) if af3_task_id is not None else None
        artifact_row = artifacts.get(af3_artifact_id) if af3_artifact_id is not None else None
        if task_row is not None and _as_uuid(_row_value(task_row, "user_id")) not in {None, user_id}:
            _issue(
                issues,
                "CROSS_USER_OWNERSHIP",
                "AF3 Task 与 Candidate 所有者不一致",
                source_type="Candidate",
                source_id=candidate_id,
            )
        if artifact_row is not None and _as_uuid(_row_value(artifact_row, "user_id")) not in {None, user_id}:
            _issue(
                issues,
                "CROSS_USER_OWNERSHIP",
                "AF3 Artifact 与 Candidate 所有者不一致",
                source_type="Candidate",
                source_id=candidate_id,
            )
        missing_reasons: list[str] = []
        if af3_task_id is None or task_row is None:
            missing_reasons.append("task")
            _issue(
                issues,
                "AF3_TASK_MISSING",
                "AF3 任务缺失，保留 StructurePredictionRun 并标记 missing_artifact",
                source_type="Candidate",
                source_id=candidate_id,
                blocking=False,
            )
        if af3_artifact_id is None or artifact_row is None:
            missing_reasons.append("artifact")
            _issue(
                issues,
                "AF3_ARTIFACT_MISSING",
                "AF3 产物缺失，保留 StructurePredictionRun 并标记 missing_artifact",
                source_type="Candidate",
                source_id=candidate_id,
                blocking=False,
            )
        request_config = (
            _row_value(task_row, "input_json", "input", default={})
            if task_row is not None
            else _row_value(row, "metadata_json", "metadata", default={})
        )
        request_config = request_config if isinstance(request_config, Mapping) else {}
        model_name = (
            request_config.get("model_name")
            or request_config.get("model")
            or _row_value(row, "model_name", "model")
            or "legacy-af3"
        )
        model_version = request_config.get("model_version") or _row_value(row, "model_version")
        mapped_task_id = task_scientific_ids.get(af3_task_id) if af3_task_id is not None else None
        mapped_attempt_id = attempt_ids.get(str(af3_task_id)) if af3_task_id is not None else None
        status, status_present = _legacy_status(af3_status_raw, default="queued")
        if missing_reasons:
            status = "missing_artifact"
        elif status not in {"queued", "running", "waiting_for_resource", "blocked", "missing_artifact", "succeeded", "failed", "cancelled"}:
            status = "queued"
            _issue(
                issues,
                "AF3_STATUS_UNVERIFIED",
                "AF3 状态无法确认，保留原引用并降级为 queued",
                source_type="Candidate",
                source_id=candidate_id,
                blocking=False,
            )
        if not status_present and not missing_reasons:
            _issue(
                issues,
                "AF3_STATUS_UNVERIFIED",
                "AF3 状态缺失，保留运行记录并降级",
                source_type="Candidate",
                source_id=candidate_id,
                blocking=False,
            )
        structure_artifact_id = _as_uuid(_row_value(row, "structure_artifact_id"))
        if structure_artifact_id is not None and structure_artifact_id not in artifacts:
            structure_artifact_id = None
        structure_payload = _payload_for_target(
            registry,
            "StructurePredictionRun",
            {
                "id": structure_prediction_run_id(candidate_id, af3_task_id),
                "candidate_id": candidate_id,
                "target_id": target_id,
                "scientific_target_id": target_id,
                "goal_id": goal_id,
                "skill_execution_id": skill_id,
                "user_id": user_id,
                "scientific_task_id": mapped_task_id,
                "attempt_id": mapped_attempt_id,
                "execution_attempt_id": mapped_attempt_id,
                "evidence_id": evidence_ids.get(str(af3_artifact_id)) if af3_artifact_id is not None else None,
                "request_config_json": _json_value(request_config),
                "request_config_hash": _canonical_hash(request_config),
                "model": str(model_name),
                "model_name": str(model_name),
                "model_version": model_version,
                "seed": _row_value(row, "seed"),
                "structure_artifact_id": structure_artifact_id,
                "result_artifact_id": af3_artifact_id if artifact_row is not None else None,
                "resource_json": {},
                "resource_usage_json": {},
                "status": status,
                "error_type": "missing_artifact" if missing_reasons else None,
                "error_message": ("AF3 旧任务/产物缺失：" + ",".join(missing_reasons)) if missing_reasons else None,
                "failure_code": "missing_artifact" if missing_reasons else None,
                "failure_message": ("AF3 旧任务/产物缺失：" + ",".join(missing_reasons)) if missing_reasons else None,
                "legacy_af3_task_id": af3_task_id,
                "idempotency_key": f"legacy-af3:{candidate_id}:{af3_task_id or 'missing-task'}",
                "created_at": _row_value(row, "created_at"),
                "started_at": _row_value(task_row, "started_at") if task_row is not None else None,
                "finished_at": _row_value(task_row, "finished_at") if task_row is not None else None,
            },
        )
        _append_operation(
            operations,
            stable_seen,
            issues,
            source_type="Candidate",
            source_id=candidate_id,
            target_type="StructurePredictionRun",
            target_id=structure_prediction_run_id(candidate_id, af3_task_id),
            payload=structure_payload,
            stable_key=f"structure-prediction:{candidate_id}:{af3_task_id or 'missing-task'}",
        )

    # StrategyPolicy -> skill scope 的不可变 DecisionPolicy 快照；Transition ->
    # StrategyFeedback。缺行动 trace/evidence 只降级，缺 policy/owner/goal/skill 阻断。
    for policy_id, row in policies.items():
        run_id = _as_uuid(_row_value(row, "research_run_id"))
        user_id = _as_uuid(_row_value(row, "user_id"))
        if run_id is None or run_id not in runs:
            continue
        skill_id = skill_execution_id(run_id)
        policy_version = _safe_int(_row_value(row, "version"), 1)
        if policy_version <= 0:
            _issue(
                issues,
                "INVALID_POLICY_VERSION",
                "StrategyPolicy.version 必须为正整数，禁止写入不可验证策略快照",
                source_type="StrategyPolicy",
                source_id=policy_id,
                details={"legacy_version": _json_value(_row_value(row, "version"))},
            )
            continue
        scope_signature = (str(skill_id), policy_version)
        if scope_signature in policy_scope_seen and policy_scope_seen[scope_signature] != policy_id:
            _issue(
                issues,
                "DUPLICATE_POLICY_SCOPE",
                "多个旧 StrategyPolicy 映射到同一 skill scope/version，无法选择不可变快照",
                source_type="StrategyPolicy",
                source_id=policy_id,
                details={"scope_key": str(skill_id), "version": policy_version},
            )
            continue
        policy_scope_seen[scope_signature] = policy_id
        enabled = bool(_row_value(row, "enabled", default=True))
        policy_payload = _payload_for_target(
            registry,
            "DecisionPolicy",
            {
                "id": policy_id,
                "scope_type": "skill",
                "scope_key": str(skill_id),
                "goal_id": run_id,
                "skill_id": "aptamer_closed_loop",
                "skill_execution_id": skill_id,
                "user_id": user_id,
                "algorithm": "tabular_q_learning",
                "family": "q_learning",
                "policy_family": "q_learning",
                "version": policy_version,
                "enabled": enabled,
                "status": "active" if enabled else "disabled",
                "parameters_json": {
                    "alpha": _row_value(row, "alpha"),
                    "gamma": _row_value(row, "gamma"),
                    "epsilon": _row_value(row, "epsilon"),
                },
                "q_table_json": _json_value(_row_value(row, "q_table_json", "q_table", default={}) or {}),
                "action_mask_json": _json_value(_row_value(row, "action_mask_json", "action_mask", default={}) or {}),
                "config_json": {"migration_source": "strategy_policies"},
                "metrics_json": _json_value(_row_value(row, "metrics_json", "metrics", default={}) or {}),
                "config_hash": _canonical_hash(
                    {
                        "alpha": _row_value(row, "alpha"),
                        "gamma": _row_value(row, "gamma"),
                        "epsilon": _row_value(row, "epsilon"),
                    }
                ),
                "legacy_policy_id": policy_id,
                "legacy_strategy_policy_id": policy_id,
                "created_at": _row_value(row, "created_at"),
            },
        )
        _append_operation(
            operations,
            stable_seen,
            issues,
            source_type="StrategyPolicy",
            source_id=policy_id,
            target_type="DecisionPolicy",
            target_id=policy_id,
            payload=policy_payload,
            stable_key=f"decision-policy:{policy_id}",
        )

    for transition_id, row in transitions.items():
        policy_id = _as_uuid(_row_value(row, "policy_id"))
        run_id = _as_uuid(_row_value(row, "research_run_id"))
        user_id = _as_uuid(_row_value(row, "user_id"))
        if policy_id is None or policy_id not in policies or run_id is None or run_id not in runs:
            continue
        if policy_id not in {
            _as_uuid(operation.target_id)
            for operation in operations
            if operation.target_type == "DecisionPolicy"
        }:
            continue
        skill_id = skill_execution_id(run_id)
        outcome = _row_value(row, "outcome_json", "outcome", default={}) or {}
        if not isinstance(outcome, Mapping):
            outcome = {}
        action = str(_row_value(row, "action", default="") or "").strip()
        trace_turn_id = _as_uuid(
            outcome.get("interaction_turn_id")
            or outcome.get("turn_id")
            or outcome.get("agent_turn_id")
        )
        trace_message_id = _as_uuid(
            outcome.get("session_message_id")
            or outcome.get("message_id")
            or outcome.get("agent_message_id")
        )
        task_id_from_outcome = _as_uuid(outcome.get("scientific_task_id") or outcome.get("task_id"))
        artifact_id_from_outcome = _as_uuid(outcome.get("evidence_id") or outcome.get("artifact_id"))
        mapped_turn_id = str(trace_turn_id) if trace_turn_id is not None and str(trace_turn_id) in turn_ids else None
        mapped_message_id = str(trace_message_id) if trace_message_id is not None and str(trace_message_id) in message_ids else None
        mapped_task_id = task_scientific_ids.get(task_id_from_outcome) if task_id_from_outcome is not None else None
        mapped_attempt_id = attempt_ids.get(str(task_id_from_outcome)) if task_id_from_outcome is not None else None
        mapped_evidence_id = evidence_ids.get(str(artifact_id_from_outcome)) if artifact_id_from_outcome is not None else None
        policy_version = _safe_int(
            _row_value(row, "policy_version", default=_row_value(policies[policy_id], "version", default=1)),
            1,
        )
        if policy_version <= 0:
            _issue(
                issues,
                "INVALID_POLICY_VERSION",
                "StrategyTransition.policy_version 必须为正整数",
                source_type="StrategyTransition",
                source_id=transition_id,
                details={
                    "legacy_policy_version": _json_value(_row_value(row, "policy_version"))
                },
            )
            continue
        current_policy_version = _safe_int(_row_value(policies[policy_id], "version"), 1)
        policy_snapshot_missing = policy_version != current_policy_version
        if policy_snapshot_missing:
            _issue(
                issues,
                "POLICY_VERSION_SNAPSHOT_MISSING",
                "策略反馈引用的历史 policy_version 没有对应不可变快照；保留版本并降级",
                source_type="StrategyTransition",
                source_id=transition_id,
                details={
                    "feedback_policy_version": policy_version,
                    "available_policy_version": current_policy_version,
                },
                blocking=False,
            )
        reward = _safe_float(_row_value(row, "reward"))
        if reward is None:
            _issue(
                issues,
                "INVALID_STRATEGY_REWARD",
                "StrategyTransition.reward 缺失、非数值或非有限值，禁止伪造奖励",
                source_type="StrategyTransition",
                source_id=transition_id,
                details={"legacy_reward": _json_value(_row_value(row, "reward"))},
            )
            continue
        degraded = (
            not action
            or mapped_turn_id is None and mapped_message_id is None
            or mapped_evidence_id is None
            or policy_snapshot_missing
        )
        if degraded:
            _issue(
                issues,
                "STRATEGY_FEEDBACK_UNVERIFIED",
                "旧策略反馈缺少可核验行动 trace 或 evidence，原值保留并标记 unverified",
                source_type="StrategyTransition",
                source_id=transition_id,
                blocking=False,
            )
        feedback_payload = _payload_for_target(
            registry,
            "StrategyFeedback",
            {
                "id": transition_id,
                "policy_id": policy_id,
                "decision_policy_id": policy_id,
                "policy_version": policy_version,
                "goal_id": run_id,
                "skill_id": "aptamer_closed_loop",
                "skill_execution_id": skill_id,
                "user_id": user_id,
                "turn_id": trace_turn_id if mapped_turn_id is not None else None,
                "interaction_turn_id": trace_turn_id if mapped_turn_id is not None else None,
                "message_id": trace_message_id if mapped_message_id is not None else None,
                "session_message_id": trace_message_id if mapped_message_id is not None else None,
                "scientific_task_id": mapped_task_id,
                "attempt_id": mapped_attempt_id,
                "execution_attempt_id": mapped_attempt_id,
                "evidence_id": mapped_evidence_id,
                "state_key": _row_value(row, "state_key", default="legacy-unverified"),
                "action": action or "legacy-unverified-action",
                "reward": reward,
                "reason": _row_value(row, "reward_reason", "reason"),
                "reward_reason": _row_value(row, "reward_reason", "reason"),
                "next_state_key": _row_value(row, "next_state_key", default="legacy-unverified-next-state"),
                "q_before": _safe_float(_row_value(row, "q_before")),
                "q_after": _safe_float(_row_value(row, "q_after")),
                "outcome_json": _json_value(outcome),
                "tool_call_id": outcome.get("tool_call_id") or _row_value(row, "tool_call_id"),
                "status": (
                    StrategyFeedbackStatus.UNVERIFIED.value
                    if degraded
                    else StrategyFeedbackStatus.RECORDED.value
                ),
                "dedupe_key": f"legacy-strategy-feedback:{transition_id}",
                "legacy_transition_id": transition_id,
                "created_at": _row_value(row, "created_at"),
            },
        )
        _append_operation(
            operations,
            stable_seen,
            issues,
            source_type="StrategyTransition",
            source_id=transition_id,
            target_type="StrategyFeedback",
            target_id=transition_id,
            payload=feedback_payload,
            stable_key=f"strategy-feedback:{transition_id}",
        )

    target_counts = Counter(operation.target_type for operation in operations)
    return LegacyMigrationPlan(
        operations=tuple(operations),
        issues=tuple(issues),
        source_counts=source_counts,
        target_counts=dict(target_counts),
        registry=registry,
    )


def inspect_legacy(session: Any, *, model_registry: Any = None) -> LegacyMigrationInspection:
    """只读检查旧表完整性，不构造目标对象、不 flush、不 commit。"""

    plan = _build_plan(session, _registry_from(model_registry))
    return LegacyMigrationInspection(source_counts=plan.source_counts, issues=plan.issues)


def plan_legacy_migration(session: Any, *, model_registry: Any = None) -> LegacyMigrationPlan:
    """生成可审阅的迁移计划；默认路径严格只读。"""

    return _build_plan(session, _registry_from(model_registry))


def _validate_attempt_lineage(
    operations: Sequence[MigrationOperation],
    issues: list[MigrationIssue],
) -> None:
    """验证迁移计划内 Attempt 父链闭合且无环。"""

    attempts = {
        operation.target_id: operation
        for operation in operations
        if operation.target_type == "ExecutionAttempt"
    }
    parent_by_child: dict[str, str] = {}
    for child_id, operation in attempts.items():
        parent_id = _id_text(operation.payload.get("parent_attempt_id"))
        if parent_id is None:
            continue
        parent_by_child[child_id] = parent_id
        if parent_id not in attempts:
            _issue(
                issues,
                "ORPHAN_ATTEMPT_LINEAGE",
                "TaskRetry 父任务未进入本次 Harness 迁移，不能建立悬空 Attempt 血缘",
                source_type=operation.source_type,
                source_id=operation.source_id,
                target_type=operation.target_type,
                target_id=operation.target_id,
                details={"parent_attempt_id": parent_id},
            )

    state: dict[str, int] = {}
    path: list[str] = []
    reported_cycles: set[tuple[str, ...]] = set()

    def visit(attempt_id: str) -> None:
        current_state = state.get(attempt_id, 0)
        if current_state == 2:
            return
        if current_state == 1:
            start = path.index(attempt_id)
            cycle = tuple(path[start:] + [attempt_id])
            signature = tuple(sorted(set(cycle)))
            if signature not in reported_cycles:
                reported_cycles.add(signature)
                operation = attempts[attempt_id]
                _issue(
                    issues,
                    "CYCLIC_ATTEMPT_LINEAGE",
                    "TaskRetry 形成循环，无法建立有向 Attempt 血缘",
                    source_type=operation.source_type,
                    source_id=operation.source_id,
                    target_type=operation.target_type,
                    target_id=operation.target_id,
                    details={"attempt_cycle": list(cycle)},
                )
            return

        state[attempt_id] = 1
        path.append(attempt_id)
        parent_id = parent_by_child.get(attempt_id)
        if parent_id in attempts:
            visit(parent_id)
        path.pop()
        state[attempt_id] = 2

    for attempt_id in sorted(attempts):
        visit(attempt_id)


def _ordered_operations_for_apply(
    operations: Sequence[MigrationOperation],
) -> tuple[MigrationOperation, ...]:
    """按 FK 拓扑排序，Attempt 内部再保证父尝试先于子尝试。"""

    attempts = {
        operation.target_id: operation
        for operation in operations
        if operation.target_type == "ExecutionAttempt"
    }
    ordered_attempts: list[MigrationOperation] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(attempt_id: str) -> None:
        if attempt_id in visited:
            return
        if attempt_id in visiting:
            raise MigrationApplyError("ExecutionAttempt 父链存在循环，拒绝应用迁移")
        visiting.add(attempt_id)
        operation = attempts[attempt_id]
        parent_id = _id_text(operation.payload.get("parent_attempt_id"))
        if parent_id is not None:
            if parent_id not in attempts:
                raise MigrationApplyError("ExecutionAttempt 父行不在迁移计划中，拒绝应用迁移")
            visit(parent_id)
        visiting.remove(attempt_id)
        visited.add(attempt_id)
        ordered_attempts.append(operation)

    for attempt_id in sorted(attempts):
        visit(attempt_id)

    non_attempts = sorted(
        (
            operation
            for operation in operations
            if operation.target_type != "ExecutionAttempt"
        ),
        key=lambda operation: (
            _APPLY_TARGET_PRIORITY.get(operation.target_type, 1_000),
            operation.target_type,
            operation.target_id,
        ),
    )
    attempt_priority = _APPLY_TARGET_PRIORITY["ExecutionAttempt"]
    before_attempts = [
        operation
        for operation in non_attempts
        if _APPLY_TARGET_PRIORITY.get(operation.target_type, 1_000) < attempt_priority
    ]
    after_attempts = [
        operation
        for operation in non_attempts
        if _APPLY_TARGET_PRIORITY.get(operation.target_type, 1_000) >= attempt_priority
    ]
    # ScientificCandidate 的父候选也需要先于子候选；虽然 SQLAlchemy 通常能
    # 在一次 flush 中解析自引用 FK，但显式顺序可兼容离线替身和严格数据库。
    candidate_operations = {
        operation.target_id: operation
        for operation in after_attempts
        if operation.target_type == "ScientificCandidate"
    }
    ordered_candidates: list[MigrationOperation] = []
    candidate_visiting: set[str] = set()
    candidate_visited: set[str] = set()

    def visit_candidate(candidate_id: str) -> None:
        if candidate_id in candidate_visited:
            return
        if candidate_id in candidate_visiting:
            raise MigrationApplyError("ScientificCandidate 父链存在循环，拒绝应用迁移")
        operation = candidate_operations[candidate_id]
        candidate_visiting.add(candidate_id)
        parent_id = _id_text(operation.payload.get("parent_candidate_id"))
        if parent_id is not None and parent_id in candidate_operations:
            visit_candidate(parent_id)
        candidate_visiting.remove(candidate_id)
        candidate_visited.add(candidate_id)
        ordered_candidates.append(operation)

    for candidate_id in sorted(candidate_operations):
        visit_candidate(candidate_id)

    before_candidates = [
        operation
        for operation in after_attempts
        if operation.target_type != "ScientificCandidate"
        and _APPLY_TARGET_PRIORITY.get(operation.target_type, 1_000)
        < _APPLY_TARGET_PRIORITY["ScientificCandidate"]
    ]
    after_candidates = [
        operation
        for operation in after_attempts
        if operation.target_type != "ScientificCandidate"
        and _APPLY_TARGET_PRIORITY.get(operation.target_type, 1_000)
        >= _APPLY_TARGET_PRIORITY["ScientificCandidate"]
    ]
    return tuple(before_attempts + ordered_attempts + before_candidates + ordered_candidates + after_candidates)


def _find_existing(session: Any, model: Any, target_id: Any, payload: Mapping[str, Any]) -> Any:
    if hasattr(session, "get"):
        try:
            existing = session.get(model, target_id)
            if existing is not None:
                return existing
        except Exception:
            pass
    # 对没有 id 主键或测试替身，使用显式 legacy_* 稳定键查找。
    for key in ("legacy_agent_session_id", "legacy_agent_message_id", "legacy_research_run_id", "legacy_task_id"):
        value = payload.get(key)
        if value is None:
            continue
        if hasattr(session, "find_one"):
            existing = session.find_one(model, key, value)
            if existing is not None:
                return existing
    return None


def _identity_mismatches(existing: Any, payload: Mapping[str, Any]) -> list[str]:
    """核对重复 apply 命中的既有行是否真是同一稳定对象。"""

    mismatches: list[str] = []
    for key in sorted(_IDEMPOTENCY_IDENTITY_FIELDS & set(payload)):
        try:
            actual = getattr(existing, key)
        except AttributeError:
            continue
        if _json_value(actual) != _json_value(payload[key]):
            mismatches.append(key)
    return mismatches


def apply_legacy_migration(
    session: Any,
    plan: LegacyMigrationPlan,
    *,
    model_registry: Any = None,
) -> MigrationResult:
    """显式向调用方事务加入计划对象；阻断问题时保证零写入。

    该函数不创建 Engine，不读取 settings，也不 commit/rollback。调用方应在
    返回 ``applied=True`` 后自行决定事务提交，异常时自行 rollback。
    """

    if not isinstance(plan, LegacyMigrationPlan):
        raise TypeError("apply_legacy_migration 需要 LegacyMigrationPlan")
    if plan.blocking_issues:
        return MigrationResult(applied=False, issues=plan.issues)
    registry = _registry_from(model_registry or plan.registry)
    if not hasattr(session, "add"):
        raise MigrationConfigurationError("Session 必须提供 add 方法")

    created = 0
    skipped = 0
    operation_results: list[Mapping[str, Any]] = []
    try:
        prepared: list[tuple[MigrationOperation, Any, Any, Any]] = []
        for operation in _ordered_operations_for_apply(plan.operations):
            model = _target_model(registry, operation.target_type)
            target_id = _as_uuid(operation.target_id) or operation.target_id
            existing = _find_existing(session, model, target_id, operation.payload)
            if existing is not None:
                mismatches = _identity_mismatches(existing, operation.payload)
                if mismatches:
                    raise MigrationApplyError(
                        "既有 Harness 对象与稳定迁移身份冲突："
                        f"target_type={operation.target_type}, fields={','.join(mismatches)}"
                    )
            prepared.append((operation, model, target_id, existing))

        # 所有既有对象先完成身份预检，再向 Session 增加任何新对象。
        for operation, model, _target_id, existing in prepared:
            if existing is not None:
                skipped += 1
                operation_results.append(
                    {
                        "target_type": operation.target_type,
                        "target_id": operation.target_id,
                        "action": "skipped_existing",
                    }
                )
                continue
            values = _coerce_model_values(model, operation.payload)
            target = _build_model(model, values)
            session.add(target)
            created += 1
            operation_results.append(
                {
                    "target_type": operation.target_type,
                    "target_id": operation.target_id,
                    "action": "created",
                }
            )
        if hasattr(session, "flush"):
            session.flush()
    except Exception as exc:
        if isinstance(exc, MigrationApplyError):
            raise
        raise MigrationApplyError(
            f"迁移计划 flush 失败；调用方仍需 rollback，原错误：{type(exc).__name__}: {exc}"
        ) from exc
    return MigrationResult(
        applied=True,
        created_count=created,
        skipped_count=skipped,
        issues=plan.issues,
        operation_results=tuple(operation_results),
    )


# 兼容更短的调用名称；它们仍然不会隐式建立数据库连接。
inspect_migration = inspect_legacy
plan_migration = plan_legacy_migration
apply_migration = apply_legacy_migration
inspect_legacy_data = inspect_legacy
build_legacy_migration_plan = plan_legacy_migration
apply_migration_plan = apply_legacy_migration


__all__ = [
    "ATTEMPT_NAMESPACE",
    "CANDIDATE_SET_NAMESPACE",
    "CHECKPOINT_NAMESPACE",
    "EVIDENCE_NAMESPACE",
    "LegacyMigrationInspection",
    "LegacyMigrationIssue",
    "LegacyMigrationPlan",
    "LegacyMigrationResult",
    "MIGRATION_NAMESPACE",
    "SCORE_RUN_NAMESPACE",
    "STRUCTURE_PREDICTION_NAMESPACE",
    "TARGET_NAMESPACE",
    "MigrationApplyError",
    "MigrationConfigurationError",
    "MigrationIssue",
    "MigrationOperation",
    "MigrationResult",
    "MigrationVerification",
    "ModelRegistry",
    "apply_legacy_migration",
    "apply_migration",
    "apply_migration_plan",
    "build_legacy_migration_plan",
    "candidate_set_id",
    "execution_attempt_id",
    "inspect_legacy",
    "inspect_legacy_data",
    "inspect_migration",
    "plan_legacy_migration",
    "plan_migration",
    "provenance_edge_id",
    "score_result_id",
    "score_run_id",
    "scientific_target_id",
    "scientific_evidence_id",
    "skill_execution_id",
    "structure_prediction_run_id",
    "workflow_checkpoint_id",
    "verify_legacy_migration",
    "verify_migration_execution",
]
