from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth.sessions import get_user_by_session_token
from app.config import get_settings
from app.db.models import User
from app.db.session import get_db


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    session_cookie: str | None = Cookie(default=None, alias=get_settings().session_cookie_name),
) -> User:
    user = get_user_by_session_token(db, session_cookie)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )
    request.state.user = user
    return user


def get_current_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin permission required"
        )
    return user
