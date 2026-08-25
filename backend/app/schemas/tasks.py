from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TaskType = Literal[
    "predict_binding_sites",
    "predict_interaction",
    "extract_empirical_features",
    "run_alphafold3",
    "coral_mcp__predict",
    "pepccd_mcp__generate",
    "remote_rna_expert__generate_rna_for_protein",
]


class CreateTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_type: TaskType
    session_id: str | None = None
    tool_call_id: str | None = None
    input: dict = Field(default_factory=dict)


class TaskResponse(BaseModel):
    id: str
    task_type: str
    status: str
    progress: float
    attempt_count: int = 0
    error_type: str | None = None
    error_message: str | None = None
    input: dict | None = None
    output: dict | None = None


class ArtifactResponse(BaseModel):
    id: str
    kind: str
    filename: str
    mime_type: str | None = None
    size_bytes: int | None = None
