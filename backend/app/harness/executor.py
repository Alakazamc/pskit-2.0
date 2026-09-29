"""aptamer_closed_loop 的 Session-native 计划装配器。

wzf：本模块只生成可审计的计划和持久化意图；它不连接数据库、Outbox、
Worker、MCP、LLM，也不执行真实科学工具。实际提交应由调用方把返回的
SessionNativeIntent 批量交给已经验证的 UoW/Repository/适配器，并在同一事务
中按 natural key 做 ensure/reuse。

feature flag、runtime wiring 和 user/session scope 三类证据默认关闭。证据不足
时路线固定为 legacy_bridge、状态为 unavailable，绝不把计划当作科学结果。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
import hashlib
import json
from types import MappingProxyType
from typing import Any
from uuid import UUID, uuid5

from app.harness.aptamer_skill import (
    AptamerExecutionPlan,
    StageState,
    StageStatus,
    apply_resource_gate,
    build_aptamer_closed_loop_plan,
    evaluate_evidence_gate,
    recover_from_stage,
)
from app.harness.execution import ExecuteCapabilityCommand
from app.harness.runtime_wiring import LEGACY_ROUTE, NATIVE_ROUTE
from app.harness.skill_registry import (
    AptamerSkillRegistry,
    RegisteredSkill,
    get_aptamer_skill_registry,
)

SKILL_ID = "aptamer_closed_loop"
_NAMESPACE = UUID("f0c7e9a5-7b30-5d5e-9b0e-5a1bb5c9a4de")
_STATUS_TO_ATTEMPT = {
    StageStatus.PLANNED.value: "queued",
    StageStatus.WAITING_FOR_DEPENDENCY.value: "waiting_for_dependency",
    StageStatus.WAITING_FOR_RESOURCE.value: "waiting_for_resource",
    StageStatus.RUNNING.value: "running",
    StageStatus.SUCCEEDED.value: "succeeded",
    StageStatus.FAILED.value: "failed",
    StageStatus.BLOCKED.value: "blocked",
}


class AptamerRuntimeError(RuntimeError):
    """计划或意图无法满足 Session-native 契约时抛出的异常。"""


@dataclass(frozen=True, slots=True)
class SkillRuntimeActivation:
    """进入 Session-native 计划装配的三类服务端证据。"""

    feature_enabled: object = False
    wiring_proven: object = False
    scope_valid: object = False
    route: object = LEGACY_ROUTE

    def __post_init__(self) -> None:
        enabled = self.feature_enabled if type(self.feature_enabled) is bool else False
        wiring = self.wiring_proven if type(self.wiring_proven) is bool else False
        scope = self.scope_valid if type(self.scope_valid) is bool else False
        route = self.route if self.route in {LEGACY_ROUTE, NATIVE_ROUTE} else LEGACY_ROUTE
        object.__setattr__(self, "feature_enabled", enabled)
        object.__setattr__(self, "wiring_proven", wiring)
        object.__setattr__(self, "scope_valid", scope)
        object.__setattr__(self, "route", route)

    @property
    def native(self) -> bool:
        return (
            self.feature_enabled is True
            and self.wiring_proven is True
            and self.scope_valid is True
            and self.route == NATIVE_ROUTE
        )

    @property
    def reason(self) -> str:
        if self.feature_enabled is not True:
            return "feature_flag_disabled"
        if self.route != NATIVE_ROUTE:
            return "legacy_bridge_default"
        if self.wiring_proven is not True:
            return "runtime_wiring_unproven"
        if self.scope_valid is not True:
            return "user_session_scope_unproven"
        return "session_native_verified"


@dataclass(frozen=True, slots=True)
class SessionNativeIntent:
    """单条 session-native 领域实体的持久化意图。"""

    entity: str
    entity_id: str
    idempotency_key: str
    values: Mapping[str, Any]
    operation: str = "ensure"

    def __post_init__(self) -> None:
        for name in ("entity", "entity_id", "idempotency_key"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        if self.operation not in {"ensure", "reuse"}:
            raise ValueError("operation must be ensure or reuse")
        if not isinstance(self.values, Mapping):
            raise TypeError("values must be a mapping")
        object.__setattr__(self, "values", _freeze(self.values))

    @property
    def model(self) -> str:
        return self.entity

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity": self.entity,
            "model": self.model,
            "entity_id": self.entity_id,
            "idempotency_key": self.idempotency_key,
            "operation": self.operation,
            "values": _thaw(self.values),
        }


@dataclass(frozen=True, slots=True)
class StagePlan:
    """领域阶段、持久化身份和只读 ExecuteCapabilityCommand 摘要。"""

    stage_id: str
    label: str
    status: str
    attempt: int
    reason: str
    capabilities: tuple[str, ...]
    depends_on: tuple[str, ...]
    parallel_group: str | None
    required_evidence: tuple[str, ...]
    requires_gpu: bool
    task_id: str
    attempt_id: str
    idempotency_key: str
    capability_commands: tuple[ExecuteCapabilityCommand, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "label": self.label,
            "status": self.status,
            "attempt": self.attempt,
            "reason": self.reason,
            "capabilities": list(self.capabilities),
            "depends_on": list(self.depends_on),
            "parallel_group": self.parallel_group,
            "required_evidence": list(self.required_evidence),
            "requires_gpu": self.requires_gpu,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "idempotency_key": self.idempotency_key,
            "capability_commands": [
                {
                    "capability_id": command.capability_id,
                    "session_id": command.session_id,
                    "user_id": command.user_id,
                    "goal_id": command.goal_id,
                    "skill_execution_id": command.skill_execution_id,
                    "idempotency_key": command.idempotency_key,
                    "inputs": _thaw(command.inputs),
                }
                for command in self.capability_commands
            ],
        }


@dataclass(frozen=True, slots=True)
class AptamerRuntimePlan:
    """计划装配结果；不携带任何执行结果。"""

    skill_id: str
    skill_version: str
    session_id: str
    user_id: str
    target: Mapping[str, Any]
    idempotency_key: str
    request_hash: str
    route: str
    status: str
    reason: str
    goal_id: str
    skill_execution_id: str
    task_graph_id: str
    stages: tuple[StagePlan, ...]
    legacy_research_run_id: str | None = None
    graph_revision: int = 1
    reused: bool = False

    def __post_init__(self) -> None:
        if self.route not in {LEGACY_ROUTE, NATIVE_ROUTE}:
            raise ValueError("route must be legacy_bridge or session_native")
        if not isinstance(self.target, Mapping):
            raise TypeError("target must be a mapping")
        if self.graph_revision < 1:
            raise ValueError("graph_revision must be positive")
        object.__setattr__(self, "target", _freeze(self.target))

    @property
    def native(self) -> bool:
        return self.route == NATIVE_ROUTE and self.status in {
            "planned",
            "waiting_for_resource",
            "waiting_for_dependency",
            "reused",
        }

    @property
    def ready_stage_ids(self) -> tuple[str, ...]:
        return tuple(
            item.stage_id
            for item in self.stages
            if item.status == StageStatus.PLANNED.value and not item.reason
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "skill_id": self.skill_id,
            "skill_version": self.skill_version,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "target": _thaw(self.target),
            "idempotency_key": self.idempotency_key,
            "request_hash": self.request_hash,
            "route": self.route,
            "status": self.status,
            "reason": self.reason,
            "goal_id": self.goal_id,
            "skill_execution_id": self.skill_execution_id,
            "task_graph_id": self.task_graph_id,
            "graph_revision": self.graph_revision,
            "reused": self.reused,
            "stages": [item.to_dict() for item in self.stages],
        }
        if self.legacy_research_run_id is not None:
            payload["legacy_research_run_id"] = self.legacy_research_run_id
        return payload

    def as_aptamer_plan(self) -> AptamerExecutionPlan:
        return AptamerExecutionPlan(
            self.skill_id,
            self.skill_version,
            self.session_id,
            self.user_id,
            self.target,
            tuple(
                StageState(
                    item.stage_id,
                    StageStatus(item.status),
                    item.attempt,
                    item.reason,
                )
                for item in self.stages
            ),
        )

class AptamerSkillRuntime:
    """aptamer_closed_loop 的纯计划装配入口。

    build_plan 只校验输入、注册表和服务端激活证据；build_intents 只生成可
    交给已有 UoW/Repository 的值字典。两者都不会创建 SQLAlchemy Session、
    提交事务或触发同步/异步科学能力。
    """

    def __init__(
        self,
        registry: AptamerSkillRegistry | None = None,
        activation: SkillRuntimeActivation | None = None,
        *,
        feature_enabled: object = False,
        wiring_proven: object = False,
        scope_valid: object = False,
        route: object = LEGACY_ROUTE,
    ) -> None:
        self.registry = registry or get_aptamer_skill_registry()
        if not isinstance(self.registry, AptamerSkillRegistry):
            raise TypeError("registry must be an AptamerSkillRegistry")
        if activation is None:
            activation = SkillRuntimeActivation(
                feature_enabled=feature_enabled,
                wiring_proven=wiring_proven,
                scope_valid=scope_valid,
                route=route,
            )
        if not isinstance(activation, SkillRuntimeActivation):
            raise TypeError("activation must be a SkillRuntimeActivation")
        self.activation = activation

    @staticmethod
    def _id(kind: str, key: str) -> str:
        return str(uuid5(_NAMESPACE, f"{kind}:{key}"))

    @staticmethod
    def _idempotency_key(value: object, request_hash: str) -> str:
        if value is None:
            return f"aptamer:{request_hash}"
        if not isinstance(value, str) or not value.strip():
            raise ValueError("idempotency_key must be non-empty")
        if len(value.strip()) > 240:
            raise ValueError("idempotency_key must be at most 240 characters")
        return value.strip()

    @staticmethod
    def _target_identifier(target: Mapping[str, Any]) -> tuple[str, str]:
        if "pdb_id" in target:
            return "pdb", str(target["pdb_id"])
        if "structure_artifact_id" in target:
            return "structure_artifact", str(target["structure_artifact_id"])
        return "protein_sequence", str(target.get("protein_sequence", ""))[:64]

    def _stage_plans(
        self,
        *,
        base: AptamerExecutionPlan,
        registered: RegisteredSkill,
        goal_id: str,
        skill_execution_id: str,
        graph_id: str,
        idempotency_key: str,
        capability_inputs: Mapping[str, Any] | None,
    ) -> tuple[StagePlan, ...]:
        state_by_id = {item.stage_id: item for item in base.stages}
        input_map = capability_inputs if isinstance(capability_inputs, Mapping) else {}
        stages: list[StagePlan] = []
        for binding in registered.stage_bindings:
            spec = binding.stage
            state = state_by_id[spec.stage_id]
            stage_key = f"{idempotency_key}:stage:{spec.stage_id}"
            task_id = self._id("scientific-task", f"{graph_id}:{stage_key}")
            attempt_no = max(state.attempt + 1, 1)
            attempt_id = self._id(
                "execution-attempt", f"{task_id}:{attempt_no}:{base.session_id}:{base.user_id}"
            )
            raw_stage_input = input_map.get(spec.stage_id, {})
            if raw_stage_input is None:
                raw_stage_input = {}
            if not isinstance(raw_stage_input, Mapping):
                raise ValueError(f"capability_inputs[{spec.stage_id}] must be a mapping")
            _reject_legacy(raw_stage_input)
            command_inputs = {
                "target": _thaw(base.target),
                "stage_id": spec.stage_id,
                **_thaw(raw_stage_input),
            }
            commands = tuple(
                ExecuteCapabilityCommand(
                    capability_id=capability_id,
                    session_id=base.session_id,
                    user_id=base.user_id,
                    inputs=command_inputs,
                    idempotency_key=f"{stage_key}:capability:{capability_id}",
                    goal_id=goal_id,
                    skill_execution_id=skill_execution_id,
                )
                for capability_id in spec.capabilities
            )
            stages.append(
                StagePlan(
                    stage_id=spec.stage_id,
                    label=spec.label,
                    status=state.status.value,
                    attempt=state.attempt,
                    reason=state.reason,
                    capabilities=spec.capabilities,
                    depends_on=spec.depends_on,
                    parallel_group=spec.parallel_group,
                    required_evidence=spec.required_evidence,
                    requires_gpu=spec.requires_gpu,
                    task_id=task_id,
                    attempt_id=attempt_id,
                    idempotency_key=stage_key,
                    capability_commands=commands,
                )
            )
        return tuple(stages)

    def build_plan(
        self,
        session_id: str,
        user_id: str,
        target: Mapping[str, Any],
        *,
        idempotency_key: object = None,
        resources: Mapping[str, Any] | None = None,
        capability_inputs: Mapping[str, Any] | None = None,
        legacy_research_run_id: object = None,
        existing_plan: AptamerRuntimePlan | None = None,
        stage_plan: AptamerExecutionPlan | None = None,
    ) -> AptamerRuntimePlan:
        """构建计划；普通会话不需要、也不会隐式生成 research_run_id。"""

        session_text = _require_text(session_id, "session_id")
        user_text = _require_text(user_id, "user_id")
        if not isinstance(target, Mapping):
            raise TypeError("target must be a mapping")
        _reject_legacy(target)
        legacy = None
        if legacy_research_run_id is not None:
            legacy = _require_text(legacy_research_run_id, "legacy_research_run_id")
        registered = self.registry.require_skill(SKILL_ID)
        base = stage_plan or build_aptamer_closed_loop_plan(
            session_id=session_text,
            user_id=user_text,
            target=target,
        )
        if base.session_id != session_text or base.user_id != user_text:
            raise ValueError("stage_plan scope does not match request")
        request_hash = _hash(
            {
                "skill_id": registered.skill_id,
                "skill_version": registered.version,
                "session_id": session_text,
                "user_id": user_text,
                "target": _thaw(base.target),
                "legacy_research_run_id": legacy,
            }
        )
        key = self._idempotency_key(idempotency_key, request_hash)
        if existing_plan is not None:
            if not isinstance(existing_plan, AptamerRuntimePlan):
                raise TypeError("existing_plan must be an AptamerRuntimePlan")
            if existing_plan.idempotency_key == key and existing_plan.request_hash != request_hash:
                return replace(
                    existing_plan,
                    status="idempotency_conflict",
                    reason="idempotency key is bound to a different canonical request",
                    reused=False,
                )
            if (
                existing_plan.idempotency_key == key
                and existing_plan.request_hash == request_hash
            ):
                return replace(
                    existing_plan,
                    status="reused",
                    reason="existing plan reused",
                    reused=True,
                )
        if not registered.contract_ready:
            status = "unavailable"
            reason = "skill capability manifest is incomplete"
            route = LEGACY_ROUTE
        elif not self.activation.native:
            status = "unavailable"
            reason = self.activation.reason
            route = LEGACY_ROUTE
        else:
            gated = apply_resource_gate(base, resources if resources is not None else {})
            base = gated
            statuses = {item.status for item in base.stages}
            status = (
                "waiting_for_resource"
                if StageStatus.WAITING_FOR_RESOURCE in statuses
                else (
                    "waiting_for_dependency"
                    if StageStatus.WAITING_FOR_DEPENDENCY in statuses
                    else "planned"
                )
            )
            reason = (
                "gpu_required_for_alphafold3"
                if status == "waiting_for_resource"
                else "session_native_plan_assembled"
            )
            route = NATIVE_ROUTE
        goal_id = self._id("research-goal", f"{session_text}:{user_text}:{key}")
        skill_execution_id = self._id(
            "skill-execution", f"{goal_id}:{registered.skill_id}:{registered.version}"
        )
        graph_id = self._id("task-graph", f"{session_text}:{key}:{registered.version}")
        stages = self._stage_plans(
            base=base,
            registered=registered,
            goal_id=goal_id,
            skill_execution_id=skill_execution_id,
            graph_id=graph_id,
            idempotency_key=key,
            capability_inputs=capability_inputs,
        )
        return AptamerRuntimePlan(
            skill_id=registered.skill_id,
            skill_version=registered.version,
            session_id=session_text,
            user_id=user_text,
            target=base.target,
            idempotency_key=key,
            request_hash=request_hash,
            route=route,
            status=status,
            reason=reason,
            goal_id=goal_id,
            skill_execution_id=skill_execution_id,
            task_graph_id=graph_id,
            stages=stages,
            legacy_research_run_id=legacy,
            reused=False,
        )

    def build_intents(
        self,
        plan: AptamerRuntimePlan,
        *,
        capability_inputs: Mapping[str, Any] | None = None,
    ) -> tuple[SessionNativeIntent, ...]:
        """把已允许的计划转为单事务持久化意图；拒绝状态返回空元组。"""

        if not isinstance(plan, AptamerRuntimePlan):
            raise TypeError("plan must be an AptamerRuntimePlan")
        if not plan.native or plan.status in {"idempotency_conflict", "unavailable"}:
            return ()
        if capability_inputs is not None:
            self._stage_plans(
                base=plan.as_aptamer_plan(),
                registered=self.registry.require_skill(SKILL_ID),
                goal_id=plan.goal_id,
                skill_execution_id=plan.skill_execution_id,
                graph_id=plan.task_graph_id,
                idempotency_key=plan.idempotency_key,
                capability_inputs=capability_inputs,
            )
        registered = self.registry.require_skill(SKILL_ID)
        manifest = registered.manifest
        target_type, target_identifier = self._target_identifier(plan.target)
        target_hash = _hash(_thaw(plan.target))
        root = plan.idempotency_key
        metadata = {
            "runtime_route": plan.route,
            "request_hash": plan.request_hash,
            "idempotency_key": root,
            "skill_id": plan.skill_id,
            "skill_version": plan.skill_version,
        }
        if plan.legacy_research_run_id is not None:
            metadata["legacy_source"] = plan.legacy_research_run_id
        intents: list[SessionNativeIntent] = []

        def add(entity: str, entity_id: str, suffix: str, values: Mapping[str, Any]) -> None:
            intents.append(
                SessionNativeIntent(
                    entity=entity,
                    entity_id=entity_id,
                    idempotency_key=f"{root}:{suffix}",
                    values=values,
                )
            )

        add(
            "ResearchSession",
            plan.session_id,
            "session",
            {
                "id": plan.session_id,
                "user_id": plan.user_id,
                "status": "active",
                "title": "aptamer_closed_loop",
                "metadata_json": metadata,
            },
        )
        goal_values: dict[str, Any] = {
            "id": plan.goal_id,
            "user_id": plan.user_id,
            "title": "aptamer_closed_loop",
            "objective": str(
                plan.target.get("objective") or "AI4S aptamer closed-loop design"
            ),
            "status": "draft",
            "scope_json": {"target": _thaw(plan.target)},
            "constraints_json": dict(manifest.recovery),
            "success_criteria_json": {
                "required_stage_evidence": {
                    item.stage_id: list(item.required_evidence)
                    for item in plan.stages
                }
            },
            "metadata_json": metadata,
        }
        if plan.legacy_research_run_id is not None:
            goal_values["legacy_research_run_id"] = plan.legacy_research_run_id
        add("ResearchGoal", plan.goal_id, "goal", goal_values)
        add(
            "SessionGoalLink",
            self._id("session-goal-link", f"{plan.session_id}:{plan.goal_id}"),
            "session-goal",
            {
                "session_id": plan.session_id,
                "goal_id": plan.goal_id,
                "user_id": plan.user_id,
                "role": "primary",
            },
        )
        target_values: dict[str, Any] = {
            "id": self._id("scientific-target", f"{plan.goal_id}:{target_hash}"),
            "goal_id": plan.goal_id,
            "user_id": plan.user_id,
            "name": target_identifier,
            "target_type": target_type,
            "identifier": target_identifier,
            "chain_id": plan.target.get("chain"),
            "sequence": plan.target.get("protein_sequence"),
            "structure_source": plan.target.get("structure_source"),
            "structure_version": plan.target.get("structure_version"),
            "identity_hash": target_hash,
            "status": "active",
            "source_json": {"target": _thaw(plan.target)},
            "metadata_json": metadata,
        }
        add("ScientificTarget", str(target_values["id"]), "target", target_values)
        skill_values: dict[str, Any] = {
            "id": plan.skill_execution_id,
            "goal_id": plan.goal_id,
            "user_id": plan.user_id,
            "skill_id": plan.skill_id,
            "skill_version": plan.skill_version,
            "status": "planned",
            "plan_version": plan.graph_revision,
            "input_json": {"target": _thaw(plan.target)},
            "config_json": {
                "recovery": dict(manifest.recovery),
                "resource_policy": dict(manifest.resource_policy),
            },
            "evidence_contract_json": dict(manifest.evidence_policy),
            "metadata_json": metadata,
        }
        if plan.legacy_research_run_id is not None:
            skill_values["legacy_research_run_id"] = plan.legacy_research_run_id
        add("SkillExecution", plan.skill_execution_id, "skill-execution", skill_values)
        add(
            "TaskGraph",
            plan.task_graph_id,
            "task-graph",
            {
                "id": plan.task_graph_id,
                "session_id": plan.session_id,
                "user_id": plan.user_id,
                "goal_id": plan.goal_id,
                "skill_execution_id": plan.skill_execution_id,
                "name": "aptamer_closed_loop",
                "status": "planned" if plan.status == "planned" else plan.status,
                "revision": plan.graph_revision,
                "metadata_json": {**metadata, "stage_count": len(plan.stages)},
            },
        )
        for stage in plan.stages:
            task_status = stage.status
            attempt_status = _STATUS_TO_ATTEMPT.get(task_status, "queued")
            stage_extra = {}
            if isinstance(capability_inputs, Mapping):
                raw = capability_inputs.get(stage.stage_id, {})
                if raw is not None:
                    if not isinstance(raw, Mapping):
                        raise ValueError(f"capability_inputs[{stage.stage_id}] must be a mapping")
                    _reject_legacy(raw)
                    stage_extra = _thaw(raw)
            task_input = {
                "target": _thaw(plan.target),
                "stage_id": stage.stage_id,
                **stage_extra,
            }
            task_values = {
                "id": stage.task_id,
                "task_graph_id": plan.task_graph_id,
                "skill_execution_id": plan.skill_execution_id,
                "user_id": plan.user_id,
                "task_key": f"stage:{stage.stage_id}",
                "name": stage.label,
                "task_type": f"{plan.skill_id}:{stage.stage_id}",
                "status": task_status,
                "priority": 0,
                "queue_name": "ai4s-science",
                "input_json": task_input,
                "completion_contract_json": {
                    "required_evidence": list(stage.required_evidence),
                    "requires_terminal_status": True,
                    "never_fake_result": True,
                },
                "resource_request_json": {
                    "profile_status": "unverified",
                    "resolved_at_runtime": True,
                    "gpu_count": 1 if stage.requires_gpu else 0,
                },
                "idempotency_key": stage.idempotency_key,
                "metadata_json": {
                    **metadata,
                    "stage_id": stage.stage_id,
                    "capabilities": list(stage.capabilities),
                },
            }
            add("ScientificTask", stage.task_id, f"task:{stage.stage_id}", task_values)
            add(
                "ExecutionAttempt",
                stage.attempt_id,
                f"attempt:{stage.stage_id}:{stage.attempt + 1}",
                {
                    "id": stage.attempt_id,
                    "scientific_task_id": stage.task_id,
                    "user_id": plan.user_id,
                    "attempt_no": stage.attempt + 1,
                    "status": attempt_status,
                    "idempotency_key": (
                        f"{stage.idempotency_key}:attempt:{stage.attempt + 1}"
                    ),
                    "input_json": task_input,
                    "output_json": {},
                    "metadata_json": {
                        **metadata,
                        "stage_id": stage.stage_id,
                        "checkpointed": True,
                    },
                },
            )
        stage_by_id = {item.stage_id: item for item in plan.stages}
        for stage in plan.stages:
            for upstream_id in stage.depends_on:
                upstream = stage_by_id[upstream_id]
                edge_id = self._id(
                    "task-dependency",
                    f"{plan.task_graph_id}:{upstream.task_id}:{stage.task_id}",
                )
                add(
                    "TaskDependency",
                    edge_id,
                    f"dependency:{upstream_id}:{stage.stage_id}",
                    {
                        "id": edge_id,
                        "task_graph_id": plan.task_graph_id,
                        "skill_execution_id": plan.skill_execution_id,
                        "upstream_task_id": upstream.task_id,
                        "downstream_task_id": stage.task_id,
                        "dependency_type": "required",
                    },
                )
        checkpoint_id = self._id(
            "workflow-checkpoint",
            f"{plan.skill_execution_id}:{plan.graph_revision}",
        )
        add(
            "WorkflowCheckpoint",
            checkpoint_id,
            f"checkpoint:{plan.graph_revision}",
            {
                "id": checkpoint_id,
                "skill_execution_id": plan.skill_execution_id,
                "checkpoint_no": plan.graph_revision,
                "plan_version": plan.graph_revision,
                "status": "active",
                "state_json": {
                    "plan_status": plan.status,
                    "stage_states": {
                        item.stage_id: {
                            "status": item.status,
                            "attempt": item.attempt,
                            "reason": item.reason,
                        }
                        for item in plan.stages
                    },
                },
                "ready_task_ids_json": [
                    item.task_id for item in plan.stages if item.stage_id in plan.ready_stage_ids
                ],
                "blocked_dependency_ids_json": [
                    item.task_id
                    for item in plan.stages
                    if item.status == StageStatus.WAITING_FOR_DEPENDENCY.value
                ],
                "digest": plan.request_hash,
                "metadata_json": metadata,
            },
        )
        return tuple(intents)

    @staticmethod
    def evidence_gate(stage_id: str, receipt: Mapping[str, Any]):
        """复用既有纯 EvidenceGate；缺字段/异常输入始终拒绝。"""

        return evaluate_evidence_gate(stage_id, receipt)

    @staticmethod
    def recover(plan: AptamerRuntimePlan, failed_stage_id: str) -> AptamerRuntimePlan:
        """只重置失败阶段及其下游，并增加失败阶段的 Attempt 序号。"""

        if not isinstance(plan, AptamerRuntimePlan):
            raise TypeError("plan must be an AptamerRuntimePlan")
        recovered = recover_from_stage(plan.as_aptamer_plan(), failed_stage_id)
        runtime = AptamerSkillRuntime(
            activation=SkillRuntimeActivation(
                feature_enabled=True,
                wiring_proven=True,
                scope_valid=True,
                route=plan.route,
            )
        )
        registered = runtime.registry.require_skill(SKILL_ID)
        stages = runtime._stage_plans(
            base=recovered,
            registered=registered,
            goal_id=plan.goal_id,
            skill_execution_id=plan.skill_execution_id,
            graph_id=plan.task_graph_id,
            idempotency_key=plan.idempotency_key,
            capability_inputs=None,
        )
        status = (
            "waiting_for_resource"
            if any(item.status == StageStatus.WAITING_FOR_RESOURCE.value for item in stages)
            else "planned"
        )
        return replace(
            plan,
            status=status,
            reason="recovery_checkpoint",
            stages=stages,
            graph_revision=plan.graph_revision + 1,
            reused=False,
        )


def _require_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty")
    return value.strip()


def _reject_legacy(value: Any) -> None:
    if isinstance(value, Mapping):
        if "research_run_id" in value:
            raise ValueError("research_run_id is not accepted by session-native aptamer plan")
        for item in value.values():
            _reject_legacy(item)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            _reject_legacy(item)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set | frozenset):
        return frozenset(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, frozenset)):
        return [_thaw(item) for item in value]
    return value


def _json_ready(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, frozenset)):
        return [_json_ready(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _hash(value: Any) -> str:
    payload = json.dumps(
        _json_ready(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "AptamerRuntimeError",
    "AptamerRuntimePlan",
    "AptamerSkillRuntime",
    "LEGACY_ROUTE",
    "NATIVE_ROUTE",
    "SessionNativeIntent",
    "SkillRuntimeActivation",
    "StagePlan",
]
