from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.models import AgentSession, Artifact, Task, User
from app.db.session import get_db
from app.schemas.tasks import ArtifactResponse, CreateTaskRequest, TaskResponse
from app.tasks.service import TaskValidationError, create_queued_task

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def serialize_task(task: Task) -> TaskResponse:
    return TaskResponse(
        id=str(task.id),
        task_type=task.task_type,
        status=task.status,
        progress=task.progress,
        attempt_count=task.attempt_count,
        error_type=task.error_type,
        error_message=task.error_message,
        input=task.input_json,
        output=task.output_json,
    )


@router.post("", response_model=TaskResponse)
def create_task(
    payload: CreateTaskRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    session_id = UUID(payload.session_id) if payload.session_id else None
    if session_id:
        session = db.get(AgentSession, session_id)
        if not session or session.user_id != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    try:
        task = create_queued_task(
            db,
            user,
            payload.task_type,
            payload.input,
            session_id=session_id,
            tool_call_id=payload.tool_call_id,
        )
    except TaskValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return serialize_task(task)


@router.get("", response_model=list[TaskResponse])
def list_tasks(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    tasks = db.scalars(
        select(Task)
        .where(Task.user_id == user.id)
        .order_by(Task.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return [serialize_task(task) for task in tasks]


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(task_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    task = db.get(Task, task_id)
    if not task or task.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return serialize_task(task)


@router.get("/{task_id}/files", response_model=list[ArtifactResponse])
def get_task_files(
    task_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    task = db.get(Task, task_id)
    if not task or task.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    artifacts = db.scalars(
        select(Artifact).where(Artifact.task_id == task.id, Artifact.user_id == user.id)
    ).all()
    return [
        ArtifactResponse(
            id=str(item.id),
            kind=item.kind,
            filename=item.filename,
            mime_type=item.mime_type,
            size_bytes=item.size_bytes,
        )
        for item in artifacts
    ]
