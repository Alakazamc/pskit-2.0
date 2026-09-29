"""事务 Outbox 的持久端口、租约 fencing 与可重投 dispatcher。

本模块刻意不依赖 SQLAlchemy，也不在进程内保存事实。``OutboxStore`` 是跨进程
持久实现必须满足的端口；``InMemoryOutboxStore`` 仅用于契约测试和本地设计验证，
不能替代第二波数据库迁移。生产实现必须把 claim token、owner、lease expiry 和
attempt_count 原子地写回数据库。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from threading import RLock
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol
from uuid import uuid4


OUTBOX_READY_STATUSES = frozenset(
    {
        "pending",
        "failed",
        "waiting_dependency",
        "waiting_resource",
        # 对齐 Harness 的任务状态命名；旧别名保留用于兼容已写入记录。
        "waiting_for_dependency",
        "waiting_for_resource",
    }
)
OUTBOX_TERMINAL_STATUSES = frozenset({"published"})
# ``dead_letter`` 仅为兼容既有持久化记录而保留；本模块的 dispatcher 不会自动写入该状态。
OUTBOX_COMPAT_STATUSES = frozenset({"dead_letter"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        return _utc(value).isoformat()
    return str(value)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _payload_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        _jsonable(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _same_event(left: "OutboxEvent", right: "OutboxEvent") -> bool:
    """比较事件语义而非租约/时间字段，供双向幂等约束复用。"""

    return (
        left.dedupe_key == right.dedupe_key
        and left.event_type == right.event_type
        and left.aggregate_type == right.aggregate_type
        and left.aggregate_id == right.aggregate_id
        and _payload_hash(left.payload) == _payload_hash(right.payload)
    )


class OutboxError(RuntimeError):
    """Outbox 端口错误基类。"""


class OutboxFencedError(OutboxError):
    """调用方使用了已过期或已被替换的 claim token。"""


class OutboxDedupeConflict(OutboxError):
    """同一 dedupe key 试图代表不同事件。"""


class OutboxNotFound(OutboxError):
    """事件不存在。"""


class AlreadyPublished(OutboxError):
    """发布端确认事件已处理，可安全视为 ack。"""


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    """事务内写入的事件事实及其 dispatcher 租约状态。

    ``claim_token``、``lease_owner``、``lease_expires_at`` 是生产持久化必须具备的
    fencing 字段；当前 ORM 表与 0004 migration 已显式包含它们，并通过跨字段约束
    保证租约状态和发布状态一致。
    """

    id: str
    dedupe_key: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    payload: Mapping[str, Any]
    status: str = "pending"
    pending_at: datetime = field(default_factory=_now)
    available_at: datetime = field(default_factory=_now)
    attempt_count: int = 0
    claim_token: str | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    published_at: datetime | None = None
    last_error: str | None = None
    waiting_reason: str | None = None

    @property
    def lease_token(self) -> str | None:
        """兼容持久化适配器常用命名；值与 ``claim_token`` 始终相同。"""

        return self.claim_token

    @property
    def fencing_token(self) -> str | None:
        """为需要显式 fencing 语义的调用方提供只读别名。"""

        return self.claim_token

    def __post_init__(self) -> None:
        for name in ("id", "dedupe_key", "event_type", "aggregate_type", "aggregate_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if len(self.dedupe_key) > 300:
            raise ValueError("dedupe_key must be at most 300 characters")
        if (
            self.status
            not in OUTBOX_READY_STATUSES | {"publishing", "published"} | OUTBOX_COMPAT_STATUSES
        ):
            raise ValueError(f"unknown outbox status: {self.status}")
        if self.attempt_count < 0:
            raise ValueError("attempt_count must be non-negative")
        if not isinstance(self.payload, Mapping):
            raise TypeError("payload must be a mapping")
        fencing_values = (self.claim_token, self.lease_owner, self.lease_expires_at)
        fencing_present = any(value is not None for value in fencing_values)
        if fencing_present and not all(value is not None for value in fencing_values):
            raise ValueError(
                "claim_token, lease_owner and lease_expires_at must be all set or all empty"
            )
        if self.status == "publishing" and not fencing_present:
            raise ValueError("publishing events must carry a complete fencing lease")
        if self.status != "publishing" and fencing_present:
            raise ValueError("only publishing events may carry a fencing lease")
        for name, value in (
            ("claim_token", self.claim_token),
            ("lease_owner", self.lease_owner),
        ):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be a non-empty string when present")
        for name, value in (
            ("lease_expires_at", self.lease_expires_at),
            ("published_at", self.published_at),
        ):
            if value is not None and not isinstance(value, datetime):
                raise TypeError(f"{name} must be a datetime when present")
        expected_waiting_reason = {
            "waiting_for_dependency": "dependency",
            "waiting_for_resource": "resource",
        }.get(self.status)
        if expected_waiting_reason is not None and self.waiting_reason != expected_waiting_reason:
            raise ValueError(
                f"{self.status} events must use waiting_reason={expected_waiting_reason!r}"
            )
        if expected_waiting_reason is None and self.waiting_reason is not None:
            raise ValueError("waiting_reason is only valid for canonical waiting statuses")
        if (self.status == "published") != (self.published_at is not None):
            raise ValueError("published status and published_at must be set together")
        object.__setattr__(self, "payload", _freeze(self.payload))
        object.__setattr__(self, "pending_at", _utc(self.pending_at))
        object.__setattr__(self, "available_at", _utc(self.available_at))
        if self.lease_expires_at is not None:
            object.__setattr__(self, "lease_expires_at", _utc(self.lease_expires_at))
        if self.published_at is not None:
            object.__setattr__(self, "published_at", _utc(self.published_at))


# 兼容称呼：应用层可以把它当作消息/记录使用，但只有一个事实对象。
OutboxMessage = OutboxEvent
OutboxRecord = OutboxEvent


class OutboxStore(Protocol):
    """跨进程持久化端口。

    ``claim`` 必须在数据库事务中以行锁或等价 compare-and-swap 完成。不得用
    dispatcher 自身的 mutex 代替；否则多副本部署和进程崩溃后会重复占用租约。
    """

    def enqueue(self, event: OutboxEvent) -> OutboxEvent: ...

    def claim(
        self,
        *,
        owner: str,
        limit: int,
        now: datetime,
        lease_seconds: float,
    ) -> tuple[OutboxEvent, ...]: ...

    def ack(self, event_id: str, *, owner: str, claim_token: str, now: datetime) -> OutboxEvent: ...

    def fail(
        self,
        event_id: str,
        *,
        owner: str,
        claim_token: str,
        error: str,
        now: datetime,
        available_at: datetime | None = None,
        waiting_reason: str | None = None,
    ) -> OutboxEvent: ...

    def release(
        self,
        event_id: str,
        *,
        owner: str,
        claim_token: str,
        now: datetime,
        available_at: datetime | None = None,
    ) -> OutboxEvent: ...


class OutboxPublisher(Protocol):
    """幂等发布端口；发布者必须以 dedupe_key 识别已处理事件。"""

    def publish(self, event: OutboxEvent) -> None: ...


Backoff = Callable[[OutboxEvent, datetime], datetime]


def exponential_backoff(event: OutboxEvent, now: datetime) -> datetime:
    """默认退避只决定下次可用时间，不设置固定重试上限。"""

    # 退避有上限但没有重试次数上限，避免持续故障在同一秒内空转。
    seconds = min(2 ** max(0, event.attempt_count - 1), 3600)
    return _utc(now) + timedelta(seconds=seconds)


class InMemoryOutboxStore:
    """仅用于契约测试的持久端口模型，不代表生产跨进程存储。"""

    def __init__(self, *, backoff: Backoff = exponential_backoff) -> None:
        self._events: dict[str, OutboxEvent] = {}
        self._dedupe: dict[str, str] = {}
        self._lock = RLock()
        self._backoff = backoff

    def enqueue(self, event: OutboxEvent) -> OutboxEvent:
        with self._lock:
            existing_by_id = self._events.get(event.id)
            if existing_by_id is not None:
                if not _same_event(existing_by_id, event):
                    raise OutboxDedupeConflict(f"event id conflict: {event.id}")
                return existing_by_id
            existing_id = self._dedupe.get(event.dedupe_key)
            if existing_id is not None:
                existing = self._events[existing_id]
                if not _same_event(existing, event):
                    raise OutboxDedupeConflict(f"dedupe key conflict: {event.dedupe_key}")
                return existing
            self._dedupe[event.dedupe_key] = event.id
            self._events[event.id] = event
            return event

    def get(self, event_id: str) -> OutboxEvent:
        try:
            return self._events[event_id]
        except KeyError as exc:
            raise OutboxNotFound(event_id) from exc

    def all(self) -> tuple[OutboxEvent, ...]:
        with self._lock:
            return tuple(self._events.values())

    def _reclaimable(self, event: OutboxEvent, now: datetime) -> bool:
        return event.status in OUTBOX_READY_STATUSES or (
            event.status == "publishing"
            and event.lease_expires_at is not None
            and event.lease_expires_at <= now
        )

    def claim(
        self,
        *,
        owner: str,
        limit: int,
        now: datetime,
        lease_seconds: float,
    ) -> tuple[OutboxEvent, ...]:
        if not owner.strip():
            raise ValueError("owner must not be empty")
        if limit < 1:
            raise ValueError("limit must be positive")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now = _utc(now)
        with self._lock:
            candidates = sorted(
                (
                    item
                    for item in self._events.values()
                    if self._reclaimable(item, now) and item.available_at <= now
                ),
                key=lambda item: (item.available_at, item.pending_at, item.id),
            )[:limit]
            claimed: list[OutboxEvent] = []
            for current in candidates:
                next_event = replace(
                    current,
                    status="publishing",
                    claim_token=f"claim-{uuid4()}",
                    lease_owner=owner,
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                    attempt_count=current.attempt_count + 1,
                    waiting_reason=None,
                )
                self._events[current.id] = next_event
                claimed.append(next_event)
            return tuple(claimed)

    def _require_claim(
        self, event_id: str, owner: str, claim_token: str, now: datetime
    ) -> OutboxEvent:
        current = self.get(event_id)
        if (
            current.status != "publishing"
            or current.lease_owner != owner
            or current.claim_token != claim_token
            or current.lease_expires_at is None
            or current.lease_expires_at <= _utc(now)
        ):
            raise OutboxFencedError(f"stale or invalid claim for event {event_id}")
        return current

    def ack(self, event_id: str, *, owner: str, claim_token: str, now: datetime) -> OutboxEvent:
        with self._lock:
            current = self._require_claim(event_id, owner, claim_token, now)
            next_event = replace(
                current,
                status="published",
                published_at=_utc(now),
                lease_owner=None,
                claim_token=None,
                lease_expires_at=None,
                last_error=None,
                waiting_reason=None,
            )
            self._events[event_id] = next_event
            return next_event

    def fail(
        self,
        event_id: str,
        *,
        owner: str,
        claim_token: str,
        error: str,
        now: datetime,
        available_at: datetime | None = None,
        waiting_reason: str | None = None,
    ) -> OutboxEvent:
        if not error.strip():
            raise ValueError("error must not be empty")
        with self._lock:
            current = self._require_claim(event_id, owner, claim_token, now)
            if waiting_reason not in {None, "dependency", "resource"}:
                raise ValueError("waiting_reason must be dependency, resource, or None")
            next_at = (
                _utc(available_at)
                if available_at is not None
                else self._backoff(current, _utc(now))
            )
            state = (
                "waiting_for_dependency"
                if waiting_reason == "dependency"
                else ("waiting_for_resource" if waiting_reason == "resource" else "failed")
            )
            next_event = replace(
                current,
                status=state,
                available_at=next_at,
                lease_owner=None,
                claim_token=None,
                lease_expires_at=None,
                last_error=error.strip(),
                waiting_reason=waiting_reason,
            )
            self._events[event_id] = next_event
            return next_event

    def release(
        self,
        event_id: str,
        *,
        owner: str,
        claim_token: str,
        now: datetime,
        available_at: datetime | None = None,
    ) -> OutboxEvent:
        with self._lock:
            current = self._require_claim(event_id, owner, claim_token, now)
            next_event = replace(
                current,
                status="pending",
                available_at=_utc(available_at) if available_at is not None else _utc(now),
                lease_owner=None,
                claim_token=None,
                lease_expires_at=None,
                waiting_reason=None,
            )
            self._events[event_id] = next_event
            return next_event


@dataclass(frozen=True, slots=True)
class DispatchOutcome:
    event_id: str
    status: str
    error: str | None = None


class OutboxDispatcher:
    """把已提交 Outbox 投递到幂等 publisher，不触碰 Task/Attempt 真相。"""

    def __init__(
        self,
        store: OutboxStore,
        publisher: OutboxPublisher,
        *,
        owner: str,
        lease_seconds: float = 60,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        if not owner.strip():
            raise ValueError("owner must not be empty")
        self.store = store
        self.publisher = publisher
        self.owner = owner
        self.lease_seconds = lease_seconds
        self.clock = clock

    def dispatch_once(self, *, limit: int = 10) -> tuple[DispatchOutcome, ...]:
        now = _utc(self.clock())
        claimed = self.store.claim(
            owner=self.owner,
            limit=limit,
            now=now,
            lease_seconds=self.lease_seconds,
        )
        outcomes: list[DispatchOutcome] = []
        for event in claimed:
            token = event.claim_token
            if token is None:
                outcomes.append(DispatchOutcome(event.id, "fenced", "claim token missing"))
                continue
            try:
                self.publisher.publish(event)
            except AlreadyPublished:
                try:
                    self.store.ack(
                        event.id, owner=self.owner, claim_token=token, now=_utc(self.clock())
                    )
                except OutboxFencedError as fence:
                    outcomes.append(DispatchOutcome(event.id, "fenced", str(fence)))
                else:
                    outcomes.append(DispatchOutcome(event.id, "published"))
            except Exception as exc:  # publisher 故障只能改变 Outbox，不改变 Task 真相
                try:
                    failed = self.store.fail(
                        event.id,
                        owner=self.owner,
                        claim_token=token,
                        error=str(exc) or type(exc).__name__,
                        now=_utc(self.clock()),
                    )
                    outcomes.append(DispatchOutcome(event.id, failed.status, failed.last_error))
                except OutboxFencedError as fence:
                    outcomes.append(DispatchOutcome(event.id, "fenced", str(fence)))
            else:
                try:
                    self.store.ack(
                        event.id, owner=self.owner, claim_token=token, now=_utc(self.clock())
                    )
                except OutboxFencedError as fence:
                    outcomes.append(DispatchOutcome(event.id, "fenced", str(fence)))
                else:
                    outcomes.append(DispatchOutcome(event.id, "published"))
        return tuple(outcomes)

    def dispatch_until_empty(
        self, *, batch_size: int = 10, max_batches: int | None = None
    ) -> tuple[DispatchOutcome, ...]:
        """批量 drain；``max_batches`` 是本次调用预算，不是重试上限。"""

        outcomes: list[DispatchOutcome] = []
        batches = 0
        while max_batches is None or batches < max_batches:
            batch = self.dispatch_once(limit=batch_size)
            if not batch:
                break
            outcomes.extend(batch)
            batches += 1
        return tuple(outcomes)


__all__ = [
    "AlreadyPublished",
    "DispatchOutcome",
    "InMemoryOutboxStore",
    "OutboxDedupeConflict",
    "OutboxDispatcher",
    "OutboxError",
    "OutboxEvent",
    "OutboxFencedError",
    "OutboxMessage",
    "OutboxNotFound",
    "OutboxPublisher",
    "OutboxRecord",
    "OutboxStore",
    "exponential_backoff",
]
