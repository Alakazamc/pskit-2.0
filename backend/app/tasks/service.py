import json
from uuid import UUID
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.locks import acquire_transaction_lock

from app.db.models import (
    Candidate,
    ResearchRun,
    ResearchTaskLink,
    Task,
    TaskRetry,
    User,
    now_utc,
)
from app.tasks.validation import validate_task_input


WORKER_TASK_NAMES = {
    "predict_binding_sites",
    "predict_interaction",
    "extract_empirical_features",
    "search_sequence_homologs",
    "search_structure_homologs",
    "generate_coral_candidates",
    "generate_pepccd_candidates",
    "run_alphafold3",
}

LONG_RUNNING_TOOL_NAMES = WORKER_TASK_NAMES | {"submit_research_top10_af3"}


class TaskQuotaExceeded(ValueError):
    pass


def _enforce_task_quotas(db: Session, user: User, task_type: str) -> None:
    settings = get_settings()
    active_statuses = ("queued", "running")
    global_active = db.scalar(
        select(func.count()).select_from(Task).where(Task.status.in_(active_statuses))
    ) or 0
    if global_active >= settings.max_global_active_tasks:
        raise TaskQuotaExceeded("The global scientific task queue is full")
    if task_type == "run_alphafold3":
        global_af3 = db.scalar(
            select(func.count()).select_from(Task).where(
                Task.task_type == "run_alphafold3",
                Task.status.in_(active_statuses),
            )
        ) or 0
        if global_af3 >= settings.max_global_active_af3:
            raise TaskQuotaExceeded("The global AlphaFold3 task queue is full")
        active = db.scalar(
            select(func.count()).select_from(Task).where(
                Task.user_id == user.id,
                Task.task_type == "run_alphafold3",
                Task.status.in_(active_statuses),
            )
        ) or 0
        if active >= settings.max_active_af3_per_user:
            raise TaskQuotaExceeded("Too many queued AlphaFold3 tasks for this user")
        return
    active = db.scalar(
        select(func.count()).select_from(Task).where(
            Task.user_id == user.id,
            Task.task_type != "run_alphafold3",
            Task.status.in_(active_statuses),
        )
    ) or 0
    if active >= settings.max_active_tasks_per_user:
        raise TaskQuotaExceeded("Too many active scientific tasks for this user")


def create_queued_task(
    db: Session,
    user: User,
    task_type: str,
    input_json: dict,
    session_id: UUID | None = None,
    tool_call_id: str | None = None,
    *,
    operation_id: str | None = None,
    commit: bool = True,
    deduplicate: bool = True,
) -> Task:
    if task_type not in WORKER_TASK_NAMES:
        raise ValueError(f"Unsupported worker task type: {task_type}")
    input_json = validate_task_input(task_type, input_json)
    acquire_transaction_lock(db, "tasks.admission")
    deterministic_id = None
    research_run_id = input_json.get("research_run_id")
    if deduplicate and (research_run_id or operation_id):
        canonical_input = json.dumps(
            input_json,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        identity_scope = (
            str(research_run_id)
            if research_run_id
            else f"operation:{operation_id}"
        )
        deterministic_id = uuid5(
            NAMESPACE_URL,
            f"pskit-task:{user.id}:{task_type}:{identity_scope}:{canonical_input}",
        )
        existing = db.get(Task, deterministic_id)
        if existing is not None:
            if existing.user_id != user.id or existing.task_type != task_type:
                raise ValueError("Deterministic task identity collision")
            return existing
    _enforce_task_quotas(db, user, task_type)
    task = Task(
        id=deterministic_id,
        user_id=user.id,
        session_id=session_id,
        tool_call_id=tool_call_id,
        task_type=task_type,
        status="queued",
        progress=0.0,
        input_json=input_json,
        output_json={},
    )
    db.add(task)
    if commit:
        db.commit()
        db.refresh(task)
    else:
        db.flush()
    return task


def task_to_result(task: Task) -> dict:
    return {
        "task_id": str(task.id),
        "task_type": task.task_type,
        "status": task.status,
        "progress": task.progress,
        "message": "Task has been queued for worker execution.",
    }


def create_retry_task(
    db: Session,
    user: User,
    parent: Task,
    *,
    client_retry_id: UUID,
    reason: str,
) -> Task:
    """为失败尝试创建唯一后继；重复请求返回同一后继任务。"""

    if parent.user_id != user.id:
        raise ValueError("Task retry owner mismatch")
    acquire_transaction_lock(db, "tasks.admission")
    existing_retry = db.get(TaskRetry, parent.id)
    if existing_retry is not None:
        existing_child = db.get(Task, existing_retry.child_task_id)
        if existing_child is None or existing_child.user_id != user.id:
            raise ValueError("Retry chain is inconsistent")
        return existing_child
    _enforce_task_quotas(db, user, parent.task_type)
    attempt_no = int((parent.output_json or {}).get("_worker_attempt", 0))
    child_id = uuid5(NAMESPACE_URL, f"pskit-task-retry:{parent.id}")
    child = Task(
        id=child_id,
        user_id=user.id,
        session_id=parent.session_id,
        tool_call_id=parent.tool_call_id,
        task_type=parent.task_type,
        status="queued",
        progress=0.0,
        input_json=dict(parent.input_json or {}),
        output_json={
            "_retry_of_task_id": str(parent.id),
            "_retry_reason": reason,
            "_worker_attempt": attempt_no,
        },
    )
    db.add(child)
    db.flush()
    db.add(
        TaskRetry(
            parent_task_id=parent.id,
            child_task_id=child.id,
            client_retry_id=client_retry_id,
            reason=reason,
        )
    )
    link = db.scalar(
        select(ResearchTaskLink).where(ResearchTaskLink.task_id == parent.id)
    )
    if link is not None:
        db.add(
            ResearchTaskLink(
                research_run_id=link.research_run_id,
                task_id=child.id,
                candidate_id=link.candidate_id,
                user_id=user.id,
                role=link.role,
            )
        )
        if link.role == "af3" and link.candidate_id is not None:
            candidate = db.get(Candidate, link.candidate_id)
            if candidate is not None and candidate.user_id == user.id:
                candidate.af3_task_id = child.id
                candidate.af3_status = "queued"
                candidate.updated_at = now_utc()
        from app.research.service import refresh_research_run_progress

        research_run = db.get(ResearchRun, link.research_run_id)
        if research_run is not None:
            refresh_research_run_progress(
                db,
                research_run,
                allow_auto_advance=False,
            )
    db.flush()
    return child
