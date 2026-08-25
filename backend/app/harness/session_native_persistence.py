"""真实 SQLAlchemy Session 的 Session-native 持久化端口。

wzf：端口只接收 executor 生成的有限实体和 AptamerRuntimeAdapter 的
OutboxEvent。它在调用方已有事务中按 FK 顺序 ``add``/``flush``，从不 commit；
冲突或异常会 rollback 并 fail-closed。默认 flag 关闭时上层不会产生 intents，
本端口也不会替普通旧 Repository 接管未知实体。
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from app.harness.executor import SessionNativeIntent
from app.harness.outbox import OutboxEvent


_ALLOWED_ORDER = (
    "ResearchSession",
    "ResearchGoal",
    "SessionGoalLink",
    "ScientificTarget",
    "SkillExecution",
    "TaskGraph",
    "ScientificTask",
    "ExecutionAttempt",
    "TaskDependency",
    "WorkflowCheckpoint",
)
_ALLOWED = frozenset(_ALLOWED_ORDER)
_UUID_FIELDS = frozenset(
    {
        "id",
        "user_id",
        "session_id",
        "goal_id",
        "legacy_research_run_id",
        "skill_execution_id",
        "task_graph_id",
        "scientific_task_id",
        "upstream_task_id",
        "downstream_task_id",
        "parent_attempt_id",
        "aggregate_id",
    }
)


class SessionNativePersistenceError(RuntimeError):
    """Session-native 批次不能安全写入时抛出的异常。"""


def _uuid(value: Any) -> UUID | Any:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return value


def _text(value: Any) -> str:
    text = str(value).strip()
    if not text:
        raise SessionNativePersistenceError("scope identity must be non-empty")
    return text


def _canonical(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): _canonical(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def _same(left: Any, right: Any) -> bool:
    return _canonical(left) == _canonical(right)


def _plain(value: Any) -> Any:
    """把 SessionNativeIntent 的 mappingproxy/tuple 解冻为 JSON 可写值。"""

    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, datetime):
        return value
    return value


def _models() -> dict[str, Any]:
    from app.db import harness_models

    return {
        name: getattr(harness_models, name)
        for name in (*_ALLOWED_ORDER, "HarnessOutboxEvent")
    }


class SessionNativePersistencePort:
    """把一批 native intents 和一个 Outbox 事件写入同一外层事务。

    ``session`` 必须是 HarnessUnitOfWork 正在使用的 SQLAlchemy Session；端口
    不创建 Session、不提交事务。``user_id``/``session_id`` 是可信作用域，所有
    intent 只允许写入该作用域内的事实。
    """

    def __init__(
        self,
        session: Any,
        *,
        user_id: UUID | str,
        session_id: UUID | str,
        model_registry: Mapping[str, Any] | None = None,
    ) -> None:
        if session is None:
            raise SessionNativePersistenceError("session is required")
        self.session = session
        self.user_id = _uuid(_text(user_id))
        self.session_id = _uuid(_text(session_id))
        self.models = dict(model_registry or _models())
        missing = [name for name in (*_ALLOWED_ORDER, "HarnessOutboxEvent") if name not in self.models]
        if missing:
            raise SessionNativePersistenceError("model registry missing: " + ", ".join(missing))

    def _query_by_idempotency(self, model: Any, key: str) -> Any | None:
        if not hasattr(model, "idempotency_key"):
            return None
        try:
            from sqlalchemy import select

            return self.session.scalars(
                select(model).where(getattr(model, "idempotency_key") == key)
            ).first()
        except Exception:
            return None

    def _query_by_natural_key(
        self, entity: str, model: Any, values: Mapping[str, Any]
    ) -> Any | None:
        fields = {
            "SessionGoalLink": ("session_id", "goal_id"),
            "ScientificTarget": ("goal_id", "identity_hash"),
        }.get(entity)
        if fields is None or any(field not in values or not hasattr(model, field) for field in fields):
            return None
        try:
            from sqlalchemy import select

            predicates = [
                getattr(model, field) == (_uuid(values[field]) if field in _UUID_FIELDS else values[field])
                for field in fields
            ]
            return self.session.scalars(select(model).where(*predicates)).first()
        except Exception:
            return None

    def _assert_scope(self, entity: str, values: Mapping[str, Any]) -> None:
        if "user_id" in values and not _same(values["user_id"], self.user_id):
            raise SessionNativePersistenceError(f"user scope mismatch: {entity}")
        if entity in {"ResearchSession", "SessionGoalLink", "TaskGraph"}:
            if entity == "ResearchSession":
                if not _same(values.get("id"), self.session_id):
                    raise SessionNativePersistenceError("ResearchSession session scope mismatch")
            elif not _same(values.get("session_id"), self.session_id):
                raise SessionNativePersistenceError(f"session scope mismatch: {entity}")

    def _assert_existing(self, entity: str, row: Any, values: Mapping[str, Any]) -> None:
        for key, expected in values.items():
            if key in {"created_at", "updated_at", "pending_at", "available_at"}:
                continue
            if not hasattr(row, key):
                continue
            actual = getattr(row, key)
            if not _same(actual, expected):
                raise SessionNativePersistenceError(
                    f"identity/payload conflict: {entity}.{key}"
                )

    def _values_for_model(self, model: Any, values: Mapping[str, Any]) -> dict[str, Any]:
        mapper = getattr(model, "__mapper__", None)
        fields = {item.key for item in mapper.attrs} if mapper is not None else set(values)
        result: dict[str, Any] = {}
        for key, value in values.items():
            if key not in fields:
                continue
            result[key] = _uuid(value) if key in _UUID_FIELDS else _plain(value)
        return result

    def _ensure_entity(self, intent: SessionNativeIntent) -> tuple[Any, bool]:
        if intent.entity not in _ALLOWED:
            raise SessionNativePersistenceError(f"unsupported native entity: {intent.entity}")
        values = dict(intent.values)
        self._assert_scope(intent.entity, values)
        model = self.models[intent.entity]
        entity_id = _uuid(intent.entity_id)
        row = self.session.get(model, entity_id)
        if row is None:
            row = self._query_by_natural_key(intent.entity, model, values)
        if row is None:
            row = self._query_by_idempotency(model, intent.idempotency_key)
            if row is not None:
                raise SessionNativePersistenceError(
                    f"idempotency key maps to another {intent.entity} id"
                )
        if row is not None:
            self._assert_existing(intent.entity, row, values)
            return row, False
        payload = self._values_for_model(model, values)
        if "id" in payload and not _same(payload["id"], entity_id):
            raise SessionNativePersistenceError(f"entity id mismatch: {intent.entity}")
        row = model(**payload)
        self.session.add(row)
        return row, True

    def _validate_batch(self, intents: tuple[SessionNativeIntent, ...]) -> None:
        if not isinstance(intents, tuple):
            raise SessionNativePersistenceError("intents must be a tuple")
        seen: dict[tuple[str, str], SessionNativeIntent] = {}
        for intent in intents:
            if not isinstance(intent, SessionNativeIntent):
                raise SessionNativePersistenceError("unsupported intent type")
            key = (intent.entity, intent.entity_id)
            previous = seen.get(key)
            if previous is not None and not _same(previous.values, intent.values):
                raise SessionNativePersistenceError(f"duplicate intent conflict: {intent.entity}")
            seen[key] = intent
            if intent.operation not in {"ensure", "reuse"}:
                raise SessionNativePersistenceError("only ensure/reuse operations are supported")
        # executor 会把每个 ScientificTask 紧跟其 ExecutionAttempt；因此不能
        # 用实体类型的全局单调排序误判合法的 Task/Attempt 交错顺序。这里只核对
        # 每个批内 FK 引用是否已在前面出现，或已存在于数据库。
        prior: set[tuple[str, str]] = set()
        references = {
            "SessionGoalLink": (("session_id", "ResearchSession"), ("goal_id", "ResearchGoal")),
            "ScientificTarget": (("goal_id", "ResearchGoal"),),
            "SkillExecution": (("goal_id", "ResearchGoal"),),
            "TaskGraph": (
                ("session_id", "ResearchSession"),
                ("goal_id", "ResearchGoal"),
                ("skill_execution_id", "SkillExecution"),
            ),
            "ScientificTask": (
                ("task_graph_id", "TaskGraph"),
                ("skill_execution_id", "SkillExecution"),
            ),
            "ExecutionAttempt": (("scientific_task_id", "ScientificTask"),),
            "TaskDependency": (
                ("task_graph_id", "TaskGraph"),
                ("skill_execution_id", "SkillExecution"),
                ("upstream_task_id", "ScientificTask"),
                ("downstream_task_id", "ScientificTask"),
            ),
            "WorkflowCheckpoint": (("skill_execution_id", "SkillExecution"),),
        }
        for intent in intents:
            for field_name, entity in references.get(intent.entity, ()):
                value = intent.values.get(field_name)
                if value is None:
                    continue
                key = (entity, str(value))
                if key in prior:
                    continue
                model = self.models[entity]
                if self.session.get(model, _uuid(value)) is None:
                    raise SessionNativePersistenceError(
                        f"foreign-key order or missing reference: {intent.entity}.{field_name}"
                    )
            prior.add((intent.entity, intent.entity_id))

    def _outbox_values(self, event: OutboxEvent) -> dict[str, Any]:
        payload = _plain(event.payload)
        if "user_id" in payload and not _same(payload["user_id"], self.user_id):
            raise SessionNativePersistenceError("Outbox user scope mismatch")
        if "session_id" in payload and not _same(payload["session_id"], self.session_id):
            raise SessionNativePersistenceError("Outbox session scope mismatch")
        status = {
            "waiting_dependency": "waiting_for_dependency",
            "waiting_resource": "waiting_for_resource",
        }.get(event.status, event.status)
        waiting_reason = event.waiting_reason
        if status == "waiting_for_dependency":
            waiting_reason = "dependency"
        elif status == "waiting_for_resource":
            waiting_reason = "resource"
        return {
            "id": uuid5(NAMESPACE_URL, f"harness-outbox:{event.dedupe_key}"),
            "dedupe_key": event.dedupe_key,
            "event_type": event.event_type,
            "aggregate_type": event.aggregate_type,
            "aggregate_id": _uuid(event.aggregate_id),
            "payload_json": payload,
            "status": status,
            "pending_at": event.pending_at,
            "available_at": event.available_at,
            "published_at": event.published_at,
            "attempt_count": event.attempt_count,
            "last_error": event.last_error,
            "waiting_reason": waiting_reason,
            "claim_token": event.claim_token,
            "lease_owner": event.lease_owner,
            "lease_expires_at": event.lease_expires_at,
        }

    def _ensure_outbox(self, event: OutboxEvent) -> tuple[Any, bool]:
        model = self.models["HarnessOutboxEvent"]
        values = self._outbox_values(event)
        from sqlalchemy import select

        row = self.session.scalars(
            select(model).where(getattr(model, "dedupe_key") == event.dedupe_key)
        ).first()
        if row is None:
            row = self.session.get(model, values["id"])
        if row is not None:
            self._assert_existing("HarnessOutboxEvent", row, values)
            return row, False
        row = model(**self._values_for_model(model, values))
        self.session.add(row)
        return row, True

    def persist_session_native_atomically(
        self,
        intents: tuple[SessionNativeIntent, ...],
        outbox_event: OutboxEvent,
    ) -> dict[str, Any]:
        """按依赖顺序 ensure intents/outbox；不 commit 外层 Session。"""

        if not isinstance(outbox_event, OutboxEvent):
            raise SessionNativePersistenceError("outbox_event must be OutboxEvent")
        try:
            self._validate_batch(intents)
            created = 0
            reused = 0
            entity_order: list[str] = []
            for intent in intents:
                _row, is_new = self._ensure_entity(intent)
                entity_order.append(intent.entity)
                created += int(is_new)
                reused += int(not is_new)
            _event_row, event_created = self._ensure_outbox(outbox_event)
            self.session.flush()
            return {
                "accepted": True,
                "created_count": created,
                "reused_count": reused,
                "outbox_created": event_created,
                "outbox_reused": not event_created,
                "entity_order": entity_order,
                "committed": False,
            }
        except SessionNativePersistenceError:
            self.session.rollback()
            raise
        except Exception as exc:
            self.session.rollback()
            raise SessionNativePersistenceError(
                f"session-native persistence failed: {type(exc).__name__}: {exc}"
            ) from exc


__all__ = ["SessionNativePersistenceError", "SessionNativePersistencePort"]