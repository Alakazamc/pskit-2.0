from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_admin, get_current_user
from app.auth.passwords import hash_password, verify_password
from app.auth.rate_limit import SlidingWindowRateLimiter
from app.auth.sessions import create_session, delete_session
from app.config import get_settings
from app.db.models import AppState, User
from app.db.session import get_db
from app.schemas.auth import (
    AdminCreateUserRequest,
    AuthRequest,
    RegistrationStatusResponse,
    UserResponse,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
SETTINGS = get_settings()
login_limiter = SlidingWindowRateLimiter(
    SETTINGS.login_rate_limit_attempts,
    SETTINGS.login_rate_limit_window_seconds,
)


def user_response(user: User) -> UserResponse:
    return UserResponse(id=str(user.id), username=user.username, role=user.role)


def client_key(request: Request, username: str) -> str:
    ip = request.client.host if request.client else "unknown"
    return f"{ip}:{username.casefold()}"


def persist_user(db: Session, payload: AuthRequest, role: str) -> User:
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=role,
    )
    db.add(user)
    return user


@router.get("/registration", response_model=RegistrationStatusResponse)
def registration_status(db: Session = Depends(get_db)):
    user_count = db.scalar(select(func.count()).select_from(User)) or 0
    mode = SETTINGS.registration_mode
    enabled = mode == "open" or (mode == "first_user" and user_count == 0)
    return RegistrationStatusResponse(enabled=enabled, mode=mode, first_user=user_count == 0)


@router.post("/register", response_model=UserResponse)
def register(
    payload: AuthRequest, request: Request, response: Response, db: Session = Depends(get_db)
):
    mode = SETTINGS.registration_mode
    user_count = db.scalar(select(func.count()).select_from(User)) or 0
    if mode == "disabled" or (mode == "first_user" and user_count > 0):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Registration is closed")

    existing = db.scalar(select(User).where(User.username == payload.username))
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid username or password"
        )

    role = "admin" if mode == "first_user" else "user"
    user = persist_user(db, payload, role)
    if mode == "first_user":
        db.add(AppState(key="admin_initialized", value=payload.username))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Registration was completed by another request",
        ) from exc
    db.refresh(user)

    token, _session = create_session(
        db,
        user,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    )
    response.set_cookie(
        SETTINGS.session_cookie_name,
        token,
        httponly=True,
        secure=SETTINGS.cookie_secure,
        samesite="lax",
        max_age=SETTINGS.session_ttl_days * 24 * 60 * 60,
        path="/",
    )
    return user_response(user)


@router.post("/login", response_model=UserResponse)
def login(
    payload: AuthRequest, request: Request, response: Response, db: Session = Depends(get_db)
):
    key = client_key(request, payload.username)
    retry_after = login_limiter.retry_after(key)
    if retry_after is not None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts",
            headers={"Retry-After": str(retry_after)},
        )
    user = db.scalar(select(User).where(User.username == payload.username))
    if not user or not verify_password(payload.password, user.password_hash):
        login_limiter.record_failure(key)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid username or password"
        )
    if user.disabled_at is not None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User disabled")

    login_limiter.reset(key)
    token, _session = create_session(
        db,
        user,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    )
    response.set_cookie(
        SETTINGS.session_cookie_name,
        token,
        httponly=True,
        secure=SETTINGS.cookie_secure,
        samesite="lax",
        max_age=SETTINGS.session_ttl_days * 24 * 60 * 60,
        path="/",
    )
    return user_response(user)


@router.post("/users", response_model=UserResponse)
def admin_create_user(
    payload: AdminCreateUserRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    existing = db.scalar(select(User).where(User.username == payload.username))
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists")
    user = persist_user(db, payload, payload.role)
    db.commit()
    db.refresh(user)
    return user_response(user)


@router.post("/logout")
def logout(
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    session_cookie: str | None = Cookie(default=None, alias=get_settings().session_cookie_name),
):
    delete_session(db, session_cookie)
    response.delete_cookie(SETTINGS.session_cookie_name, path="/")
    return {"ok": True, "user_id": str(user.id)}


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(get_current_user)):
    return user_response(user)
