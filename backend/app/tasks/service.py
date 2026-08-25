import json
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import Task, User

LONG_RUNNING_TOOL_NAMES = {
    "predict_binding_sites",
    "predict_interaction",
    "extract_empirical_features",
    "run_alphafold3",
    "coral_mcp__predict",
    "pepccd_mcp__generate",
    "remote_rna_expert__generate_rna_for_protein",
}

MAX_TASK_INPUT_BYTES = 1_000_000
MAX_SEQUENCE_LENGTH = 20_000


class TaskValidationError(ValueError):
    pass


def _require_string(value: object, name: str, max_length: int = MAX_SEQUENCE_LENGTH) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TaskValidationError(f"{name} is required")
    normalized = "".join(value.split()).upper()
    if len(normalized) > max_length:
        raise TaskValidationError(f"{name} exceeds {max_length} characters")
    return normalized


def validate_task_input(task_type: str, input_json: dict) -> dict:
    if task_type not in LONG_RUNNING_TOOL_NAMES:
        raise TaskValidationError(f"Unsupported task type: {task_type}")
    try:
        encoded = json.dumps(input_json, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TaskValidationError("Task input must be JSON serializable") from exc
    if len(encoded) > MAX_TASK_INPUT_BYTES:
        raise TaskValidationError(f"Task input exceeds {MAX_TASK_INPUT_BYTES} bytes")

    normalized = dict(input_json)
    if task_type in {"predict_binding_sites", "extract_empirical_features"}:
        artifact_id = _require_string(normalized.get("artifact_id"), "artifact_id", 64)
        normalized["artifact_id"] = artifact_id
        normalized.pop("pdb_path", None)
    elif task_type == "predict_interaction":
        normalized["protein_sequence"] = _require_string(
            normalized.get("protein_sequence") or normalized.get("protein_seq"),
            "protein_sequence",
        )
        normalized["nucleic_sequence"] = _require_string(
            normalized.get("nucleic_sequence") or normalized.get("nucleic_acid_seq"),
            "nucleic_sequence",
        )
    elif task_type == "run_alphafold3":
        entities = normalized.get("entities")
        if not isinstance(entities, list) or not entities or len(entities) > 32:
            raise TaskValidationError("entities must contain between 1 and 32 items")
        total_length = 0
        for index, entity in enumerate(entities):
            if not isinstance(entity, dict) or entity.get("type") not in {"protein", "rna", "dna"}:
                raise TaskValidationError(f"entities[{index}].type must be protein, rna, or dna")
            total_length += len(
                _require_string(entity.get("sequence"), f"entities[{index}].sequence")
            )
        if total_length > MAX_SEQUENCE_LENGTH:
            raise TaskValidationError(f"Total entity sequence length exceeds {MAX_SEQUENCE_LENGTH}")
        samples = int(normalized.get("num_diffusion_samples", 5))
        if not 1 <= samples <= 20:
            raise TaskValidationError("num_diffusion_samples must be between 1 and 20")
        normalized["num_diffusion_samples"] = samples
    elif task_type in {"coral_mcp__predict", "pepccd_mcp__generate"}:
        arguments = normalized.get("arguments")
        if not isinstance(arguments, dict) or not arguments:
            raise TaskValidationError("arguments must be a non-empty object")
        normalized = {"arguments": arguments}
    elif task_type == "remote_rna_expert__generate_rna_for_protein":
        pdb_id = _require_string(normalized.get("pdb_id"), "pdb_id", 4)
        if len(pdb_id) != 4 or not pdb_id.isalnum():
            raise TaskValidationError("pdb_id must be a 4-character alphanumeric ID")
        normalized["pdb_id"] = pdb_id
        samples = int(normalized.get("num_samples", 3))
        if not 1 <= samples <= 50:
            raise TaskValidationError("num_samples must be between 1 and 50")
        normalized["num_samples"] = samples
    return normalized


def create_queued_task(
    db: Session,
    user: User,
    task_type: str,
    input_json: dict,
    session_id: UUID | None = None,
    tool_call_id: str | None = None,
) -> Task:
    input_json = validate_task_input(task_type, input_json)
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
        "message": "Task has been queued for background worker execution.",
    }
