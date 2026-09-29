"""AI4S Harness 的受控特性开关判定。

本模块只提供不可变值对象和纯判定函数。它不读取环境变量、生产配置、数据库或
网络，也不把客户端文本、工具参数或 ``CapabilityManifest`` 本身当作授权事实。
后续接线层必须显式传入这里定义的运行时配置、Manifest、接线证明和当前作用域
证据；任何缺失、类型异常或不一致都返回稳定的拒绝原因。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import UUID


FETCH_PDB_INFO = "fetch_pdb_info"
"""本批唯一允许受控启用的能力。"""

SUPPORTED_CAPABILITIES = frozenset({FETCH_PDB_INFO})


class FeatureFlagReason(str, Enum):
    """稳定且不含输入值、异常文本或敏感信息的判定原因。"""

    ENABLED = "enabled"
    DEFAULT_DISABLED = "default_disabled"
    INVALID_CAPABILITY = "invalid_capability"
    INVALID_CONFIG = "invalid_config"
    CAPABILITY_NOT_ALLOWLISTED = "capability_not_allowlisted"
    MANIFEST_REQUIRED = "manifest_required"
    MANIFEST_NOT_NATIVE = "manifest_not_native"
    WIRING_REQUIRED = "wiring_required"
    WIRING_NOT_PROVEN = "wiring_not_proven"
    SCOPE_REQUIRED = "scope_required"
    SCOPE_NOT_PROVEN = "scope_not_proven"


_REASONS = frozenset(item.value for item in FeatureFlagReason)
_MISSING = object()


def _normalise_allowlist(value: object) -> tuple[tuple[str, ...], bool]:
    """将受控 allowlist 复制为不可变 tuple，并拒绝模糊输入。"""

    if value is None or isinstance(value, (str, bytes, bytearray, Mapping)):
        return (), False
    if not isinstance(value, Iterable):
        return (), False
    try:
        values = tuple(value)
    except Exception:
        return (), False
    if not values:
        return (), True
    if any(
        type(item) is not str
        or not item
        or item != item.strip()
        or item not in SUPPORTED_CAPABILITIES
        for item in values
    ):
        return (), False
    if len(set(values)) != len(values):
        return (), False
    return tuple(sorted(values)), True


def _normalise_capability(value: object) -> str | None:
    if type(value) is not str or value not in SUPPORTED_CAPABILITIES:
        return None
    return value


@dataclass(frozen=True, slots=True)
class FeatureFlagConfig:
    """由受控运行时显式传入的不可变开关配置。

    ``capability_allowlist`` 是规范字段；其余三个字段仅是接线层迁移时的同义
    输入。多个字段同时提供且不一致会被标记为无效，判定函数随后 fail-closed。
    构造函数不抛出用户输入异常，避免错误文本进入普通响应；无效对象永远不能
    产生允许结果。
    """

    global_enabled: object = False
    capability_allowlist: object = ()
    enabled_capabilities: object | None = None
    allowed_capabilities: object | None = None
    allowlist: object | None = None
    enabled: object | None = None
    _valid: bool = field(default=True, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        bool_valid = type(self.global_enabled) is bool
        effective_global = self.global_enabled if bool_valid else False

        raw_sources: list[object] = []
        # 空 tuple 是规范字段的默认值；只有存在其他别名时才把非空规范值纳入
        # 冲突比较，避免迁移层仅传 ``allowlist=...`` 时被默认值误判为冲突。
        try:
            primary_is_default = self.capability_allowlist == ()
        except Exception:
            primary_is_default = False
        if not primary_is_default or all(
            item is None
            for item in (self.enabled_capabilities, self.allowed_capabilities, self.allowlist)
        ):
            raw_sources.append(self.capability_allowlist)
        raw_sources.extend(
            item
            for item in (self.enabled_capabilities, self.allowed_capabilities, self.allowlist)
            if item is not None
        )
        normalised: list[tuple[str, ...]] = []
        source_valid = True
        for source in raw_sources:
            values, valid = _normalise_allowlist(source)
            source_valid = source_valid and valid
            normalised.append(values)
        first = normalised[0] if normalised else ()
        if any(values != first for values in normalised[1:]):
            source_valid = False

        if self.enabled is not None:
            if type(self.enabled) is not bool or self.enabled != effective_global:
                bool_valid = False

        object.__setattr__(self, "global_enabled", effective_global)
        object.__setattr__(self, "enabled", effective_global)
        object.__setattr__(self, "capability_allowlist", first)
        object.__setattr__(self, "enabled_capabilities", first)
        object.__setattr__(self, "allowed_capabilities", first)
        object.__setattr__(self, "allowlist", first)
        object.__setattr__(self, "_valid", bool_valid and source_valid)

    @property
    def valid(self) -> bool:
        return self._valid

    @classmethod
    def default(cls) -> "FeatureFlagConfig":
        """返回默认关闭且不含任何能力的配置。"""

        return cls()


@dataclass(frozen=True, slots=True)
class RuntimeWiringEvidence:
    """能力进入原生执行器前必须由接线层提供的不可变证明。"""

    capability_id: object
    proven: object = False
    runtime_wired: object | None = None
    proof_ref: object | None = None
    _valid: bool = field(default=True, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        capability = _normalise_capability(self.capability_id)
        proven_valid = type(self.proven) is bool
        runtime_valid = self.runtime_wired is None or type(self.runtime_wired) is bool
        effective = self.proven if proven_valid else False
        if self.runtime_wired is not None and (
            not runtime_valid or self.runtime_wired != effective
        ):
            runtime_valid = False
            effective = False
        ref_valid = type(self.proof_ref) is str and bool(self.proof_ref) and (
            self.proof_ref == self.proof_ref.strip()
        )
        object.__setattr__(self, "capability_id", capability or "")
        object.__setattr__(self, "proven", effective if type(effective) is bool else False)
        object.__setattr__(self, "runtime_wired", effective if type(effective) is bool else False)
        object.__setattr__(self, "_valid", capability is not None and proven_valid and runtime_valid and ref_valid)

    @property
    def valid(self) -> bool:
        return self._valid


RuntimeWiringProof = RuntimeWiringEvidence


@dataclass(frozen=True, slots=True)
class UserSessionScopeEvidence:
    """当前用户/session 作用域的受控证据；ID 仅用于匹配，不会进入拒绝原因。"""

    capability_id: object
    user_id: object
    session_id: object
    in_scope: object = False
    authorized: object | None = None
    scope_valid: object | None = None
    _valid: bool = field(default=True, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        capability = _normalise_capability(self.capability_id)
        text_valid = all(_is_uuid_text(value) for value in (self.user_id, self.session_id))
        bool_values = [self.in_scope]
        bool_values.extend(
            value for value in (self.authorized, self.scope_valid) if value is not None
        )
        bool_valid = all(type(value) is bool for value in bool_values)
        effective = self.in_scope if type(self.in_scope) is bool else False
        for alias in (self.authorized, self.scope_valid):
            if alias is not None and (type(alias) is not bool or alias != effective):
                bool_valid = False
                effective = False
        object.__setattr__(self, "capability_id", capability or "")
        object.__setattr__(self, "user_id", self.user_id if text_valid else "")
        object.__setattr__(self, "session_id", self.session_id if text_valid else "")
        object.__setattr__(self, "in_scope", effective if type(effective) is bool else False)
        object.__setattr__(self, "authorized", effective if type(effective) is bool else False)
        object.__setattr__(self, "scope_valid", effective if type(effective) is bool else False)
        object.__setattr__(self, "_valid", capability is not None and text_valid and bool_valid)

    @property
    def valid(self) -> bool:
        return self._valid


CurrentUserSessionEvidence = UserSessionScopeEvidence
CurrentUserSessionScope = UserSessionScopeEvidence
ScopeEvidence = UserSessionScopeEvidence


def _is_uuid_text(value: object) -> bool:
    if type(value) is not str or not value or value != value.strip():
        return False
    try:
        UUID(value)
    except (TypeError, ValueError, AttributeError):
        return False
    return True


@dataclass(frozen=True, slots=True)
class FeatureFlagDecision:
    """不可变、可审计且不回显原始输入的开关结果。"""

    enabled: bool
    reason: str
    capability_id: str = ""

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            object.__setattr__(self, "enabled", False)
        if not isinstance(self.reason, str) or self.reason not in _REASONS:
            object.__setattr__(self, "reason", FeatureFlagReason.INVALID_CONFIG.value)
        if not isinstance(self.capability_id, str) or self.capability_id not in SUPPORTED_CAPABILITIES:
            object.__setattr__(self, "capability_id", "")

    @property
    def allowed(self) -> bool:
        return self.enabled

    @property
    def ok(self) -> bool:
        return self.enabled

    @property
    def decision(self) -> str:
        return "allow" if self.enabled else "deny"

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "allowed": self.enabled,
            "decision": self.decision,
            "reason": self.reason,
            "capability_id": self.capability_id,
        }


FeatureFlagResult = FeatureFlagDecision
RuntimeFeatureConfig = FeatureFlagConfig


def _manifest_reason(manifest: object, capability_id: str) -> FeatureFlagReason:
    if manifest is None:
        return FeatureFlagReason.MANIFEST_REQUIRED
    try:
        manifest_capability = getattr(manifest, "capability_id", _MISSING)
        availability = getattr(manifest, "availability", _MISSING)
        enabled = getattr(manifest, "enabled", _MISSING)
        if manifest_capability != capability_id:
            return FeatureFlagReason.MANIFEST_NOT_NATIVE
        if availability != "session_native":
            return FeatureFlagReason.MANIFEST_NOT_NATIVE
        if enabled is not _MISSING and enabled is not True:
            return FeatureFlagReason.MANIFEST_NOT_NATIVE
    except Exception:
        return FeatureFlagReason.MANIFEST_NOT_NATIVE
    return FeatureFlagReason.ENABLED


def evaluate_feature_flag(
    capability_id: object,
    config: FeatureFlagConfig | None = None,
    manifest: object | None = None,
    wiring: RuntimeWiringEvidence | None = None,
    scope: UserSessionScopeEvidence | None = None,
) -> FeatureFlagDecision:
    """按固定顺序执行 fail-closed 判定，不产生外部副作用。"""

    capability = _normalise_capability(capability_id)
    if capability is None:
        return FeatureFlagDecision(False, FeatureFlagReason.INVALID_CAPABILITY.value)
    if config is None:
        return FeatureFlagDecision(False, FeatureFlagReason.DEFAULT_DISABLED.value, capability)
    try:
        config_valid = isinstance(config, FeatureFlagConfig) and config.valid
    except Exception:
        config_valid = False
    if not config_valid:
        return FeatureFlagDecision(False, FeatureFlagReason.INVALID_CONFIG.value, capability)
    try:
        global_enabled = config.global_enabled
        allowlisted = capability in config.capability_allowlist
    except Exception:
        return FeatureFlagDecision(False, FeatureFlagReason.INVALID_CONFIG.value, capability)
    if global_enabled is not True:
        return FeatureFlagDecision(False, FeatureFlagReason.DEFAULT_DISABLED.value, capability)
    if not allowlisted:
        return FeatureFlagDecision(
            False,
            FeatureFlagReason.CAPABILITY_NOT_ALLOWLISTED.value,
            capability,
        )

    manifest_result = _manifest_reason(manifest, capability)
    if manifest_result is not FeatureFlagReason.ENABLED:
        return FeatureFlagDecision(False, manifest_result.value, capability)

    if wiring is None:
        return FeatureFlagDecision(False, FeatureFlagReason.WIRING_REQUIRED.value, capability)
    try:
        wiring_valid = (
            isinstance(wiring, RuntimeWiringEvidence)
            and wiring.valid
            and wiring.capability_id == capability
            and wiring.proven is True
        )
    except Exception:
        wiring_valid = False
    if not wiring_valid:
        return FeatureFlagDecision(False, FeatureFlagReason.WIRING_NOT_PROVEN.value, capability)

    if scope is None:
        return FeatureFlagDecision(False, FeatureFlagReason.SCOPE_REQUIRED.value, capability)
    try:
        scope_valid = (
            isinstance(scope, UserSessionScopeEvidence)
            and scope.valid
            and scope.capability_id == capability
            and scope.in_scope is True
        )
    except Exception:
        scope_valid = False
    if not scope_valid:
        return FeatureFlagDecision(False, FeatureFlagReason.SCOPE_NOT_PROVEN.value, capability)

    return FeatureFlagDecision(True, FeatureFlagReason.ENABLED.value, capability)


def decide_feature_flag(
    capability_id: object,
    config: FeatureFlagConfig | None = None,
    manifest: object | None = None,
    wiring: RuntimeWiringEvidence | None = None,
    scope: UserSessionScopeEvidence | None = None,
) -> FeatureFlagDecision:
    """``evaluate_feature_flag`` 的语义别名，便于接线层表达决策动作。"""

    return evaluate_feature_flag(capability_id, config, manifest, wiring, scope)


def is_session_native_enabled(
    capability_id: object,
    config: FeatureFlagConfig | None = None,
    manifest: object | None = None,
    wiring: RuntimeWiringEvidence | None = None,
    scope: UserSessionScopeEvidence | None = None,
) -> bool:
    """布尔便捷接口；缺少显式证据时默认 ``False``，绝不隐式读取外部状态。"""

    return evaluate_feature_flag(capability_id, config, manifest, wiring, scope).enabled


__all__ = [
    "CurrentUserSessionEvidence",
    "FETCH_PDB_INFO",
    "FeatureFlagConfig",
    "FeatureFlagDecision",
    "FeatureFlagReason",
    "FeatureFlagResult",
    "RuntimeFeatureConfig",
    "RuntimeWiringEvidence",
    "RuntimeWiringProof",
    "CurrentUserSessionScope",
    "ScopeEvidence",
    "SUPPORTED_CAPABILITIES",
    "UserSessionScopeEvidence",
    "decide_feature_flag",
    "evaluate_feature_flag",
    "is_session_native_enabled",
]
