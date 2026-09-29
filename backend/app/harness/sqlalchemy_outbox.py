"""SQLAlchemy 持久化 Outbox 存储。

本模块把 :mod:`app.harness.outbox` 的内存契约落到
``harness_outbox_events`` 表。所有租约变更都在数据库事务中完成；
``ack``、``fail`` 和 ``release`` 通过 owner、token 与租约截止时间条件
更新，因而旧 worker 无法覆盖新 worker 的结果。

wzf：这里不使用进程锁，也不从应用配置隐式创建数据库连接；调用方必须
显式提供 ``session_factory``，便于跨进程、重启和故障恢复验证。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Iterator
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.harness_models import HarnessOutboxEvent
from app.harness.outbox import (
    AlreadyPublished,
    Backoff,
    InMemoryOutboxStore,
    OutboxDedupeConflict,
    OutboxDispatcher,
    OutboxError,
    OutboxEvent,
    OutboxFencedError,
    OutboxMessage,
    OutboxNotFound,
    OutboxPublisher,
    OutboxRecord,
    OutboxStore,
    _same_event,
    _utc,
    exponential_backoff,
)


_PERSISTED_STATUSES = frozenset(
    {
        "pending",
        "publishing",
        "published",
        "failed",
        "waiting_for_dependency",
        "waiting_for_resource",
        "dead_letter",
    }
)
_READY_STATUSES = frozenset(
    {
        "pending",
        "failed",
        "waiting_for_dependency",
        "waiting_for_resource",
    }
)


def _canonical_uuid(value: object, field_name: str) -> UUID:
    """解析 canonical UUID 字符串；拒绝大小写、花括号和隐式 UUID5。"""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a canonical UUID string")
    text = value.strip()
    try:
        parsed = UUID(text)
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a canonical UUID string") from exc
    if str(parsed) != text:
        raise ValueError(f"{field_name} must be a lowercase canonical UUID")
    return parsed


def _db_datetime(session: Session, value: datetime) -> datetime:
    """把 UTC 时间转成当前方言的绑定值。

    SQLite 的 ``DateTime(timezone=True)`` 仍以 naive 值落盘，所以约定
    naive 值始终代表 UTC；PostgreSQL 保留带时区的 UTC 值。
    """

    utc_value = _utc(value)
    try:
        dialect = session.get_bind().dialect.name
    except Exception:
        dialect = ""
    return utc_value.replace(tzinfo=None) if dialect == "sqlite" else utc_value


def _row_label(row: HarnessOutboxEvent) -> str:
    return str(row.id)


class SQLAlchemyOutboxStore:
    """基于 SQLAlchemy Session 工厂的跨进程 Outbox 实现。"""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        backoff: Backoff = exponential_backoff,
    ) -> None:
        if not callable(session_factory):
            raise TypeError("session_factory must be callable")
        if not callable(backoff):
            raise TypeError("backoff must be callable")
        self.session_factory = session_factory
        self._backoff = backoff

    @contextmanager
    def _session(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
        finally:
            session.close()

    @staticmethod
    def _validate_event(event: OutboxEvent) -> tuple[UUID, UUID]:
        if not isinstance(event, OutboxEvent):
            raise TypeError("event must be an OutboxEvent")
        event_id = _canonical_uuid(event.id, "event.id")
        aggregate_id = _canonical_uuid(event.aggregate_id, "event.aggregate_id")
        if event.status == "publishing":
            raise ValueError("enqueue must not accept a publishing event")
        if event.status not in _PERSISTED_STATUSES:
            raise ValueError(f"unsupported persisted outbox status: {event.status}")
        # 0004 的 check 约束只接受 canonical waiting 状态；兼容别名只在内存契约中保留。
        if event.status in {"waiting_for_dependency", "waiting_for_resource"}:
            expected = "dependency" if event.status == "waiting_for_dependency" else "resource"
            if event.waiting_reason != expected:
                raise ValueError(f"{event.status} requires waiting_reason={expected!r}")
        elif event.waiting_reason is not None:
            raise ValueError("waiting_reason is only valid for canonical waiting statuses")
        return event_id, aggregate_id

    @staticmethod
    def _row_to_event(row: HarnessOutboxEvent) -> OutboxEvent:
        """把 ORM 行恢复为领域事件，并再次验证持久 UUID 与 fencing 完整性。"""

        event_id = _canonical_uuid(str(row.id), "persisted event.id")
        if row.aggregate_id is None:
            raise OutboxError(f"outbox event {_row_label(row)} has no aggregate_id")
        try:
            aggregate_id = _canonical_uuid(str(row.aggregate_id), "persisted aggregate_id")
        except ValueError as exc:
            raise OutboxError(
                f"outbox event {_row_label(row)} has a non-canonical aggregate_id"
            ) from exc
        payload = row.payload_json
        if not isinstance(payload, Mapping):
            raise OutboxError(f"outbox event {_row_label(row)} payload is not a mapping")
        try:
            return OutboxEvent(
                id=str(event_id),
                dedupe_key=row.dedupe_key,
                event_type=row.event_type,
                aggregate_type=row.aggregate_type,
                aggregate_id=str(aggregate_id),
                payload=dict(payload),
                status=row.status,
                pending_at=_utc(row.pending_at),
                available_at=_utc(row.available_at),
                attempt_count=row.attempt_count,
                claim_token=row.claim_token,
                lease_owner=row.lease_owner,
                lease_expires_at=(
                    _utc(row.lease_expires_at) if row.lease_expires_at is not None else None
                ),
                published_at=_utc(row.published_at) if row.published_at is not None else None,
                last_error=row.last_error,
                waiting_reason=row.waiting_reason,
            )
        except (TypeError, ValueError) as exc:
            raise OutboxError(f"invalid persisted outbox event {_row_label(row)}") from exc

    @staticmethod
    def _row_values(
        event: OutboxEvent,
        event_id: UUID,
        aggregate_id: UUID,
        session: Session,
    ) -> dict[str, Any]:
        pending_at = _db_datetime(session, event.pending_at)
        return {
            "id": event_id,
            "dedupe_key": event.dedupe_key,
            "event_type": event.event_type,
            "aggregate_type": event.aggregate_type,
            "aggregate_id": aggregate_id,
            "payload_json": dict(event.payload),
            "status": event.status,
            "pending_at": pending_at,
            "available_at": _db_datetime(session, event.available_at),
            "published_at": (
                _db_datetime(session, event.published_at)
                if event.published_at is not None
                else None
            ),
            "attempt_count": event.attempt_count,
            "last_error": event.last_error,
            "created_at": pending_at,
            "claim_token": None,
            "lease_owner": None,
            "lease_expires_at": None,
            "waiting_reason": event.waiting_reason,
            "updated_at": pending_at,
        }

    @staticmethod
    def _lookup(session: Session, event_id: UUID, dedupe_key: str) -> HarnessOutboxEvent | None:
        row = session.execute(
            select(HarnessOutboxEvent).where(HarnessOutboxEvent.id == event_id)
        ).scalar_one_or_none()
        if row is None:
            row = session.execute(
                select(HarnessOutboxEvent).where(HarnessOutboxEvent.dedupe_key == dedupe_key)
            ).scalar_one_or_none()
        return row

    def _find_existing(self, event: OutboxEvent) -> OutboxEvent | None:
        event_id, _aggregate_id = self._validate_event(event)
        with self._session() as session:
            row = self._lookup(session, event_id, event.dedupe_key)
            return self._row_to_event(row) if row is not None else None

    def enqueue(self, event: OutboxEvent) -> OutboxEvent:
        event_id, aggregate_id = self._validate_event(event)
        try:
            with self._session() as session:
                with session.begin():
                    existing = self._lookup(session, event_id, event.dedupe_key)
                    if existing is not None:
                        existing_event = self._row_to_event(existing)
                        if not _same_event(existing_event, event):
                            raise OutboxDedupeConflict(
                                f"event id or dedupe key conflict: {event.id}/{event.dedupe_key}"
                            )
                        return existing_event
                    row = HarnessOutboxEvent(
                        **self._row_values(event, event_id, aggregate_id, session)
                    )
                    session.add(row)
                    # flush 触发数据库唯一约束；竞态由下方新 Session 分支重新读取。
                    session.flush()
                    return self._row_to_event(row)
        except IntegrityError:
            # 只把确实能读到的相同 dedupe 记录视为幂等；其他完整性错误原样暴露，避免吞错。
            existing = self._find_existing(event)
            if existing is None:
                raise
            if not _same_event(existing, event):
                raise OutboxDedupeConflict(
                    f"event id or dedupe key conflict: {event.id}/{event.dedupe_key}"
                )
            return existing

    def get(self, event_id: str) -> OutboxEvent:
        parsed = _canonical_uuid(event_id, "event_id")
        with self._session() as session:
            row = session.execute(
                select(HarnessOutboxEvent).where(HarnessOutboxEvent.id == parsed)
            ).scalar_one_or_none()
            if row is None:
                raise OutboxNotFound(event_id)
            return self._row_to_event(row)

    def all(self) -> tuple[OutboxEvent, ...]:
        with self._session() as session:
            rows = session.execute(
                select(HarnessOutboxEvent).order_by(
                    HarnessOutboxEvent.available_at,
                    HarnessOutboxEvent.pending_at,
                    HarnessOutboxEvent.id,
                )
            ).scalars()
            return tuple(self._row_to_event(row) for row in rows)

    @staticmethod
    def _claimable_filter(now: datetime):
        return and_(
            HarnessOutboxEvent.available_at <= now,
            or_(
                HarnessOutboxEvent.status.in_(_READY_STATUSES),
                and_(
                    HarnessOutboxEvent.status == "publishing",
                    HarnessOutboxEvent.lease_expires_at <= now,
                ),
            ),
        )

    def claim(
        self,
        *,
        owner: str,
        limit: int,
        now: datetime,
        lease_seconds: float,
    ) -> tuple[OutboxEvent, ...]:
        if not isinstance(owner, str) or not owner.strip():
            raise ValueError("owner must not be empty")
        if len(owner.strip()) > 200:
            raise ValueError("owner must be at most 200 characters")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be positive")
        if isinstance(lease_seconds, bool) or lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        now_utc = _utc(now)
        with self._session() as session:
            with session.begin():
                db_now = _db_datetime(session, now_utc)
                # PostgreSQL 等支持行锁的数据库在这里 skip-locked；SQLite 会忽略 FOR UPDATE，
                # 但随后每行 UPDATE 仍带同一 claimable 条件，CAS 结果决定唯一 owner。
                candidates = (
                    session.execute(
                        select(HarnessOutboxEvent)
                        .where(self._claimable_filter(db_now))
                        .order_by(
                            HarnessOutboxEvent.available_at,
                            HarnessOutboxEvent.pending_at,
                            HarnessOutboxEvent.id,
                        )
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                    .scalars()
                    .all()
                )
                expires = _db_datetime(session, now_utc + timedelta(seconds=lease_seconds))
                claimed: list[OutboxEvent] = []
                for row in candidates:
                    token = f"claim-{uuid4()}"
                    result = session.execute(
                        update(HarnessOutboxEvent)
                        .where(
                            HarnessOutboxEvent.id == row.id,
                            self._claimable_filter(db_now),
                        )
                        .values(
                            status="publishing",
                            claim_token=token,
                            # 与 OutboxDispatcher 保持同一 owner 语义。Dispatcher 会把
                            # 构造时的原值传给 ack/fail；若此处擅自 strip，合法但带
                            # 首尾空白的 owner 会在刚 claim 后立即被 fencing。
                            lease_owner=owner,
                            lease_expires_at=expires,
                            attempt_count=HarnessOutboxEvent.attempt_count + 1,
                            waiting_reason=None,
                            updated_at=db_now,
                        )
                        .execution_options(synchronize_session=False)
                    )
                    if result.rowcount != 1:
                        continue
                    session.expire(row)
                    session.refresh(row)
                    claimed.append(self._row_to_event(row))
                return tuple(claimed)

    @staticmethod
    def _live_claim_filter(
        event_id: UUID,
        owner: object,
        claim_token: object,
        now: datetime,
    ):
        return and_(
            HarnessOutboxEvent.id == event_id,
            HarnessOutboxEvent.status == "publishing",
            HarnessOutboxEvent.lease_owner == owner,
            HarnessOutboxEvent.claim_token == claim_token,
            HarnessOutboxEvent.lease_expires_at > now,
        )

    @staticmethod
    def _fenced(event_id: str) -> OutboxFencedError:
        return OutboxFencedError(f"stale or invalid claim for event {event_id}")

    def ack(self, event_id: str, *, owner: str, claim_token: str, now: datetime) -> OutboxEvent:
        parsed = _canonical_uuid(event_id, "event_id")
        with self._session() as session:
            with session.begin():
                db_now = _db_datetime(session, _utc(now))
                result = session.execute(
                    update(HarnessOutboxEvent)
                    .where(self._live_claim_filter(parsed, owner, claim_token, db_now))
                    .values(
                        status="published",
                        published_at=db_now,
                        claim_token=None,
                        lease_owner=None,
                        lease_expires_at=None,
                        last_error=None,
                        waiting_reason=None,
                        updated_at=db_now,
                    )
                    .execution_options(synchronize_session=False)
                )
                if result.rowcount != 1:
                    raise self._fenced(event_id)
                row = session.get(HarnessOutboxEvent, parsed)
                if row is None:  # pragma: no cover - UPDATE already matched this row
                    raise self._fenced(event_id)
                session.refresh(row)
                return self._row_to_event(row)

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
        if not isinstance(error, str) or not error.strip():
            raise ValueError("error must not be empty")
        if waiting_reason not in {None, "dependency", "resource"}:
            raise ValueError("waiting_reason must be dependency, resource, or None")
        parsed = _canonical_uuid(event_id, "event_id")
        now_utc = _utc(now)
        with self._session() as session:
            with session.begin():
                db_now = _db_datetime(session, now_utc)
                row = session.get(HarnessOutboxEvent, parsed)
                if row is None:
                    raise self._fenced(event_id)
                current = self._row_to_event(row)
                next_at = (
                    _utc(available_at)
                    if available_at is not None
                    else _utc(self._backoff(current, now_utc))
                )
                state = (
                    "waiting_for_dependency"
                    if waiting_reason == "dependency"
                    else "waiting_for_resource"
                    if waiting_reason == "resource"
                    else "failed"
                )
                result = session.execute(
                    update(HarnessOutboxEvent)
                    .where(self._live_claim_filter(parsed, owner, claim_token, db_now))
                    .values(
                        status=state,
                        available_at=_db_datetime(session, next_at),
                        claim_token=None,
                        lease_owner=None,
                        lease_expires_at=None,
                        last_error=error.strip(),
                        waiting_reason=waiting_reason,
                        updated_at=db_now,
                    )
                    .execution_options(synchronize_session=False)
                )
                if result.rowcount != 1:
                    raise self._fenced(event_id)
                session.refresh(row)
                return self._row_to_event(row)

    def release(
        self,
        event_id: str,
        *,
        owner: str,
        claim_token: str,
        now: datetime,
        available_at: datetime | None = None,
    ) -> OutboxEvent:
        parsed = _canonical_uuid(event_id, "event_id")
        now_utc = _utc(now)
        with self._session() as session:
            with session.begin():
                db_now = _db_datetime(session, now_utc)
                result = session.execute(
                    update(HarnessOutboxEvent)
                    .where(self._live_claim_filter(parsed, owner, claim_token, db_now))
                    .values(
                        status="pending",
                        available_at=_db_datetime(
                            session, _utc(available_at) if available_at is not None else now_utc
                        ),
                        claim_token=None,
                        lease_owner=None,
                        lease_expires_at=None,
                        waiting_reason=None,
                        updated_at=db_now,
                    )
                    .execution_options(synchronize_session=False)
                )
                if result.rowcount != 1:
                    raise self._fenced(event_id)
                row = session.get(HarnessOutboxEvent, parsed)
                if row is None:  # pragma: no cover - UPDATE already matched this row
                    raise self._fenced(event_id)
                session.refresh(row)
                return self._row_to_event(row)


__all__ = [
    "AlreadyPublished",
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
    "SQLAlchemyOutboxStore",
    "exponential_backoff",
]
