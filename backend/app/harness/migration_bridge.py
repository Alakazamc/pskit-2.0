"""Agent 与 Harness 迁移期间的受控运行时桥接。

wzf：普通对话继续使用旧 Agent/ToolRunner；本模块只生成服务端关联与最小
dispatch 摘要，并提供一个默认关闭的 Session-native executor 注入点。它不读取
客户端 native 声称，不连接生产数据库、Worker、MCP 或旧 ResearchRun。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from app.harness.runtime_wiring import (
    LEGACY_ROUTE,
    NATIVE_ROUTE,
    RuntimeWiringContext,
    build_persisted_dispatch,
    stable_correlation_id,
)

DISPATCH_METADATA_KEY = "runtime_dispatch"
DISPATCH_SCHEMA_VERSION = "agent-dispatch-v1"


def _text(value: object, field_name: str, *, maximum: int = 240) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or len(text) > maximum:
        return None
    return text


def _same(left: object, right: object) -> bool:
    left_text = _text(left, "id")
    right_text = _text(right, "id")
    return left_text is not None and left_text == right_text


@dataclass(frozen=True, slots=True)
class AgentDispatchSummary:
    """由服务端创建、可安全持久化并可在 Agent 重启后恢复的摘要。"""

    correlation_id: str
    user_id: str
    session_id: str
    turn_id: str
    route: str = LEGACY_ROUTE
    capability_id: str | None = None
    server_created: bool = True
    schema_version: str = DISPATCH_SCHEMA_VERSION

    def __post_init__(self) -> None:
        values = {
            "correlation_id": _text(self.correlation_id, "correlation_id"),
            "user_id": _text(self.user_id, "user_id"),
            "session_id": _text(self.session_id, "session_id"),
            "turn_id": _text(self.turn_id, "turn_id"),
        }
        if any(value is None for value in values.values()):
            raise ValueError("dispatch summary identity is incomplete")
        if self.route not in {LEGACY_ROUTE, NATIVE_ROUTE}:
            raise ValueError("dispatch summary route is invalid")
        if self.capability_id is not None:
            capability = _text(self.capability_id, "capability_id")
            if capability is None:
                raise ValueError("dispatch summary capability is invalid")
            object.__setattr__(self, "capability_id", capability)
        if self.schema_version != DISPATCH_SCHEMA_VERSION:
            raise ValueError("dispatch summary schema is invalid")
        if type(self.server_created) is not bool:
            raise TypeError("server_created must be a bool")
        for field_name, value in values.items():
            object.__setattr__(self, field_name, value)

    @property
    def valid(self) -> bool:
        return (
            self.server_created is True
            and self.schema_version == DISPATCH_SCHEMA_VERSION
            and self.route in {LEGACY_ROUTE, NATIVE_ROUTE}
            and all(
                _text(getattr(self, field_name), field_name) is not None
                for field_name in ("correlation_id", "user_id", "session_id", "turn_id")
            )
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "server_created": self.server_created,
            "correlation_id": self.correlation_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "route": self.route,
        }
        if self.capability_id is not None:
            result["capability_id"] = self.capability_id
        return result


def new_agent_dispatch_summary(
    *,
    user_id: object,
    session_id: object,
    turn_id: object,
    correlation_id: object | None = None,
) -> AgentDispatchSummary:
    """创建不依赖 ResearchRun 的服务端关联摘要。"""

    turn_text = _text(turn_id, "turn_id")
    if turn_text is None:
        raise ValueError("turn_id is required")
    correlation = _text(correlation_id, "correlation_id")
    if correlation is None:
        correlation = stable_correlation_id(
            invocation_id=turn_text,
            capability_id="agent-turn",
        )
    return AgentDispatchSummary(
        correlation_id=correlation,
        user_id=str(user_id),
        session_id=str(session_id),
        turn_id=turn_text,
    )


create_agent_dispatch_summary = new_agent_dispatch_summary


def _summary_from_mapping(value: Mapping[str, Any]) -> AgentDispatchSummary | None:
    try:
        if value.get("schema_version") != DISPATCH_SCHEMA_VERSION:
            return None
        if value.get("server_created") is not True:
            return None
        return AgentDispatchSummary(
            correlation_id=value.get("correlation_id"),
            user_id=value.get("user_id"),
            session_id=value.get("session_id"),
            turn_id=value.get("turn_id"),
            route=value.get("route", LEGACY_ROUTE),
            capability_id=value.get("capability_id"),
            server_created=value.get("server_created"),
            schema_version=value.get("schema_version"),
        )
    except (TypeError, ValueError, KeyError):
        return None


def load_dispatch_summary(
    metadata: Mapping[str, Any] | None,
    *,
    user_id: object,
    session_id: object,
    turn_id: object,
) -> AgentDispatchSummary | None:
    """从服务端消息 metadata 恢复摘要；不接受不匹配或伪造的输入。"""

    if not isinstance(metadata, Mapping):
        return None
    value: object = metadata.get(DISPATCH_METADATA_KEY)
    if not isinstance(value, Mapping):
        return None
    summary = _summary_from_mapping(value)
    if summary is None:
        return None
    if not (
        _same(summary.user_id, user_id)
        and _same(summary.session_id, session_id)
        and _same(summary.turn_id, turn_id)
    ):
        return None
    return summary if summary.valid else None


def build_native_dispatch(
    summary: AgentDispatchSummary | None,
    capability_id: object,
) -> dict[str, str] | None:
    """仅由已验证服务端 binding 生成 Worker 可核对的 native dispatch。"""

    if summary is None or not summary.valid:
        return None
    capability = _text(capability_id, "capability_id")
    if capability is None:
        return None
    try:
        return build_persisted_dispatch(
            capability_id=capability,
            user_id=summary.user_id,
            session_id=summary.session_id,
            turn_id=summary.turn_id,
            correlation_id=summary.correlation_id,
        )
    except (TypeError, ValueError):
        return None


AgentHarnessExecutor = Callable[..., Mapping[str, Any]]
AgentWiringContextFactory = Callable[..., RuntimeWiringContext | None]


@dataclass(frozen=True, slots=True)
class AgentRuntimeBinding:
    """当前 Agent 轮次实际可用的桥接绑定；默认 executor/context 均为空。"""

    summary: AgentDispatchSummary
    executor: AgentHarnessExecutor | None = None
    wiring_context: RuntimeWiringContext | None = None
    enabled: bool = False

    def dispatch_for(self, capability_id: object) -> dict[str, str] | None:
        if not self.enabled:
            return None
        return build_native_dispatch(self.summary, capability_id)


@dataclass(frozen=True, slots=True)
class AgentRuntimeBridge:
    """可注入但默认关闭的 Session-native 运行时桥。"""

    enabled: bool = False
    executor: AgentHarnessExecutor | None = None
    wiring_context_factory: AgentWiringContextFactory | None = None

    def bind(
        self,
        *,
        summary: AgentDispatchSummary,
        user_id: object,
        session_id: object,
        turn_id: object,
    ) -> AgentRuntimeBinding:
        disabled = AgentRuntimeBinding(summary=summary)
        if self.enabled is not True or not summary.valid:
            return disabled
        if not (
            _same(summary.user_id, user_id)
            and _same(summary.session_id, session_id)
            and _same(summary.turn_id, turn_id)
        ):
            return disabled
        executor = self.executor
        if not callable(executor):
            execute_method = getattr(executor, "execute", None)
            executor = execute_method if callable(execute_method) else None
        if executor is None or not callable(self.wiring_context_factory):
            return disabled
        try:
            context = self.wiring_context_factory(
                user_id=str(user_id),
                session_id=str(session_id),
                turn_id=str(turn_id),
                dispatch_summary=summary.to_dict(),
            )
        except Exception:
            return disabled
        if not isinstance(context, RuntimeWiringContext):
            return disabled
        return AgentRuntimeBinding(
            summary=summary,
            executor=executor,
            wiring_context=context,
            enabled=True,
        )


_DEFAULT_BRIDGE = AgentRuntimeBridge()
_active_bridge = _DEFAULT_BRIDGE


def get_agent_runtime_bridge() -> AgentRuntimeBridge:
    return _active_bridge


def install_agent_runtime_bridge(bridge: AgentRuntimeBridge | None) -> None:
    global _active_bridge
    _active_bridge = bridge if isinstance(bridge, AgentRuntimeBridge) else _DEFAULT_BRIDGE


get_runtime_bridge = get_agent_runtime_bridge
install_runtime_bridge = install_agent_runtime_bridge


@contextmanager
def installed_agent_runtime_bridge(bridge: AgentRuntimeBridge | None):
    previous = get_agent_runtime_bridge()
    install_agent_runtime_bridge(bridge)
    try:
        yield
    finally:
        install_agent_runtime_bridge(previous)


__all__ = [
    "AgentDispatchSummary",
    "AgentHarnessExecutor",
    "AgentRuntimeBinding",
    "AgentRuntimeBridge",
    "DISPATCH_METADATA_KEY",
    "DISPATCH_SCHEMA_VERSION",
    "build_native_dispatch",
    "create_agent_dispatch_summary",
    "get_agent_runtime_bridge",
    "get_runtime_bridge",
    "install_agent_runtime_bridge",
    "install_runtime_bridge",
    "installed_agent_runtime_bridge",
    "load_dispatch_summary",
    "new_agent_dispatch_summary",
]