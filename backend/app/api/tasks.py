from uuid import UUID
from collections import defaultdict
from collections.abc import Sequence

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.config import get_settings
from app.db.models import (
    AgentSession,
    Artifact,
    Candidate,
    ResearchRun,
    Task,
    TaskRetry,
    User,
)
from app.db.session import get_db
from app.schemas.tasks import (
    ArtifactResponse,
    CreateTaskRequest,
    RetryTaskRequest,
    TaskResponse,
)
from app.tasks.service import (
    WORKER_TASK_NAMES,
    TaskQuotaExceeded,
    create_queued_task,
    create_retry_task,
)
from app.research.service import link_task_to_research_run, public_task_error_message


router = APIRouter(prefix="/api/tasks", tags=["tasks"])
RESEARCH_TASK_CONTEXT = {
    "search_sequence_homologs": ("homology_analysis", "homology"),
    "search_structure_homologs": ("homology_analysis", "homology"),
    "predict_binding_sites": ("binding_assessment", "binding"),
    "predict_interaction": ("binding_assessment", "binding"),
    "extract_empirical_features": ("binding_assessment", "binding"),
    "generate_coral_candidates": ("candidate_generation", "candidate_generation"),
    "generate_pepccd_candidates": ("candidate_generation", "candidate_generation"),
    "run_alphafold3": ("top10_af3", "af3"),
}


def serialize_artifact(artifact: Artifact) -> ArtifactResponse:
    return ArtifactResponse(
        id=str(artifact.id),
        kind=artifact.kind,
        filename=artifact.filename,
        mime_type=artifact.mime_type,
        size_bytes=artifact.size_bytes,
        download_url=f"/api/files/{artifact.id}/download",
    )


def public_task_error(task: Task) -> str | None:
    return public_task_error_message(task)


def redact_task_payload(payload: dict | None) -> dict | None:
    if not isinstance(payload, dict):
        return None
    blocked = {
        "api_key",
        "artifact_registration_error",
        "research_sync_error",
        "token",
        "secret",
        "password",
        "output_dir",
        "stdout_preview",
        "stderr",
    }

    def sensitive_key(key: str) -> bool:
        normalized = "".join(
            character for character in key.lower() if character.isalnum()
        )
        return (
            key.lower() in blocked
            or key.lower().endswith(("_path", "_dir"))
            or normalized in {"authorization", "cookie", "setcookie"}
            or normalized.endswith(
                (
                    "apikey",
                    "password",
                    "passwd",
                    "passphrase",
                    "dbpass",
                    "secret",
                    "secretaccesskey",
                    "token",
                    "accesstoken",
                    "privatekey",
                    "credential",
                    "credentials",
                    "dsn",
                    "databaseurl",
                    "connectionstring",
                )
            )
        )

    def redact(value):
        if isinstance(value, dict):
            return {
                key: redact(item)
                for key, item in value.items()
                if not sensitive_key(key)
            }
        if isinstance(value, list):
            return [redact(item) for item in value]
        return value

    return redact(payload)


def _task_response(
    task: Task,
    artifacts: Sequence[Artifact],
    retry_of: TaskRetry | None,
    retry_to: TaskRetry | None,
    *,
    include_details: bool = False,
) -> TaskResponse:
    return TaskResponse(
        id=str(task.id),
        session_id=str(task.session_id) if task.session_id else None,
        tool_call_id=task.tool_call_id,
        task_type=task.task_type,
        status=task.status,
        progress=task.progress,
        error_type=task.error_type,
        error_message=public_task_error(task),
        retry_of_task_id=(
            str(retry_of.parent_task_id) if retry_of is not None else None
        ),
        retry_task_id=(
            str(retry_to.child_task_id) if retry_to is not None else None
        ),
        input=redact_task_payload(task.input_json) if include_details else None,
        output=redact_task_payload(task.output_json) if include_details else None,
        artifacts=[serialize_artifact(item) for item in artifacts],
        created_at=task.created_at.isoformat(),
        updated_at=task.updated_at.isoformat(),
        started_at=task.started_at.isoformat() if task.started_at else None,
        finished_at=task.finished_at.isoformat() if task.finished_at else None,
    )


