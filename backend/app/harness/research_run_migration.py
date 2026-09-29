"""ResearchRun 到新 Harness 的显式迁移服务。

wzf：本模块只封装已有 ``app.harness.migration`` 的计划、应用和核对边界，
不删除旧 ResearchRun，不建立 Engine/默认 Session，也不隐式提交事务。调用方必须注入 Session、ModelRegistry 和（需要事务时）
commit/rollback port；因此纯 SQLite/内存替身可以完整测试，生产删除入口默认
永久关闭。此服务是单个 ResearchRun 的安全边界：注入 Session 必须只暴露该
run 的来源行；若计划含其他 run 的直接来源或 legacy 引用，服务会阻断而不会
静默丢弃关联对象。全量迁移应继续显式调用既有 migration 入口。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Protocol
from uuid import UUID

from app.harness.migration import (
    LegacyMigrationPlan,
    MigrationApplyError,
    MigrationConfigurationError,
    MigrationIssue,
    MigrationResult,
    MigrationVerification,
    ModelRegistry,
    apply_legacy_migration,
    plan_legacy_migration,
    verify_legacy_migration,
)


class ResearchRunMigrationError(RuntimeError):
    """ResearchRun 来源或迁移范围不能安全确认时抛出的异常。"""


class LegacyDeletionDisabled(ResearchRunMigrationError):
    """旧 ResearchRun 删除默认关闭；本服务不提供隐式删除。"""


class MigrationTransactionPort(Protocol):
    """由调用方注入的事务边界；服务不创建数据库连接。"""

    def begin(self) -> Any:
        """开始一个由调用方控制的事务。"""

    def commit(self) -> Any:
        """提交已经核验的迁移。"""

    def rollback(self) -> Any:
        """回滚未提交或核验失败的迁移。"""


def _text_id(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


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


def _rows_for(session: Any, model: Any) -> list[Any]:
    """只使用显式读取接口；不在服务内猜测数据库连接。"""

    if model is None:
        return []
    if hasattr(session, "rows_for"):
        return list(session.rows_for(model))
    if hasattr(session, "all_for"):
        return list(session.all_for(model))
    raise MigrationConfigurationError(
        "ResearchRunMigrationService 的 Session 必须提供 rows_for 或 all_for"
    )


def _coerce_registry(value: Any) -> ModelRegistry:
    if isinstance(value, ModelRegistry):
        return value
    if isinstance(value, Mapping):
        legacy = value.get("legacy", value.get("old", {}))
        target = value.get("target", value.get("new", {}))
        if not isinstance(legacy, Mapping) or not isinstance(target, Mapping):
            raise MigrationConfigurationError("model_registry 的 legacy/target 必须是映射")
        return ModelRegistry(dict(legacy), dict(target))
    # wzf：migration 模块可能被热重载，不能用严格类身份拒绝同契约的新 ModelRegistry。
    missing = object()
    legacy = getattr(value, "legacy", missing)
    target = getattr(value, "target", missing)
    if legacy is not missing or target is not missing:
        if legacy is missing or target is missing:
            raise MigrationConfigurationError(
                "model_registry 必须同时提供 legacy 和 target 映射"
            )
        if not isinstance(legacy, Mapping) or not isinstance(target, Mapping):
            raise MigrationConfigurationError("model_registry 的 legacy/target 必须是映射")
        # 保留调用方 registry 实例，避免 plan_builder 的注入身份契约被破坏。
        return value
    raise MigrationConfigurationError(
        "必须显式注入 ModelRegistry；服务不会导入默认数据库模型"
    )


def _coerce_plan(value: Any, registry: Any) -> LegacyMigrationPlan:
    """校验并归一化跨模块重载产生的 LegacyMigrationPlan。"""

    if isinstance(value, LegacyMigrationPlan):
        return value
    try:
        operations = tuple(value.operations)
        issues = tuple(value.issues)
        source_counts = value.source_counts
        target_counts = value.target_counts
    except (AttributeError, TypeError) as exc:
        raise MigrationConfigurationError(
            "plan_builder 必须返回 LegacyMigrationPlan 或兼容结构"
        ) from exc
    if not isinstance(source_counts, Mapping) or not isinstance(target_counts, Mapping):
        raise MigrationConfigurationError(
            "LegacyMigrationPlan 的 source_counts/target_counts 必须是映射"
        )
    for issue in issues:
        if any(not hasattr(issue, name) for name in ("code", "message", "blocking")):
            raise MigrationConfigurationError("LegacyMigrationPlan 的 issues 结构无效")
    for operation in operations:
        required = ("operation", "source_type", "source_id", "target_type", "target_id", "payload")
        if any(not hasattr(operation, name) for name in required):
            raise MigrationConfigurationError("LegacyMigrationPlan 的 operations 结构无效")
        if operation.operation not in {"create", "ensure"}:
            raise MigrationConfigurationError("LegacyMigrationPlan 的 operation 类型无效")
        if not isinstance(operation.payload, Mapping):
            raise MigrationConfigurationError("LegacyMigrationPlan 的 operation payload 必须是映射")
    return LegacyMigrationPlan(
        operations=operations,
        issues=issues,
        source_counts=source_counts,
        target_counts=target_counts,
        registry=getattr(value, "registry", registry),
    )


@dataclass(frozen=True, slots=True)
class ResearchRunMigrationOutcome:
    """一次服务调用的可审计结果；不把 applied 等同于已提交。"""

    status: str
    research_run_id: str
    plan: LegacyMigrationPlan | None = None
    apply_result: MigrationResult | None = None
    verification: MigrationVerification | None = None
    committed: bool = False
    rolled_back: bool = False
    legacy_deleted: bool = False
    issues: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        valid = {"blocked", "verified_uncommitted", "committed", "rolled_back", "failed"}
        if self.status not in valid:
            raise ValueError(f"unknown migration outcome status: {self.status}")
        if self.committed and self.rolled_back:
            raise ValueError("committed and rolled_back cannot both be true")
        if self.legacy_deleted:
            raise ValueError("ResearchRunMigrationService never deletes legacy rows")
        object.__setattr__(self, "issues", tuple(str(item) for item in self.issues))

    @property
    def applied(self) -> bool:
        return bool(self.apply_result and self.apply_result.applied)

    @property
    def verified(self) -> bool:
        return bool(self.verification and self.verification.passed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "research_run_id": self.research_run_id,
            "applied": self.applied,
            "verified": self.verified,
            "committed": self.committed,
            "rolled_back": self.rolled_back,
            "legacy_deleted": False,
            "issues": list(self.issues),
            "operation_count": self.plan.operation_count if self.plan else 0,
            "created_count": self.apply_result.created_count if self.apply_result else 0,
            "skipped_count": self.apply_result.skipped_count if self.apply_result else 0,
            "verification": self.verification.as_dict() if self.verification else None,
        }


@dataclass(slots=True)
class _TransactionState:
    active: bool = False
    committed: bool = False
    rolled_back: bool = False


class ResearchRunMigrationService:
    """把一个已确认的 ResearchRun 显式迁移到新 Harness。

    ``session`` 可以是 SQLAlchemy Session，也可以是只实现 ``rows_for``、
    ``add``、``get``、``flush`` 的 SQLite/内存替身。默认计划器是真实
    ``plan_legacy_migration``；测试可通过 ``plan_builder`` 注入受控计划器。
    """

    def __init__(
        self,
        *,
        session: Any,
        model_registry: ModelRegistry | Mapping[str, Any],
        transaction_port: MigrationTransactionPort | Any | None = None,
        plan_builder: Callable[..., LegacyMigrationPlan] | None = None,
    ) -> None:
        if session is None:
            raise MigrationConfigurationError("session is required")
        self.session = session
        self.model_registry = _coerce_registry(model_registry)
        self.transaction_port = transaction_port
        self.plan_builder = plan_builder or plan_legacy_migration
        self._state = _TransactionState()
        self._active_run_id: str | None = None

    def _run_row(self, research_run_id: str) -> Any:
        model = self.model_registry.legacy.get("ResearchRun")
        rows = _rows_for(self.session, model)
        matches = [
            row
            for row in rows
            if _text_id(_row_value(row, "id")) == research_run_id
        ]
        if not matches:
            raise ResearchRunMigrationError(
                f"ResearchRun 不存在或未在注入 Session 范围内：{research_run_id}"
            )
        if len(matches) != 1:
            raise ResearchRunMigrationError(
                f"ResearchRun 稳定身份不唯一：{research_run_id}"
            )
        return matches[0]

    def _scope_issues(self, plan: LegacyMigrationPlan, research_run_id: str) -> tuple[MigrationIssue, ...]:
        issues: list[MigrationIssue] = []
        for operation in plan.operations:
            if operation.source_type == "ResearchRun" and operation.source_id != research_run_id:
                issues.append(
                    MigrationIssue(
                        "OUT_OF_SCOPE_RESEARCH_RUN",
                        "迁移计划包含请求范围之外的 ResearchRun",
                        source_type=operation.source_type,
                        source_id=operation.source_id,
                        target_type=operation.target_type,
                        target_id=operation.target_id,
                    )
                )
            legacy_id = _text_id(operation.payload.get("legacy_research_run_id"))
            if legacy_id is not None and legacy_id != research_run_id:
                issues.append(
                    MigrationIssue(
                        "OUT_OF_SCOPE_LEGACY_REFERENCE",
                        "目标对象引用了请求范围之外的 legacy ResearchRun",
                        source_type=operation.source_type,
                        source_id=operation.source_id,
                        target_type=operation.target_type,
                        target_id=operation.target_id,
                        details={"legacy_research_run_id": legacy_id},
                    )
                )
        return tuple(issues)

    def plan(
        self,
        research_run_id: UUID | str,
        *,
        expected_user_id: UUID | str | None = None,
    ) -> LegacyMigrationPlan:
        run_id = _text_id(research_run_id)
        if run_id is None:
            raise ResearchRunMigrationError("research_run_id must be non-empty")
        row = self._run_row(run_id)
        expected_owner = _text_id(expected_user_id)
        actual_owner = _text_id(_row_value(row, "user_id"))
        if expected_owner is not None and actual_owner != expected_owner:
            raise ResearchRunMigrationError(
                f"ResearchRun 所有权不匹配：expected={expected_owner}, actual={actual_owner}"
            )
        plan = _coerce_plan(
            self.plan_builder(self.session, model_registry=self.model_registry),
            self.model_registry,
        )
        scope_issues = self._scope_issues(plan, run_id)
        if scope_issues:
            plan = replace(plan, issues=tuple(plan.issues) + scope_issues)
        self._active_run_id = run_id
        return plan

    def _target_counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for name, model in self.model_registry.target.items():
            try:
                result[name] = len(_rows_for(self.session, model))
            except MigrationConfigurationError:
                result[name] = 0
        return result

    def _tx_object(self) -> Any:
        return self.transaction_port or self.session

    def _begin(self) -> None:
        method = getattr(self._tx_object(), "begin", None)
        if callable(method):
            method()
        self._state = _TransactionState(active=True)

    def _commit(self) -> None:
        method = getattr(self._tx_object(), "commit", None)
        if not callable(method):
            raise MigrationConfigurationError("迁移 commit 需要显式 transaction_port.commit")
        method()
        self._state = _TransactionState(committed=True)

    def rollback(self) -> bool:
        """回滚当前未提交迁移；没有活动事务时返回 False。"""

        if not self._state.active:
            return False
        method = getattr(self._tx_object(), "rollback", None)
        if not callable(method):
            raise MigrationConfigurationError(
                "迁移 rollback 需要显式 transaction_port.rollback"
            )
        method()
        self._state = _TransactionState(rolled_back=True)
        return True

    def _rollback_safely(self) -> bool:
        try:
            return self.rollback()
        except Exception:
            return False

    def migrate(
        self,
        research_run_id: UUID | str,
        *,
        expected_user_id: UUID | str | None = None,
        expected_artifact_hashes: Mapping[str, str] | None = None,
        commit: bool = False,
    ) -> ResearchRunMigrationOutcome:
        """计划、应用、重复应用核对并按需提交；默认不 commit、不删除旧行。"""

        run_id = _text_id(research_run_id)
        if run_id is None:
            raise ResearchRunMigrationError("research_run_id must be non-empty")
        plan = self.plan(run_id, expected_user_id=expected_user_id)
        if plan.blocking_issues:
            return ResearchRunMigrationOutcome(
                "blocked",
                run_id,
                plan=plan,
                issues=tuple(issue.code for issue in plan.blocking_issues),
            )

        before_counts = self._target_counts()
        try:
            self._begin()
            first = apply_legacy_migration(
                self.session,
                plan,
                model_registry=self.model_registry,
            )
            if not first.applied:
                self._rollback_safely()
                return ResearchRunMigrationOutcome(
                    "blocked",
                    run_id,
                    plan=plan,
                    apply_result=first,
                    rolled_back=self._state.rolled_back,
                    issues=tuple(issue.code for issue in first.blocking_issues),
                )
            repeated = apply_legacy_migration(
                self.session,
                plan,
                model_registry=self.model_registry,
            )
            verification = verify_legacy_migration(
                self.session,
                plan,
                first,
                model_registry=self.model_registry,
                expected_artifact_hashes=expected_artifact_hashes,
                before_target_counts=before_counts,
                repeated_result=repeated,
            )
            if not verification.passed:
                rolled_back = self._rollback_safely()
                return ResearchRunMigrationOutcome(
                    "rolled_back" if rolled_back else "failed",
                    run_id,
                    plan=plan,
                    apply_result=first,
                    verification=verification,
                    rolled_back=rolled_back,
                    issues=verification.issues,
                )
            if commit:
                self._commit()
                return ResearchRunMigrationOutcome(
                    "committed",
                    run_id,
                    plan=plan,
                    apply_result=first,
                    verification=verification,
                    committed=True,
                )
            return ResearchRunMigrationOutcome(
                "verified_uncommitted",
                run_id,
                plan=plan,
                apply_result=first,
                verification=verification,
            )
        except MigrationApplyError as exc:
            rolled_back = self._rollback_safely()
            return ResearchRunMigrationOutcome(
                "rolled_back" if rolled_back else "failed",
                run_id,
                plan=plan,
                rolled_back=rolled_back,
                issues=(str(exc),),
            )

    run = migrate

    def delete_legacy(self, research_run_id: UUID | str) -> None:
        """明确拒绝删除旧入口；清理由迁移后独立审批的工具负责。"""

        raise LegacyDeletionDisabled(
            f"旧 ResearchRun 保留，禁止删除：{_text_id(research_run_id) or '<empty>'}"
        )


__all__ = [
    "LegacyDeletionDisabled",
    "MigrationTransactionPort",
    "ResearchRunMigrationError",
    "ResearchRunMigrationOutcome",
    "ResearchRunMigrationService",
]