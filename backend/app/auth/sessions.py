import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import AuthSession, User, ensure_utc, now_utc


def make_session_token() -> str:
    return secrets.token_urlsafe(48)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(
    db: Session,
    user: User,
    user_agent: str | None = None,
    ip: str | None = None,
) -> tuple[str, AuthSession]:
    settings = get_settings()
    now = now_utc()
    maximum = max(1, int(os.getenv("PSKIT_MAX_ACTIVE_SESSIONS_PER_USER", "5")))
    active = db.scalars(
        select(AuthSession)
        .where(AuthSession.user_id == user.id)
        .order_by(AuthSession.created_at.desc())
    ).all()
    retained = []
    for existing in active:
        if ensure_utc(existing.expires_at) <= now:
            db.delete(existing)
        else:
            retained.append(existing)
    for existing in retained[maximum - 1 :]:
        db.delete(existing)
    token = make_session_token()
    session = AuthSession(
        session_hash=hash_session_token(token),
        user_id=user.id,
        expires_at=now_utc() + timedelta(days=settings.session_ttl_days),
        user_agent=user_agent,
        ip=ip,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return token, session


def get_user_by_session_token(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    session = db.get(AuthSession, hash_session_token(token))
    if not session:
        return None
    expires_at = ensure_utc(session.expires_at)
    issued_at = ensure_utc(session.created_at)
    configured_expiry = issued_at + timedelta(days=get_settings().session_ttl_days)
    expires_at = min(expires_at, configured_expiry)
    if expires_at < datetime.now(timezone.utc):
        db.delete(session)
        db.commit()
        return None
    user = db.get(User, session.user_id)
    if not user or user.disabled_at is not None:
        return None
    # wzf：认证读取不应把每个 API 请求都变成写事务；五分钟粒度足够审计活跃度。
    if ensure_utc(session.last_seen_at) < now_utc() - timedelta(minutes=5):
        session.last_seen_at = now_utc()
        db.commit()
    return user


def delete_session(db: Session, token: str | None) -> None:
    if not token:
        return
    session = db.get(AuthSession, hash_session_token(token))
    if session:
        db.delete(session)
        db.commit()