def serialize_tasks(
    db: Session, tasks: Sequence[Task], *, include_details: bool = False,
) -> list[TaskResponse]:
    if not tasks:
        return []
    task_ids = [task.id for task in tasks]
    artifacts_by_task = defaultdict(list)
    for artifact in db.scalars(
        select(Artifact).where(Artifact.task_id.in_(task_ids)).order_by(Artifact.created_at)
    ):
        artifacts_by_task[(artifact.task_id, artifact.user_id)].append(artifact)
    retries = db.scalars(select(TaskRetry).where(or_(
        TaskRetry.parent_task_id.in_(task_ids), TaskRetry.child_task_id.in_(task_ids),
    ))).all()
    retry_of = {retry.child_task_id: retry for retry in retries}
    retry_to = {retry.parent_task_id: retry for retry in retries}
    return [
        _task_response(
            task, artifacts_by_task[(task.id, task.user_id)],
            retry_of.get(task.id), retry_to.get(task.id), include_details=include_details,
        )
        for task in tasks
    ]


def serialize_task(db: Session, task: Task, *, include_details: bool = False) -> TaskResponse:
    return serialize_tasks(db, [task], include_details=include_details)[0]


@router.post("", response_model=TaskResponse)
def create_task(
    payload: CreateTaskRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if payload.task_type not in WORKER_TASK_NAMES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unsupported worker task type",
        )
    session_id = payload.session_id
    if session_id:
        session = db.get(AgentSession, session_id)
        if not session or session.user_id != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    research_run = None
    candidate = None
    raw_research_run_id = payload.input.get("research_run_id")
    if raw_research_run_id is not None:
        try:
            research_run_id = UUID(str(raw_research_run_id))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="research_run_id must be a UUID",
            ) from exc
        research_run = db.get(ResearchRun, research_run_id)
        if research_run is None or research_run.user_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Research run not found",
            )
        expected_stage, _role = RESEARCH_TASK_CONTEXT[payload.task_type]
        if research_run.current_stage != expected_stage:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Task type '{payload.task_type}' is only allowed during "
                    f"'{expected_stage}'"
                ),
            )
        raw_candidate_id = payload.input.get("candidate_id")
        if payload.task_type == "run_alphafold3":
            try:
                candidate_id = UUID(str(raw_candidate_id or ""))
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Research AF3 tasks require a valid candidate_id",
                ) from exc
            candidate = db.get(Candidate, candidate_id)
            if (
                candidate is None
                or candidate.user_id != user.id
                or candidate.research_run_id != research_run.id
            ):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Candidate not found",
                )
    try:
        task = create_queued_task(
            db,
            user,
            payload.task_type,
            payload.input,
            session_id=session_id,
            tool_call_id=payload.tool_call_id,
            commit=research_run is None,
        )
        if research_run is not None:
            _expected_stage, role = RESEARCH_TASK_CONTEXT[payload.task_type]
            link_task_to_research_run(
                db,
                user,
                research_run,
                task,
                role,
                candidate,
            )
            db.commit()
            db.refresh(task)
    except TaskQuotaExceeded as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": "30"},
        ) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return serialize_task(db, task)


@router.get("", response_model=list[TaskResponse])
def list_tasks(
    limit: int = Query(default=100, ge=1, le=200),
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
    return serialize_tasks(db, tasks)


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(
    task_id: UUID,
    include_details: bool = Query(default=False),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    task = db.get(Task, task_id)
    if not task or task.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    return serialize_task(db, task, include_details=include_details)


@router.post("/{task_id}/retry", response_model=TaskResponse)
def retry_task(
    task_id: UUID,
    payload: RetryTaskRequest | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    task = db.get(Task, task_id)
    if not task or task.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    if task.status != "failed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only failed tasks can be retried",
        )
    if task.task_type not in WORKER_TASK_NAMES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This task type is no longer supported by the worker",
        )
    attempt_no = int((task.output_json or {}).get("_worker_attempt", 0))
    if attempt_no >= get_settings().task_max_attempts:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Task retry limit has been reached",
        )
    retry_request = payload or RetryTaskRequest()
    try:
        new_task = create_retry_task(
            db,
            user,
            task,
            client_retry_id=retry_request.client_retry_id,
            reason="manual",
        )
        db.commit()
    except TaskQuotaExceeded as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": "30"},
        ) from exc
    except IntegrityError:
        db.rollback()
        retry_link = db.get(TaskRetry, task.id)
        new_task = (
            db.get(Task, retry_link.child_task_id)
            if retry_link is not None
            else None
        )
        if new_task is None or new_task.user_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Task retry conflicted with another request",
            )
    db.refresh(new_task)
    return serialize_task(db, new_task)


@router.get("/{task_id}/files", response_model=list[ArtifactResponse])
def get_task_files(task_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    task = db.get(Task, task_id)
    if not task or task.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    artifacts = db.scalars(
        select(Artifact).where(Artifact.task_id == task.id, Artifact.user_id == user.id)
    ).all()
    return [
        serialize_artifact(item)
        for item in artifacts
    ]
