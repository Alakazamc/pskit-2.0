"""aptamer_closed_loop Skill 的受控计划与事务交接服务。

wzf：服务只负责把显式激活证据、资源证据和已进入 HarnessUnitOfWork 的
SQLAlchemy Session 交给纯 AptamerSkillRuntime/AptamerRuntimeAdapter。它不创建
Session、不提交事务、不启动 Worker/MCP，也不把 handoff 结果伪装成 persisted
或科学结果；缺少 UoW、作用域、端口或激活证据时固定回 legacy/unavailable。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.harness.executor import (
    AptamerRuntimePlan,
    AptamerSkillRuntime,
    SkillRuntimeActivation,
)
from app.harness.outbox import OutboxEvent
from app.harness.runtime_adapter import (
    AptamerRuntimeAdapter,
    RuntimeAdapterDecision,
    SessionNativePersistencePort,
)
from app.harness.runtime_wiring import LEGACY_ROUTE, NATIVE_ROUTE


_MISSING = object()
_WAITING_STATUSES = frozenset({"waiting_for_resource", "waiting_for_dependency"})


@dataclass(frozen=True, slots=True)
class AptamerServiceDecision:
    """服务层安全摘要；persisted 永远为 False。"""

    status: str
    route: str
    reason: str
    plan: AptamerRuntimePlan | None = None
    adapter_decision: RuntimeAdapterDecision | None = None
    persisted: bool = False

    def __post_init__(self) -> None:
        if self.route not in {LEGACY_ROUTE, NATIVE_ROUTE}:
            raise ValueError("route must be legacy_bridge or session_native")
        if not isinstance(self.status, str) or not self.status.strip():
            raise ValueError("status must be non-empty")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason must be non-empty")
        if self.persisted is not False:
            object.__setattr__(self, "persisted", False)

    @property
    def intent_count(self) -> int:
        decision = self.adapter_decision
        return len(decision.intents) if decision is not None else 0

    @property
    def outbox_event(self) -> OutboxEvent | None:
        decision = self.adapter_decision
        return decision.outbox_event if decision is not None else None

    @property
    def handoff_status(self) -> str | None:
        decision = self.adapter_decision
        return decision.status if decision is not None else None

    @staticmethod
    def _plan_summary(plan: AptamerRuntimePlan | None) -> dict[str, Any] | None:
        if plan is None:
            return None
        return {
            "skill_id": plan.skill_id,
            "skill_version": plan.skill_version,
            "session_id": plan.session_id,
            "user_id": plan.user_id,
            "idempotency_key": plan.idempotency_key,
            "request_hash": plan.request_hash,
            "route": plan.route,
            "status": plan.status,
            "reason": plan.reason,
            "goal_id": plan.goal_id,
            "skill_execution_id": plan.skill_execution_id,
            "task_graph_id": plan.task_graph_id,
            "graph_revision": plan.graph_revision,
            "reused": plan.reused,
            "ready_stage_ids": list(plan.ready_stage_ids),
            "stages": [
                {
                    "stage_id": stage.stage_id,
                    "status": stage.status,
                    "attempt": stage.attempt,
                    "reason": stage.reason,
                    "capabilities": list(stage.capabilities),
                    "depends_on": list(stage.depends_on),
                    "requires_gpu": stage.requires_gpu,
                    "task_id": stage.task_id,
                    "attempt_id": stage.attempt_id,
                }
                for stage in plan.stages
            ],
        }

    def to_public_dict(self) -> dict[str, Any]:
        """只返回计划/意图/Outbox 摘要，不展开输入、密钥、路径或科学产物。"""

        event = self.outbox_event
        entities: list[str] = []
        if self.adapter_decision is not None:
            entities = sorted({item.entity for item in self.adapter_decision.intents})
        return {
            "status": self.status,
            "route": self.route,
            "reason": self.reason,
            "handoff_status": self.handoff_status,
            "persisted": False,
            "plan": self._plan_summary(self.plan),
            "intent": {
                "count": self.intent_count,
                "entities": entities,
            },
            "outbox": (
                {
                    "status": event.status,
                    "event_type": event.event_type,
                    "aggregate_type": event.aggregate_type,
                    "aggregate_id": event.aggregate_id,
                    "dedupe_key": event.dedupe_key,
                    "intent_count": len(self.adapter_decision.intents)
                    if self.adapter_decision is not None
                    else 0,
                    "persisted": False,
                }
                if event is not None
                else None
            ),
        }


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _same_id(left: object, right: object) -> bool:
    left_text = _text(left)
    right_text = _text(right)
    return left_text is not None and right_text is not None and left_text == right_text


def _unavailable(reason: str, *, plan: AptamerRuntimePlan | None = None) -> AptamerServiceDecision:
    return AptamerServiceDecision(
        status="unavailable",
        route=LEGACY_ROUTE,
        reason=reason,
        plan=plan,
    )


def _entered_uow_session(uow: object) -> object | None:
    """只接受已进入的 UoW；不接受普通 Depends(get_db) Session 冒充。"""

    if uow is None or getattr(uow, "_state", None) != "entered":
        return None
    try:
        session = getattr(uow, "session")
    except Exception:
        return None
    return session


class AptamerSkillService:
    """把一次 Aptamer 计划限制在调用方已开启的 Harness UoW 内。"""

    def __init__(
        self,
        *,
        uow: object,
        persistence_port: SessionNativePersistencePort | Any,
        activation: SkillRuntimeActivation | None = None,
        resources: Mapping[str, Any] | None = None,
        runtime: AptamerSkillRuntime | None = None,
    ) -> None:
        self.uow = uow
        self.persistence_port = persistence_port
        self.activation = activation
        self.resources = dict(resources) if isinstance(resources, Mapping) else resources
        self.runtime = runtime

    def _validate_scope(self, session: object, user_id: object, session_id: object) -> str | None:
        uow_session = _entered_uow_session(self.uow)
        if uow_session is None:
            return "harness_uow_not_entered"
        if uow_session is not session:
            return "uow_session_mismatch"
        uow_user = getattr(self.uow, "user_id", _MISSING)
        uow_scope = getattr(self.uow, "session_id", _MISSING)
        if uow_user is _MISSING or uow_scope is _MISSING:
            return "uow_scope_evidence_required"
        if not _same_id(uow_user, user_id) or not _same_id(uow_scope, session_id):
            return "uow_scope_mismatch"
        return None

    def _validate_port(self, session: object, user_id: object, session_id: object) -> str | None:
        method = getattr(self.persistence_port, "persist_session_native_atomically", None)
        if not callable(method):
            return "session_native_persistence_port_required"
        port_session = getattr(self.persistence_port, "session", _MISSING)
        if port_session is _MISSING:
            return "persistence_port_session_required"
        if port_session is not session:
            return "persistence_port_session_mismatch"
        for name, expected in (("user_id", user_id), ("session_id", session_id)):
            actual = getattr(self.persistence_port, name, _MISSING)
            if actual is _MISSING or not _same_id(actual, expected):
                return f"persistence_port_{name}_scope_mismatch"
        return None

    @staticmethod
    def _scope_payload(user_id: object, session_id: object) -> dict[str, object]:
        return {
            "in_scope": True,
            "user_id": str(user_id),
            "session_id": str(session_id),
        }

    def plan_and_handoff(
        self,
        *,
        session: object,
        user_id: UUID | str,
        session_id: UUID | str,
        target: Mapping[str, Any],
        resources: Mapping[str, Any] | None = None,
        activation: SkillRuntimeActivation | None = None,
        idempotency_key: object = None,
        capability_inputs: Mapping[str, Any] | None = None,
        legacy_research_run_id: object = None,
        existing_plan: AptamerRuntimePlan | None = None,
    ) -> AptamerServiceDecision:
        """构建计划并把同一批意图/Outbox 交给显式 Session-native 端口。"""

        scope_error = self._validate_scope(session, user_id, session_id)
        if scope_error is not None:
            return _unavailable(scope_error)
        port_error = self._validate_port(session, user_id, session_id)
        if port_error is not None:
            return _unavailable(port_error)

        effective_activation = activation if activation is not None else self.activation
        if not isinstance(effective_activation, SkillRuntimeActivation):
            return _unavailable("activation_required")
        if not effective_activation.native:
            return _unavailable(effective_activation.reason)

        effective_resources = resources if resources is not None else self.resources
        if effective_resources is None:
            return _unavailable("resource_evidence_required")
        if not isinstance(effective_resources, Mapping):
            return _unavailable("resource_evidence_invalid")

        user_text = _text(user_id)
        session_text = _text(session_id)
        if user_text is None or session_text is None:
            return _unavailable("scope_identity_required")

        runtime = self.runtime
        if runtime is None:
            try:
                runtime = AptamerSkillRuntime(activation=effective_activation)
            except Exception:
                return _unavailable("runtime_unavailable")
        elif runtime.activation != effective_activation:
            return _unavailable("runtime_activation_mismatch")

        if existing_plan is not None and (
            not _same_id(existing_plan.user_id, user_text)
            or not _same_id(existing_plan.session_id, session_text)
        ):
            return _unavailable("existing_plan_scope_mismatch")

        try:
            plan = runtime.build_plan(
                session_text,
                user_text,
                target,
                idempotency_key=idempotency_key,
                resources=effective_resources,
                capability_inputs=capability_inputs,
                legacy_research_run_id=legacy_research_run_id,
                existing_plan=existing_plan,
            )
        except Exception as exc:
            return _unavailable(f"plan_rejected:{type(exc).__name__}")

        if not plan.native:
            return AptamerServiceDecision(
                status=plan.status,
                route=LEGACY_ROUTE,
                reason=plan.reason,
                plan=plan,
            )

        try:
            adapter = AptamerRuntimeAdapter(
                runtime=runtime,
                activation=effective_activation,
                scope_evidence=self._scope_payload(user_text, session_text),
            )
            handoff = adapter.handoff(
                plan,
                self.persistence_port,
                capability_inputs=capability_inputs,
            )
        except Exception as exc:
            return AptamerServiceDecision(
                status="handoff_failed",
                route=NATIVE_ROUTE,
                reason=f"handoff_error:{type(exc).__name__}",
                plan=plan,
            )

        if handoff.status in {"handoff_failed", "adapter_unavailable"}:
            return AptamerServiceDecision(
                status=handoff.status,
                route=NATIVE_ROUTE,
                reason=handoff.reason,
                plan=plan,
                adapter_decision=handoff,
            )
        if plan.status in _WAITING_STATUSES:
            return AptamerServiceDecision(
                status=plan.status,
                route=NATIVE_ROUTE,
                reason=plan.reason,
                plan=plan,
                adapter_decision=handoff,
            )
        return AptamerServiceDecision(
            status=handoff.status,
            route=NATIVE_ROUTE,
            reason=handoff.reason,
            plan=plan,
            adapter_decision=handoff,
        )


AptamerRuntimeService = AptamerSkillService
AptamerPlanDecision = AptamerServiceDecision


__all__ = [
    "AptamerPlanDecision",
    "AptamerRuntimeService",
    "AptamerServiceDecision",
    "AptamerSkillService",
]