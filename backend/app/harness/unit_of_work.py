"""PSKit AI4S Harness 的外层 SQLAlchemy Unit of Work。

wzf：UoW 是单个 Harness 持久化批次的唯一事务拥有者。它只接收调用方
提供的 ``session_factory``，不读取生产配置、不创建第二个 Session，也不
直接操作 Outbox store；业务事实和同批 Outbox 均由
``SQLAlchemyInvocationRepository`` 写入当前 Session。
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from types import TracebackType
from typing import Any
from uuid import UUID

from app.harness.repository import (
    SQLAlchemyInvocationRepository,
    _db_uuid,
)


class HarnessUnitOfWorkError(RuntimeError):
    """UoW 生命周期或事务边界无法安全满足时抛出的异常。"""


class HarnessUnitOfWorkScopeError(HarnessUnitOfWorkError, ValueError):
    """user/session 标识非法，或 Session 不属于指定 user。"""


_ACTIVE_UOW: ContextVar[object | None] = ContextVar(
    "pskit_harness_active_uow",
    default=None,
)


class HarnessUnitOfWork:
    """拥有一个 SQLAlchemy Session 和一个显式外层事务的 Harness UoW。

    ``session_factory`` 必须是返回新 Session 的无参可调用对象（例如
    ``sessionmaker``）。UoW 只允许使用一次，并且不能在另一个活跃 UoW 内
    嵌套。调用方必须通过显式 ``with`` 进入/退出，以确保提交边界可审计。
    """

    def __init__(
        self,
        session_factory: Callable[[], Any],
        *,
        user_id: str | UUID,
        session_id: str | UUID,
    ) -> None:
        if not callable(session_factory):
            raise TypeError("session_factory must be callable")
        self.session_factory = session_factory
        self.user_id = self._scope_uuid(user_id, "user")
        self.session_id = self._scope_uuid(session_id, "session")
        self._state = "new"
        self._session: Any | None = None
        self._transaction: Any | None = None
        self._repository: SQLAlchemyInvocationRepository | None = None
        self._active_token: Any | None = None

    @staticmethod
    def _scope_uuid(value: str | UUID, kind: str) -> UUID:
        """复用 Repository 的严格实体 ID 规则，并把错误归一为 scope 错误。"""

        try:
            return _db_uuid(value, kind=kind)
        except Exception as exc:  # noqa: BLE001 - scope 必须统一 fail-closed
            raise HarnessUnitOfWorkScopeError(f"invalid {kind}_id") from exc

    @property
    def session(self) -> Any:
        """返回当前 UoW 独占的 Session；离开作用域后拒绝使用。"""

        session = self._session
        if self._state != "entered" or session is None:
            raise HarnessUnitOfWorkError("UnitOfWork session is only available inside context")
        return session

    @property
    def repository(self) -> SQLAlchemyInvocationRepository:
        """返回与 UoW 同一 Session 绑定的作用域 Repository。"""

        repository = self._repository
        if self._state != "entered" or repository is None:
            raise HarnessUnitOfWorkError(
                "UnitOfWork repository is only available inside context"
            )
        return repository

    # ``repo`` 仅是便于旧调用方迁移的只读别名，不创建新的 Repository/Session。
    @property
    def repo(self) -> SQLAlchemyInvocationRepository:
        return self.repository

    def __enter__(self) -> "HarnessUnitOfWork":
        if self._state != "new":
            raise HarnessUnitOfWorkError("UnitOfWork cannot be reused")
        if _ACTIVE_UOW.get() is not None:
            raise HarnessUnitOfWorkError("nested UnitOfWork is not allowed")

        session: Any | None = None
        transaction: Any | None = None
        token: Any | None = None
        try:
            # wzf：Session 必须由调用方工厂新建并由本 UoW 独占；已有事务或
            # 未清空的 identity map 会把外层未知对象带进本批，因此主动拒绝。
            session = self.session_factory()
            self._require_session(session)
            self._require_clean_session(session)

            transaction = session.begin()
            if transaction is None or not callable(getattr(transaction, "__enter__", None)):
                raise HarnessUnitOfWorkError("session.begin() did not return a context manager")
            transaction.__enter__()

            self._validate_scope(session)
            repository = SQLAlchemyInvocationRepository(
                session,
                user_id=self.user_id,
                session_id=self.session_id,
                owns_transaction=True,
            )
            token = _ACTIVE_UOW.set(self)
            self._session = session
            self._transaction = transaction
            self._repository = repository
            self._active_token = token
            self._state = "entered"
            return self
        except BaseException as exc:
            # __enter__ 失败也必须回滚已打开的外层事务，并关闭工厂返回的
            # Session；否则 scope 校验失败会泄漏连接或留下隐式事务。
            if transaction is not None:
                self._exit_transaction(
                    transaction,
                    session,
                    type(exc),
                    exc,
                    exc.__traceback__,
                )
            if token is not None:
                _ACTIVE_UOW.reset(token)
            self._close_session(session)
            self._state = "failed"
            raise

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        if self._state != "entered" or self._transaction is None:
            raise HarnessUnitOfWorkError("UnitOfWork context is not active")

        transaction = self._transaction
        session = self._session
        try:
            # 事务 context manager 的正常 __exit__ 才能调用 commit；UoW 不
            # 调用 Session.commit，保证这里只有一个可审计提交边界。
            self._exit_transaction(transaction, session, exc_type, exc, traceback)
        finally:
            if self._active_token is not None:
                _ACTIVE_UOW.reset(self._active_token)
                self._active_token = None
            self._repository = None
            self._transaction = None
            self._session = None
            self._state = "closed"
            self._close_session(session)
        return False

    @staticmethod
    def _require_session(session: Any) -> None:
        if session is None:
            raise HarnessUnitOfWorkError("session_factory returned None")
        for name in ("begin", "close", "rollback"):
            if not callable(getattr(session, name, None)):
                raise HarnessUnitOfWorkError(f"SQLAlchemy Session must expose {name}()")
        if not callable(getattr(session, "flush", None)):
            raise HarnessUnitOfWorkError("SQLAlchemy Session must expose flush()")

    @staticmethod
    def _require_clean_session(session: Any) -> None:
        in_transaction = getattr(session, "in_transaction", None)
        active = bool(in_transaction() if callable(in_transaction) else in_transaction)
        if active:
            raise HarnessUnitOfWorkError(
                "session_factory returned a Session with an active transaction"
            )
        for attr in ("new", "dirty", "deleted"):
            if getattr(session, attr, ()):
                raise HarnessUnitOfWorkError("session_factory returned a dirty Session")
        identity_map = getattr(session, "identity_map", ())
        if identity_map:
            raise HarnessUnitOfWorkError("session_factory returned a non-empty identity map")

    def _validate_scope(self, session: Any) -> None:
        """在同一外层事务中核验 research_sessions 的 user 归属。"""

        try:
            from sqlalchemy import select
            from app.db.harness_models import ResearchSession

            row = session.scalar(
                select(ResearchSession).where(
                    ResearchSession.id == self.session_id,
                )
            )
        except BaseException as exc:
            raise HarnessUnitOfWorkScopeError("cannot validate research session scope") from exc
        if row is None or row.user_id != self.user_id:
            raise HarnessUnitOfWorkScopeError("session_id is not owned by user_id")

    @staticmethod
    def _exit_transaction(
        transaction: Any,
        session: Any | None,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """退出外层事务；提交异常时显式 rollback，再把原异常继续抛出。"""

        try:
            transaction.__exit__(exc_type, exc, traceback)
        except BaseException:
            HarnessUnitOfWork._rollback_safely(session)
            raise
        if exc_type is not None:
            # SQLAlchemy context manager 已会回滚；再次显式调用是为了让
            # 适配的 Session/测试替身也满足“异常必回滚”的 fail-closed 契约。
            HarnessUnitOfWork._rollback_safely(session)

    @staticmethod
    def _rollback_safely(session: Any | None) -> None:
        if session is None or not callable(getattr(session, "rollback", None)):
            return
        try:
            session.rollback()
        except BaseException:
            # 保留提交/原始业务异常；close 仍在调用方 finally 中执行。
            pass

    @staticmethod
    def _close_session(session: Any | None) -> None:
        if session is None:
            return
        try:
            session.close()
        except BaseException:
            # 关闭失败不能掩盖业务或提交错误，但不会再次使用该 Session。
            pass


UnitOfWork = HarnessUnitOfWork


__all__ = [
    "HarnessUnitOfWork",
    "HarnessUnitOfWorkError",
    "HarnessUnitOfWorkScopeError",
    "UnitOfWork",
]
