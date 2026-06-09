import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import AuthSession, User, now_utc


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
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at < datetime.now(timezone.utc):
        db.delete(session)
        db.commit()
        return None
    user = db.get(User, session.user_id)
    if not user or user.disabled_at is not None:
        return None
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
