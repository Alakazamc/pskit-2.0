from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_admin
from app.db.models import (
    AgentTurn,
    Artifact,
    AuditEvent,
    AuthSession,
    Task,
    User,
    now_utc,
)
from app.db.session import get_db
from app.schemas.admin import (
    AdminMetricsResponse,
    AdminUserResponse,
    UpdateAdminUserRequest,
)


router = APIRouter(prefix="/api/admin", tags=["admin"])


def _user_response(db: Session, user: User) -> AdminUserResponse:
    active_sessions = db.scalar(
        select(func.count()).select_from(AuthSession).where(
            AuthSession.user_id == user.id,
            AuthSession.expires_at > now_utc(),
        )
    ) or 0
    return AdminUserResponse(
        id=str(user.id),
        username=user.username,
        role=user.role,
        disabled=user.disabled_at is not None,
        active_sessions=active_sessions,
        created_at=user.created_at.isoformat(),
    )


def _audit(db: Session, admin: User, action: str, target: User, detail: dict) -> None:
    db.add(
        AuditEvent(
            actor_user_id=admin.id,
            action=action,
            target_type="user",
            target_id=str(target.id),
            detail_json=detail,
        )
    )


@router.get("/users", response_model=list[AdminUserResponse])
def list_users(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    users = db.scalars(
        select(User).order_by(User.created_at.desc()).offset(offset).limit(limit)
    ).all()
    return [_user_response(db, user) for user in users]


@router.patch("/users/{user_id}", response_model=AdminUserResponse)
def update_user(
    user_id: UUID,
    payload: UpdateAdminUserRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if target.id == admin.id and (payload.disabled is True or payload.role == "user"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An administrator cannot disable or demote the current account",
        )
    changes: dict[str, object] = {}
    if payload.disabled is not None:
        target.disabled_at = now_utc() if payload.disabled else None
        changes["disabled"] = payload.disabled
        if payload.disabled:
            db.execute(delete(AuthSession).where(AuthSession.user_id == target.id))
    if payload.role is not None:
        if payload.role not in {"admin", "user"}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="role must be admin or user",
            )
        target.role = payload.role
        changes["role"] = payload.role
    if not changes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No user changes were provided",
        )
    _audit(db, admin, "admin.user.update", target, changes)
    db.commit()
    db.refresh(target)
    return _user_response(db, target)


@router.post("/users/{user_id}/revoke-sessions")
def revoke_user_sessions(
    user_id: UUID,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    result = db.execute(delete(AuthSession).where(AuthSession.user_id == target.id))
    _audit(db, admin, "admin.user.revoke_sessions", target, {"count": result.rowcount})
    db.commit()
    return {"ok": True, "revoked": result.rowcount}


@router.get("/metrics", response_model=AdminMetricsResponse)
def metrics(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    task_rows = db.execute(select(Task.status, func.count()).group_by(Task.status)).all()
    return AdminMetricsResponse(
        users=db.scalar(select(func.count()).select_from(User)) or 0,
        active_sessions=db.scalar(
            select(func.count()).select_from(AuthSession).where(
                AuthSession.expires_at > now_utc()
            )
        ) or 0,
        tasks_by_status={str(name): int(count) for name, count in task_rows},
        artifacts=db.scalar(select(func.count()).select_from(Artifact)) or 0,
        active_agent_turns=db.scalar(
            select(func.count()).select_from(AgentTurn).where(
                AgentTurn.status.in_(("queued", "running"))
            )
        ) or 0,
    )
