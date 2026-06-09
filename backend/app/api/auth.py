from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.auth.passwords import hash_password, verify_password
from app.auth.sessions import create_session, delete_session
from app.config import get_settings
from app.db.models import User
from app.db.session import get_db
from app.schemas.auth import AuthRequest, UserResponse


router = APIRouter(prefix="/api/auth", tags=["auth"])


def user_response(user: User) -> UserResponse:
    return UserResponse(id=str(user.id), username=user.username, role=user.role)


@router.post("/register", response_model=UserResponse)
def register(payload: AuthRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    existing = db.scalar(select(User).where(User.username == payload.username))
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid username or password")

    user_count = db.scalar(select(func.count()).select_from(User)) or 0
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role="admin" if user_count == 0 else "user",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token, _session = create_session(
        db,
        user,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
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
        ip=request.client.host if request.client else None,
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
