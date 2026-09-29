import secrets

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.auth.passwords import hash_password, verify_password
from app.auth.sessions import create_session, delete_session
from app.config import get_settings
from app.db.models import SystemState, User, now_utc
from app.db.locks import acquire_transaction_lock
from app.db.session import get_db
from app.network import client_ip
from app.schemas.auth import AuthRequest, UserResponse


router = APIRouter(prefix="/api/auth", tags=["auth"])


def user_response(user: User) -> UserResponse:
    return UserResponse(id=str(user.id), username=user.username, role=user.role)


def claim_initial_admin(db: Session, user: User) -> bool:
    """原子认领唯一首位管理员，避免并发注册产生多个管理员。"""

    if db.scalar(select(User.id).where(User.role == "admin").limit(1)) is not None:
        return False
    values = {
        "key": "auth.initial_admin",
        "value_json": {"user_id": str(user.id)},
        "created_at": now_utc(),
        "updated_at": now_utc(),
    }
    dialect_name = db.get_bind().dialect.name
    if dialect_name == "sqlite":
        statement = sqlite_insert(SystemState).values(**values).on_conflict_do_nothing(
            index_elements=[SystemState.key]
        )
    elif dialect_name == "postgresql":
        statement = postgresql_insert(SystemState).values(**values).on_conflict_do_nothing(
            index_elements=[SystemState.key]
        )
    else:
        raise RuntimeError(f"Unsupported database dialect for admin bootstrap: {dialect_name}")
    claimed = db.execute(statement)
    if claimed.rowcount != 1:
        return False
    user.role = "admin"
    return True


def authorize_initial_admin_claim(
    db: Session,
    bootstrap_token: str | None,
) -> bool:
    """生产环境首位管理员必须证明持有服务器本地引导令牌。"""

    if db.scalar(select(User.id).where(User.role == "admin").limit(1)) is not None:
        return False
    settings = get_settings()
    if settings.app_env.lower() not in {"production", "release"}:
        return True
    expected = settings.initial_admin_bootstrap_token or ""
    supplied = bootstrap_token or ""
    if not expected or not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Initial administrator bootstrap token is required",
        )
    return True


@router.get("/registration")
def registration_status(db: Session = Depends(get_db)):
    settings = get_settings()
    first_user = db.scalar(select(User.id).where(User.role == "admin").limit(1)) is None
    requires_bootstrap_token = (
        first_user and settings.app_env.lower() in {"production", "release"}
    )
    return {
        # Even with public registration disabled, the installation must expose
        # one bootstrap path for its first administrator.
        "enabled": settings.registration_mode == "open" or first_user,
        "mode": settings.registration_mode,
        "first_user": first_user,
        "requires_bootstrap_token": requires_bootstrap_token,
    }


@router.post("/register", response_model=UserResponse)
def register(payload: AuthRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    acquire_transaction_lock(db, "auth.bootstrap")
    existing = db.scalar(select(User).where(User.username == payload.username))
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid username or password")

    should_claim_initial_admin = authorize_initial_admin_claim(
        db,
        payload.bootstrap_token,
    )
    if get_settings().registration_mode == "disabled" and not should_claim_initial_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Public registration is disabled; ask an administrator for an account",
        )
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role="user",
    )
    db.add(user)
    try:
        db.flush()
        if should_claim_initial_admin:
            claim_initial_admin(db, user)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid username or password",
        ) from exc
    db.refresh(user)

    token, _session = create_session(
        db,
        user,
        user_agent=request.headers.get("user-agent"),
        ip=client_ip(request),
    )
    settings = get_settings()
    response.set_cookie(
        settings.session_cookie_name,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.session_ttl_days * 24 * 60 * 60,
        path="/",
    )
    return user_response(user)


@router.post("/login", response_model=UserResponse)
def login(payload: AuthRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == payload.username))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid username or password")
    if user.disabled_at is not None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User disabled")

    token, _session = create_session(
        db,
        user,
        user_agent=request.headers.get("user-agent"),
        ip=client_ip(request),
    )
    settings = get_settings()
    response.set_cookie(
        settings.session_cookie_name,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.session_ttl_days * 24 * 60 * 60,
        path="/",
    )
    return user_response(user)


@router.post("/logout")
def logout(
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    session_cookie: str | None = Cookie(default=None, alias=get_settings().session_cookie_name),
):
    settings = get_settings()
    delete_session(db, session_cookie)
    response.delete_cookie(settings.session_cookie_name, path="/")
    return {"ok": True, "user_id": str(user.id)}


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(get_current_user)):
    return user_response(user)
