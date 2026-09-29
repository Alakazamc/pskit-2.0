"""与 Web/SSE 解耦的不可变领域事件。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from types import MappingProxyType
from typing import Any, Mapping
from uuid import UUID, uuid4


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json_default(value: Any) -> str:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if hasattr(value, "value"):
        return str(value.value)
    raise TypeError(f"无法为事件计算规范 JSON：{type(value).__name__}")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
        allow_nan=False,
    )


def _freeze(value: Any) -> Any:
    """递归复制并冻结 JSON 兼容值，避免事件与调用方共享可变对象。"""

    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted((_freeze(item) for item in value), key=repr))
    if isinstance(value, (str, int, float, bool, type(None), UUID, datetime)):
        return value
    # 事件 payload 应是 JSON 结构；复制未知对象后由 canonical JSON 给出明确错误。
    return deepcopy(value)


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return deepcopy(value)


def build_dedupe_key(
    aggregate_type: str,
    aggregate_id: UUID | str,
    event_type: str,
    payload: Mapping[str, Any] | None = None,
    *,
    schema_version: str = "1",
) -> str:
    """根据领域身份和规范化 payload 生成稳定的去重键。

    键中不包含 ``event_id`` 或时间，因此同一事实重放时仍会得到同一结果；
    payload 的键顺序和 UUID/时间表示也不会影响结果。
    """

    normalized_aggregate_type = str(aggregate_type).strip()
    normalized_aggregate_id = str(aggregate_id).strip()
    normalized_event_type = str(event_type).strip()
    normalized_schema_version = str(schema_version).strip()
    if not normalized_aggregate_type:
        raise ValueError("aggregate_type 不能为空")
    if not normalized_aggregate_id:
        raise ValueError("aggregate_id 不能为空")
    if not normalized_event_type:
        raise ValueError("event_type 不能为空")
    if not normalized_schema_version:
        raise ValueError("schema_version 不能为空")
    if payload is not None and not isinstance(payload, Mapping):
        raise TypeError("payload 必须是映射")
    identity = {
        "aggregate_type": normalized_aggregate_type,
        "aggregate_id": normalized_aggregate_id,
        "event_type": normalized_event_type,
        "schema_version": normalized_schema_version,
        "payload": _thaw(payload) if payload is not None else {},
    }
    digest = hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()
    # HarnessOutboxEvent.dedupe_key 长度上限为 300；只持久化固定长度摘要，
    # 可读聚合身份仍保存在事件自身字段中。
    return f"harness-event:{digest}"


# 常见的旧名称保留为显式别名，避免调用方自行实现不一致的哈希格式。
stable_dedupe_key = build_dedupe_key
make_dedupe_key = build_dedupe_key
dedupe_key_for = build_dedupe_key


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """事务内可持久化的领域事件，不负责传输或 SSE 推送。"""

    aggregate_type: str
    aggregate_id: UUID | str
    event_type: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = "1"
    dedupe_key: str | None = None
    event_id: UUID = field(default_factory=uuid4)
    occurred_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not self.aggregate_type.strip():
            raise ValueError("aggregate_type 不能为空")
        if not str(self.aggregate_id).strip():
            raise ValueError("aggregate_id 不能为空")
        if not self.event_type.strip():
            raise ValueError("event_type 不能为空")
        if not self.schema_version.strip():
            raise ValueError("schema_version 不能为空")
        if self.dedupe_key is not None and not self.dedupe_key.strip():
            raise ValueError("dedupe_key 不能为空")
        if self.dedupe_key is not None and len(self.dedupe_key) > 300:
            raise ValueError("dedupe_key 不能超过 300 个字符")
        if not isinstance(self.payload, Mapping):
            raise TypeError("payload 必须是映射")
        object.__setattr__(self, "payload", _freeze(self.payload))
        if self.occurred_at.tzinfo is None:
            object.__setattr__(
                self,
                "occurred_at",
                self.occurred_at.replace(tzinfo=timezone.utc),
            )
        else:
            object.__setattr__(
                self,
                "occurred_at",
                self.occurred_at.astimezone(timezone.utc),
            )
        if self.dedupe_key is None:
            object.__setattr__(
                self,
                "dedupe_key",
                build_dedupe_key(
                    self.aggregate_type,
                    self.aggregate_id,
                    self.event_type,
                    self.payload,
                    schema_version=self.schema_version,
                ),
            )

    def payload_copy(self) -> dict[str, Any]:
        """返回可安全修改的 payload 副本。"""

        return _thaw(self.payload)

    def as_dict(self) -> dict[str, Any]:
        """返回适合 JSON 编码的独立字典。"""

        return {
            "event_id": str(self.event_id),
            "aggregate_type": self.aggregate_type,
            "aggregate_id": str(self.aggregate_id),
            "event_type": self.event_type,
            "occurred_at": self.occurred_at.isoformat(),
            "payload": self.payload_copy(),
            "schema_version": self.schema_version,
            "dedupe_key": self.dedupe_key,
        }

    to_dict = as_dict
