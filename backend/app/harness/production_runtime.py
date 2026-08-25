"""受控的 Harness 生产运行时组合层。

wzf：本模块只做依赖组合和 fail-closed 资格校验，不修改应用启动、Agent、API
或 Worker 的现有入口。生产调用方必须显式提供 feature flag、能力清单、接线
证据、用户/session 作用域、Session-native 持久化端口以及已经完成策略接线的
SessionCapabilityExecutor；缺少任一项时只返回 legacy_bridge 组合，不泄漏部分
构造对象，也不创建数据库 Session、事务或 Worker。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.harness.capabilities import CapabilityManifest
from app.harness.execution import SessionCapabilityExecutor
from app.harness.executor import AptamerSkillRuntime, SkillRuntimeActivation
from app.harness.feature_flags import (
    FeatureFlagConfig,
    FeatureFlagDecision,
    RuntimeWiringEvidence,
    UserSessionScopeEvidence,
    evaluate_feature_flag,
)
from app.harness.runtime_adapter import (
    AptamerRuntimeAdapter,
    SessionNativePersistencePort,
)
from app.harness.runtime_wiring import (
    LEGACY_ROUTE,
    NATIVE_ROUTE,
    RuntimeWiringContext,
)


DEFAULT_PRODUCTION_CAPABILITY = "fetch_pdb_info"


@dataclass(frozen=True, slots=True)
class ProductionRuntimeComposition:
    """一次请求可消费的已验证组合；legacy 组合不携带半成品依赖。

    session_capability_executor 必须由调用方先完成 Repository、策略、收据
    和真实科学执行器的受控装配。本层不会为这些依赖猜测默认实现。
    """

    capability_id: str
    route: str
    reason: str
    feature_decision: FeatureFlagDecision
    context: RuntimeWiringContext | None = None
    session_capability_executor: SessionCapabilityExecutor | None = None
    runtime_adapter: AptamerRuntimeAdapter | None = None
    persistence_port: SessionNativePersistencePort | Any | None = None
    activation: SkillRuntimeActivation = field(default_factory=SkillRuntimeActivation)

    def __post_init__(self) -> None:
        if not isinstance(self.capability_id, str) or not self.capability_id.strip():
            raise ValueError("capability_id must be non-empty")
        if self.route not in {LEGACY_ROUTE, NATIVE_ROUTE}:
            raise ValueError("route must be legacy_bridge or session_native")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise ValueError("reason must be non-empty")
        if not isinstance(self.feature_decision, FeatureFlagDecision):
            raise TypeError("feature_decision must be FeatureFlagDecision")
        if not isinstance(self.activation, SkillRuntimeActivation):
            raise TypeError("activation must be SkillRuntimeActivation")

        native_parts = (
            self.context,
            self.session_capability_executor,
            self.runtime_adapter,
            self.persistence_port,
        )
        if self.route == NATIVE_ROUTE:
            if not self.feature_decision.enabled or not self.activation.native:
                raise ValueError("native composition requires enabled activation")
            if any(item is None for item in native_parts):
                raise ValueError("native composition cannot contain missing dependencies")
        elif any(item is not None for item in native_parts):
            raise ValueError("legacy composition must not expose native dependencies")

    @property
    def enabled(self) -> bool:
        """是否具备进入 Session-native 旁路的完整组合。"""

        return self.route == NATIVE_ROUTE

    @property
    def native(self) -> bool:
        return self.enabled

    @property
    def legacy(self) -> bool:
        return not self.enabled

    # 这些只读别名让 Agent/API 装配层可以使用更短的依赖名，不产生第二份状态。
    @property
    def executor(self) -> SessionCapabilityExecutor | None:
        return self.session_capability_executor

    @property
    def adapter(self) -> AptamerRuntimeAdapter | None:
        return self.runtime_adapter

    @property
    def wiring_context(self) -> RuntimeWiringContext | None:
        return self.context


def _disabled(
    capability_id: str,
    reason: str,
    decision: FeatureFlagDecision | None = None,
) -> ProductionRuntimeComposition:
    """构造不带任何 native 依赖的稳定 legacy 结果。"""

    if decision is None:
        decision = evaluate_feature_flag(capability_id)
    return ProductionRuntimeComposition(
        capability_id=capability_id,
        route=LEGACY_ROUTE,
        reason=reason,
        feature_decision=decision,
        activation=SkillRuntimeActivation(),
    )


def _executor_manifest(
    executor: SessionCapabilityExecutor,
    capability_id: str,
) -> object | None:
    registry = getattr(executor, "registry", None)
    if isinstance(registry, Mapping):
        return registry.get(capability_id)
    getter = getattr(registry, "get_manifest", None)
    if callable(getter):
        try:
            return getter(capability_id)
        except Exception:
            return None
    return None


def _same_manifest(executor: SessionCapabilityExecutor, manifest: object, capability_id: str) -> bool:
    bound = _executor_manifest(executor, capability_id)
    if bound is None:
        return False
    if getattr(bound, "capability_id", None) != capability_id:
        return False
    bound_digest = getattr(bound, "digest", None)
    manifest_digest = getattr(manifest, "digest", None)
    return isinstance(bound_digest, str) and bool(bound_digest) and bound_digest == manifest_digest


def _scope_payload(scope: UserSessionScopeEvidence) -> dict[str, object]:
    return {
        "in_scope": scope.in_scope,
        "user_id": str(scope.user_id),
        "session_id": str(scope.session_id),
    }


def _adapter_scope_matches(
    adapter: AptamerRuntimeAdapter,
    scope: UserSessionScopeEvidence,
) -> bool:
    evidence = getattr(adapter, "scope_evidence", None)
    if evidence is None:
        return True
    if not isinstance(evidence, Mapping):
        return False
    return (
        evidence.get("in_scope") is True
        and str(evidence.get("user_id") or "") == str(scope.user_id)
        and str(evidence.get("session_id") or "") == str(scope.session_id)
    )


def build_production_runtime(
    *,
    capability_id: str = DEFAULT_PRODUCTION_CAPABILITY,
    feature_config: FeatureFlagConfig | None = None,
    manifest: CapabilityManifest | Any | None = None,
    wiring_evidence: RuntimeWiringEvidence | None = None,
    scope_evidence: UserSessionScopeEvidence | None = None,
    persistence_port: SessionNativePersistencePort | Any | None = None,
    session_capability_executor: SessionCapabilityExecutor | None = None,
    runtime_adapter: AptamerRuntimeAdapter | None = None,
    aptamer_runtime: AptamerSkillRuntime | None = None,
) -> ProductionRuntimeComposition:
    """显式构造一份生产组合；证据或受控依赖不完整时固定回退 legacy。

    该函数不会从环境变量、全局单例、请求 claims 或数据库推导任何证据。为了
    防止把未完成的策略/科学接线误当作可执行实现，session_capability_executor
    也必须由调用方显式注入；没有它即使 flag 已打开也只能回 legacy。
    """

    if not isinstance(capability_id, str) or not capability_id.strip():
        return _disabled(DEFAULT_PRODUCTION_CAPABILITY, "invalid_capability")
    capability_id = capability_id.strip()

    decision = evaluate_feature_flag(
        capability_id,
        feature_config,
        manifest,
        wiring_evidence,
        scope_evidence,
    )
    if not decision.enabled:
        return _disabled(capability_id, decision.reason, decision)

    if not isinstance(wiring_evidence, RuntimeWiringEvidence):
        return _disabled(capability_id, "runtime_wiring_evidence_required", decision)
    if not isinstance(scope_evidence, UserSessionScopeEvidence):
        return _disabled(capability_id, "scope_evidence_required", decision)
    if not isinstance(manifest, CapabilityManifest):
        return _disabled(capability_id, "capability_manifest_required", decision)
    if not callable(getattr(persistence_port, "persist_session_native_atomically", None)):
        return _disabled(capability_id, "session_native_persistence_port_required", decision)
    if not isinstance(session_capability_executor, SessionCapabilityExecutor):
        return _disabled(capability_id, "session_capability_executor_required", decision)

    activated = getattr(session_capability_executor, "activated_capabilities", frozenset())
    if capability_id not in activated:
        return _disabled(capability_id, "capability_executor_not_activated", decision)
    if not _same_manifest(session_capability_executor, manifest, capability_id):
        return _disabled(capability_id, "capability_manifest_mismatch", decision)

    activation = SkillRuntimeActivation(
        feature_enabled=True,
        wiring_proven=True,
        scope_valid=True,
        route=NATIVE_ROUTE,
    )
    if runtime_adapter is None:
        try:
            runtime = aptamer_runtime or AptamerSkillRuntime(activation=activation)
            runtime_adapter = AptamerRuntimeAdapter(
                runtime=runtime,
                activation=activation,
                scope_evidence=_scope_payload(scope_evidence),
            )
        except Exception:
            return _disabled(capability_id, "runtime_adapter_unavailable", decision)
    elif not isinstance(runtime_adapter, AptamerRuntimeAdapter):
        return _disabled(capability_id, "runtime_adapter_required", decision)

    if runtime_adapter.activation.native is not True:
        return _disabled(capability_id, "runtime_adapter_activation_unproven", decision)
    if not _adapter_scope_matches(runtime_adapter, scope_evidence):
        return _disabled(capability_id, "runtime_adapter_scope_mismatch", decision)

    context = RuntimeWiringContext(
        feature_config=feature_config,
        manifest=manifest,
        wiring_evidence=wiring_evidence,
        scope_evidence=scope_evidence,
    )
    return ProductionRuntimeComposition(
        capability_id=capability_id,
        route=NATIVE_ROUTE,
        reason="session_native_runtime_composed",
        feature_decision=decision,
        context=context,
        session_capability_executor=session_capability_executor,
        runtime_adapter=runtime_adapter,
        persistence_port=persistence_port,
        activation=activation,
    )


# 便于不同启动层采用同一明确语义；均指向同一个纯组合器。
compose_production_runtime = build_production_runtime
create_production_runtime = build_production_runtime


__all__ = [
    "DEFAULT_PRODUCTION_CAPABILITY",
    "ProductionRuntimeComposition",
    "build_production_runtime",
    "compose_production_runtime",
    "create_production_runtime",
]