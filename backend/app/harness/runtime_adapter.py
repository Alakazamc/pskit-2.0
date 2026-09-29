"""aptamer_closed_loop 计划到既有 Harness 持久化边界的纯适配器。

wzf：既有 SQLAlchemyInvocationRepository.persist_atomically 只接受执行层
Invocation/TaskGraph/ScientificTask/Attempt/Evidence/Provenance/Outbox intent，
不能直接接收 ResearchSession/ResearchGoal/SkillExecution 的 SessionNativeIntent。
本模块因此只定义显式的 SessionNativePersistencePort 交接协议，并把相同事务所需
的 OutboxEvent 一并交给该端口；不创建 DB Session、不调用现有 Repository 的
错误入口、不启动 Worker/MCP，也不把“交给适配器”声称为已持久化或已执行。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
from typing import Any, Protocol

from app.harness.executor import (
    AptamerRuntimePlan,
    AptamerSkillRuntime,
    SessionNativeIntent,
    SkillRuntimeActivation,
)
from app.harness.outbox import OutboxEvent
from app.harness.runtime_wiring import LEGACY_ROUTE, NATIVE_ROUTE


class RuntimeAdapterError(RuntimeError):
    """计划不能安全交给持久化适配端口时抛出的异常。"""


class SessionNativePersistencePort(Protocol):
    """由 UoW/Repository/Outbox 组合层实现的单事务交接端口。

    实现必须在一个外层事务中 ensure/reuse 全部 SessionNativeIntent，并把
    outbox_event 与事实一起落盘。方法返回值只作为适配器交接回执，不能被解释为
    科学工具结果或数据库提交证明。
    """

    def persist_session_native_atomically(
        self,
        intents: tuple[SessionNativeIntent, ...],
        outbox_event: OutboxEvent,
    ) -> Any:
        """接收一批 session-native 意图和同批 Outbox 事件。"""


@dataclass(frozen=True, slots=True)
class RuntimeAdapterDecision:
    """适配器的可审计判定；persisted 永远需要端口外部证明。"""

    status: str
    route: str
    reason: str
    plan: AptamerRuntimePlan | None = None
    intents: tuple[SessionNativeIntent, ...] = ()
    outbox_event: OutboxEvent | None = None
    adapter_result: Any = None
    persisted: bool = False

    def __post_init__(self) -> None:
        if self.route not in {LEGACY_ROUTE, NATIVE_ROUTE}:
            raise ValueError("route must be legacy_bridge or session_native")
        if self.status not in {
            "unavailable",
            "prepared",
            "adapter_unavailable",
            "handoff_failed",
            "submitted_to_adapter",
        }:
            raise ValueError("unknown adapter status")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason must be non-empty")
        if any(not isinstance(item, SessionNativeIntent) for item in self.intents):
            raise TypeError("intents must contain SessionNativeIntent")
        if self.persisted is not False:
            object.__setattr__(self, "persisted", False)

    @property
    def accepted(self) -> bool:
        """仅表示端口方法已被调用/接受，不代表数据库或科学执行成功。"""

        return self.status == "submitted_to_adapter"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status,
            "route": self.route,
            "reason": self.reason,
            "persisted": False,
            "intent_count": len(self.intents),
            "outbox_dedupe_key": self.outbox_event.dedupe_key
            if self.outbox_event is not None
            else None,
        }
        if self.plan is not None:
            payload["plan"] = {
                "skill_id": self.plan.skill_id,
                "session_id": self.plan.session_id,
                "user_id": self.plan.user_id,
                "skill_execution_id": self.plan.skill_execution_id,
                "task_graph_id": self.plan.task_graph_id,
                "request_hash": self.plan.request_hash,
            }
        return payload


class AptamerRuntimeAdapter:
    """在激活证据完整时准备或交接 AptamerRuntimePlan。

    prepare 是纯适配步骤；handoff 只调用调用方注入的协议对象，因而可以在
    SQLite/内存契约测试中验证而不触碰生产资源。
    """

    def __init__(
        self,
        runtime: AptamerSkillRuntime | None = None,
        activation: SkillRuntimeActivation | None = None,
        *,
        scope_evidence: Mapping[str, Any] | None = None,
    ) -> None:
        if runtime is None:
            runtime = AptamerSkillRuntime(activation=activation)
        if not isinstance(runtime, AptamerSkillRuntime):
            raise TypeError("runtime must be an AptamerSkillRuntime")
        if activation is not None and activation != runtime.activation:
            raise ValueError("runtime and activation evidence disagree")
        self.runtime = runtime
        self.activation = runtime.activation
        self.scope_evidence = (
            dict(scope_evidence) if isinstance(scope_evidence, Mapping) else None
        )

    def _scope_ok(self, plan: AptamerRuntimePlan) -> bool:
        if self.activation.scope_valid is not True:
            return False
        if self.scope_evidence is None:
            return True
        return (
            self.scope_evidence.get("in_scope") is True
            and str(self.scope_evidence.get("user_id") or "") == plan.user_id
            and str(self.scope_evidence.get("session_id") or "") == plan.session_id
        )

    @staticmethod
    def _outbox_event(plan: AptamerRuntimePlan, intent_count: int) -> OutboxEvent:
        event_id = hashlib.sha256(
            f"aptamer-plan:{plan.request_hash}:{plan.skill_execution_id}".encode("utf-8")
        ).hexdigest()
        now = datetime.now(timezone.utc)
        return OutboxEvent(
            id=f"aptamer-plan-{event_id}",
            dedupe_key=f"aptamer-plan:{plan.idempotency_key}",
            event_type="AptamerSkillPlanPrepared",
            aggregate_type="skill_execution",
            aggregate_id=plan.skill_execution_id,
            payload={
                "route": NATIVE_ROUTE,
                "skill_id": plan.skill_id,
                "skill_version": plan.skill_version,
                "session_id": plan.session_id,
                "user_id": plan.user_id,
                "skill_execution_id": plan.skill_execution_id,
                "task_graph_id": plan.task_graph_id,
                "request_hash": plan.request_hash,
                "intent_count": intent_count,
                "persisted": False,
            },
            pending_at=now,
            available_at=now,
        )

    def prepare(
        self,
        plan: AptamerRuntimePlan,
        *,
        capability_inputs: Mapping[str, Any] | None = None,
    ) -> RuntimeAdapterDecision:
        """校验激活证据并生成可交给端口的意图/Outbox 批次。"""

        if not isinstance(plan, AptamerRuntimePlan):
            return RuntimeAdapterDecision(
                "unavailable", LEGACY_ROUTE, "invalid_plan",
            )
        if self.activation.feature_enabled is not True:
            return RuntimeAdapterDecision(
                "unavailable", LEGACY_ROUTE, "feature_flag_disabled", plan=plan
            )
        if self.activation.route != NATIVE_ROUTE:
            return RuntimeAdapterDecision(
                "unavailable", LEGACY_ROUTE, "legacy_bridge_default", plan=plan
            )
        if self.activation.wiring_proven is not True:
            return RuntimeAdapterDecision(
                "unavailable", LEGACY_ROUTE, "runtime_wiring_unproven", plan=plan
            )
        if not self._scope_ok(plan):
            return RuntimeAdapterDecision(
                "unavailable", LEGACY_ROUTE, "user_session_scope_unproven", plan=plan
            )
        if plan.route != NATIVE_ROUTE or not plan.native:
            return RuntimeAdapterDecision(
                "unavailable", LEGACY_ROUTE, "plan_is_not_session_native", plan=plan
            )
        if plan.status in {"unavailable", "idempotency_conflict"}:
            return RuntimeAdapterDecision(
                "unavailable", LEGACY_ROUTE, "plan_status_denied", plan=plan
            )
        intents = self.runtime.build_intents(plan, capability_inputs=capability_inputs)
        if not intents:
            return RuntimeAdapterDecision(
                "unavailable", LEGACY_ROUTE, "no_persistable_intents", plan=plan
            )
        event = self._outbox_event(plan, len(intents))
        return RuntimeAdapterDecision(
            "prepared",
            NATIVE_ROUTE,
            "session_native_intents_prepared",
            plan=plan,
            intents=intents,
            outbox_event=event,
        )

    def handoff(
        self,
        plan: AptamerRuntimePlan,
        port: SessionNativePersistencePort | Any,
        *,
        capability_inputs: Mapping[str, Any] | None = None,
    ) -> RuntimeAdapterDecision:
        """把批次交给显式端口；端口缺失/异常均 fail-closed。

        返回 submitted_to_adapter 只表示协议方法返回，persisted 仍为 False；
        端口如需提交证明应由上层另行记录并验证。
        """

        prepared = self.prepare(plan, capability_inputs=capability_inputs)
        if prepared.status != "prepared":
            return prepared
        method = getattr(port, "persist_session_native_atomically", None)
        if not callable(method):
            return replace(
                prepared,
                status="adapter_unavailable",
                reason="session_native_persistence_port_required",
            )
        try:
            result = method(prepared.intents, prepared.outbox_event)
        except Exception as exc:
            return replace(
                prepared,
                status="handoff_failed",
                reason=f"persistence_port_error:{type(exc).__name__}",
            )
        return replace(
            prepared,
            status="submitted_to_adapter",
            reason="submitted_without_commit_proof",
            adapter_result=result,
            persisted=False,
        )


def adapt_plan(
    plan: AptamerRuntimePlan,
    *,
    runtime: AptamerSkillRuntime | None = None,
    activation: SkillRuntimeActivation | None = None,
    scope_evidence: Mapping[str, Any] | None = None,
    capability_inputs: Mapping[str, Any] | None = None,
) -> RuntimeAdapterDecision:
    """便捷纯入口；等价于 AptamerRuntimeAdapter(...).prepare(plan)。"""

    adapter = AptamerRuntimeAdapter(
        runtime=runtime,
        activation=activation,
        scope_evidence=scope_evidence,
    )
    return adapter.prepare(plan, capability_inputs=capability_inputs)


__all__ = [
    "AptamerRuntimeAdapter",
    "RuntimeAdapterDecision",
    "RuntimeAdapterError",
    "SessionNativePersistencePort",
    "adapt_plan",
]
