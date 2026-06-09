from pydantic import BaseModel, Field


class CreateTaskRequest(BaseModel):
    task_type: str
    session_id: str | None = None
    tool_call_id: str | None = None
    input: dict = Field(default_factory=dict)


class TaskResponse(BaseModel):
    id: str
    task_type: str
    status: str
    progress: float
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
