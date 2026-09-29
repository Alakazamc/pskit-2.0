from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class CreateTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_type: str = Field(min_length=1, max_length=80)
    session_id: UUID | None = None
    tool_call_id: str | None = Field(default=None, max_length=200)
    input: dict = Field(default_factory=dict)


class ArtifactResponse(BaseModel):
    id: str
    kind: str
    filename: str
    mime_type: str | None = None
    size_bytes: int | None = None
    download_url: str


class RetryTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_retry_id: UUID = Field(default_factory=uuid4)


class TaskResponse(BaseModel):
    id: str
    session_id: str | None
    tool_call_id: str | None
    task_type: str
    status: str
    progress: float
    error_type: str | None = None
    error_message: str | None = None
    retry_of_task_id: str | None = None
    retry_task_id: str | None = None
    input: dict | None = None
    output: dict | None = None
    artifacts: list[ArtifactResponse]
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None
