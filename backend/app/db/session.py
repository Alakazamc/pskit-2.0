from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings
from app.db.base import Base


settings = get_settings()
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)


if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=10000")
            cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.artifact_dir.mkdir(parents=True, exist_ok=True)
    from app.db import harness_models, models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    # wzf：旧数据升级在启动写阶段显式完成，读取 API 不承担隐式迁移。
    from app.agent.policy import backfill_strategy_policies

    db = SessionLocal()
    try:
        backfill_strategy_policies(db)
    finally:
        db.close()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
