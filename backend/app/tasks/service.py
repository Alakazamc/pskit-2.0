from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import Task, User


LONG_RUNNING_TOOL_NAMES = {
    "predict_binding_sites",
    "predict_interaction",
    "extract_empirical_features",
    "run_alphafold3",
    "remote_rna_expert__generate_rna_for_protein",
}


def create_queued_task(
    db: Session,
    user: User,
    task_type: str,
    input_json: dict,
    session_id: UUID | None = None,
    tool_call_id: str | None = None,
) -> Task:
    task = Task(
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
    db.commit()
    db.refresh(task)
    return task


def task_to_result(task: Task) -> dict:
    return {
        "task_id": str(task.id),
        "task_type": task.task_type,
        "status": task.status,
        "progress": task.progress,
        "message": "Task has been queued. Worker execution will be implemented in the next phase.",
    }

