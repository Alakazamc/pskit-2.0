"""Agent/API/Worker 的受控运行时接线。

这个模块只负责判定一次调用是否具备进入 Session-native 旁路的资格，
不执行科学工具，也不把客户端声称的 ``native`` 当作事实。旧 ToolRunner
路径继续作为默认回退路径；只有服务器端 feature flag、能力清单和持久化
调度意图同时满足时，调用方才可以把旁路标记为 ``session_native``。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
from typing import Any
from uuid import UUID, uuid4


FETCH_PDB_INFO = "fetch_pdb_info"
NATIVE_ROUTE = "session_native"
LEGACY_ROUTE = "legacy_bridge"
_NATIVE_CANDIDATES = frozenset({FETCH_PDB_INFO})


def _clean(value: object, field_name: str, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ValueError(f"{field_name} is required")
        return None
    text = str(value).strip()
    if not text and required:
        raise ValueError(f"{field_name} is required")
    return text or None


def _is_uuid_like(value: str) -> bool:
    try:
        UUID(value)
    except (TypeError, ValueError, AttributeError):
        return False
    return True


@dataclass(frozen=True, slots=True)
class RuntimeWiringDecision:
    """服务端对一次调用路线的不可变判定。"""

    allowed: bool
    route: str
    capability_id: str
    correlation_id: str
    reason: str
    server_verified: bool = False

    def __post_init__(self) -> None:
        if self.route not in {NATIVE_ROUTE, LEGACY_ROUTE}:
            raise ValueError("route must be session_native or legacy_bridge")
        if not isinstance(self.allowed, bool) or not isinstance(self.server_verified, bool):
            raise TypeError("allowed and server_verified must be bool")
        if not self.capability_id.strip() or not self.correlation_id.strip():
            raise ValueError("capability_id and correlation_id must be non-empty")
        if not self.reason.strip():
            raise ValueError("reason must be non-empty")

    @property
    def native(self) -> bool:
        return self.allowed and self.route == NATIVE_ROUTE and self.server_verified

    def to_public_dict(self) -> dict[str, Any]:
        """返回可供 SSE/API 使用的摘要，不包含凭证、路径或内部异常。"""

        return {
            "allowed": self.allowed,
            "route": self.route,
            "capability_id": self.capability_id,
            "correlation_id": self.correlation_id,
            "reason": self.reason,
            "server_verified": self.server_verified,
        }


def new_correlation_id() -> str:
    """由服务端生成关联 ID；不采用客户端的自由文本作为身份。"""

    return str(uuid4())


def stable_correlation_id(*, invocation_id: object, capability_id: str) -> str:
    """为已持久化 Invocation 生成稳定的跨 Agent/API/Worker 关联 ID。"""

    raw = f"{_clean(invocation_id, 'invocation_id', required=True)}\0{capability_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class RuntimeWiringContext:
    """由受控启动层注入的一组不可变服务端证据。

    本对象不从环境、请求体或数据库自行拼装授权。生产启动层必须先解析配置、
    校验当前 user/session 归属并取得接线证明，再显式构造此对象。
    """

    feature_config: object
    manifest: object
    wiring_evidence: object
    scope_evidence: object


def _feature_flag_decision(capability_id: str, context: RuntimeWiringContext | None):
    """调用纯 feature flag 判定；模块或证据异常一律返回 ``None``。"""

    if not isinstance(context, RuntimeWiringContext):
        return None
    try:
        from app.harness.feature_flags import evaluate_feature_flag

        return evaluate_feature_flag(
            capability_id,
            context.feature_config,
            context.manifest,
            context.wiring_evidence,
            context.scope_evidence,
        )
    except Exception:
        return None


def _client_claimed_native(claims: Mapping[str, Any] | None) -> bool:
    if not isinstance(claims, Mapping):
        return False
    for key in ("native", "session_native", "availability", "route"):
        value = claims.get(key)
        if key in {"native", "session_native"} and value is True:
            return True
        if key == "availability" and str(value).strip() == NATIVE_ROUTE:
            return True
        if key == "route" and str(value).strip() == NATIVE_ROUTE:
            return True
    return False


def resolve_runtime_wiring(
    capability_id: str,
    *,
    user_id: object,
    session_id: object,
    turn_id: object | None = None,
    correlation_id: object | None = None,
    client_claims: Mapping[str, Any] | None = None,
    persisted_dispatch: Mapping[str, Any] | None = None,
    context: RuntimeWiringContext | None = None,
    require_persisted_dispatch: bool = False,
) -> RuntimeWiringDecision:
    """在 Agent/API/Worker 三处复用的资格判定。

    缺少任一服务端证明时返回旧路线。只有 Worker 收到完整、已持久化且
    身份一致的 dispatch 元数据时，才会返回 ``allowed=True`` 的 native 路线。
    """

    capability = _clean(capability_id, "capability_id", required=True) or ""
    user = _clean(user_id, "user_id")
    session = _clean(session_id, "session_id")
    turn = _clean(turn_id, "turn_id")
    correlation = _clean(correlation_id, "correlation_id") or new_correlation_id()
    base = dict(
        allowed=False,
        route=LEGACY_ROUTE,
        capability_id=capability,
        correlation_id=correlation,
        server_verified=False,
    )

    if not user or not session:
        return RuntimeWiringDecision(reason="missing server session ownership", **base)
    if _client_claimed_native(client_claims):
        return RuntimeWiringDecision(reason="client native claim ignored", **base)
    if capability not in _NATIVE_CANDIDATES:
        return RuntimeWiringDecision(reason="capability is not in the native allowlist", **base)
    flag = _feature_flag_decision(capability, context)
    if flag is None:
        return RuntimeWiringDecision(reason="session-native server evidence is unavailable", **base)
    if not bool(getattr(flag, "enabled", False)):
        reason = str(getattr(flag, "reason", "invalid_config"))
        return RuntimeWiringDecision(reason=f"session-native denied: {reason}", **base)

    # feature flag 已校验 capability；这里额外绑定本次解析使用的 user/session，
    # 防止拿另一个会话的合法 scope evidence 重放。
    try:
        scope = context.scope_evidence if context is not None else None
        if str(scope.user_id) != user or str(scope.session_id) != session:
            return RuntimeWiringDecision(reason="server scope evidence mismatch", **base)
    except Exception:
        return RuntimeWiringDecision(reason="server scope evidence mismatch", **base)

    if require_persisted_dispatch and persisted_dispatch is None:
        return RuntimeWiringDecision(reason="persisted dispatch intent is required", **base)
    if persisted_dispatch is not None and not isinstance(persisted_dispatch, Mapping):
        return RuntimeWiringDecision(reason="persisted dispatch intent is invalid", **base)
    if persisted_dispatch is not None:
        expected = {
            "route": NATIVE_ROUTE,
            "capability_id": capability,
            "user_id": user,
            "session_id": session,
            "correlation_id": correlation,
        }
        for key, value in expected.items():
            if str(persisted_dispatch.get(key) or "").strip() != value:
                return RuntimeWiringDecision(reason=f"persisted dispatch {key} mismatch", **base)
        if turn is not None and str(persisted_dispatch.get("turn_id") or "").strip() != turn:
            return RuntimeWiringDecision(reason="persisted dispatch turn_id mismatch", **base)

    return RuntimeWiringDecision(
        allowed=True,
        route=NATIVE_ROUTE,
        capability_id=capability,
        correlation_id=correlation,
        reason="server native route verified",
        server_verified=True,
    )


def build_persisted_dispatch(
    *,
    capability_id: str,
    user_id: object,
    session_id: object,
    correlation_id: object,
    turn_id: object | None = None,
) -> dict[str, str]:
    """生成可随 Task/Intent 落库的最小调度摘要。"""

    result = {
        "route": NATIVE_ROUTE,
        "capability_id": _clean(capability_id, "capability_id", required=True) or "",
        "user_id": _clean(user_id, "user_id", required=True) or "",
        "session_id": _clean(session_id, "session_id", required=True) or "",
        "correlation_id": _clean(correlation_id, "correlation_id", required=True) or "",
    }
    if turn_id is not None:
        result["turn_id"] = _clean(turn_id, "turn_id", required=True) or ""
    return result


def validate_persisted_dispatch(
    *,
    capability_id: str,
    user_id: object,
    session_id: object | None,
    persisted_dispatch: Mapping[str, Any] | None,
    context: RuntimeWiringContext | None = None,
) -> RuntimeWiringDecision:
    """Worker 入口校验已落库意图；缺失/篡改不得以内存状态代替。"""

    return resolve_runtime_wiring(
        capability_id,
        user_id=user_id,
        session_id=session_id,
        correlation_id=(persisted_dispatch or {}).get("correlation_id")
        if isinstance(persisted_dispatch, Mapping)
        else None,
        persisted_dispatch=persisted_dispatch,
        context=context,
        require_persisted_dispatch=True,
    )


__all__ = [
    "FETCH_PDB_INFO",
    "LEGACY_ROUTE",
    "NATIVE_ROUTE",
    "RuntimeWiringDecision",
    "RuntimeWiringContext",
    "build_persisted_dispatch",
    "new_correlation_id",
    "resolve_runtime_wiring",
    "stable_correlation_id",
    "validate_persisted_dispatch",
]
