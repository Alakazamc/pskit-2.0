from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

ProjectIcon = Literal[
    "folder", "flask", "atom", "dna", "microscope", "beaker",
    "book", "database", "cpu", "network", "sparkles", "layers",
    "graduation", "pencil", "code", "terminal", "music", "palette",
    "stethoscope", "briefcase", "chart", "scale", "globe", "wrench",
]


class Project(BaseModel):
    id: str
    name: str
    description: str
    icon: ProjectIcon = "folder"


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    icon: ProjectIcon = "folder"


class ProjectIconUpdate(BaseModel):
    icon: ProjectIcon


class ProjectRename(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class Session(BaseModel):
    id: str
    project_id: str
    title: str
    status: Literal["idle", "running", "waiting", "completed", "failed", "cancelled"] = "idle"
    latest_run_id: str | None = None
    title_status: Literal["idle", "pending", "generated", "failed", "manual"] = "manual"


class SessionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    auto_title: bool = False


class SessionRename(BaseModel):
    title: str = Field(min_length=1, max_length=160)


class SessionMove(BaseModel):
    project_id: str = Field(min_length=1)


class ProjectSkillSettings(BaseModel):
    skill_ids: list[str] = Field(default_factory=list)
    default_skill_ids: list[str] = Field(default_factory=list, max_length=3)


class TextPart(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ToolCallPart(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    tool_call_id: str | None = None
    tool: str
    status: str
    summary: str


class ToolResultPart(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str | None = None
    tool: str
    result: dict[str, Any]


class FilePart(BaseModel):
    type: Literal["file"] = "file"
    id: str
    name: str


class ArtifactPart(BaseModel):
    type: Literal["artifact"] = "artifact"
    id: str
    name: str
    kind: str


class CitationPart(BaseModel):
    type: Literal["citation"] = "citation"
    title: str
    url: str


class ProgressPart(BaseModel):
    type: Literal["progress"] = "progress"
    label: str
    value: int = Field(ge=0, le=100)


class ErrorPart(BaseModel):
    type: Literal["error"] = "error"
    code: str
    message: str


MessagePart = Annotated[
    TextPart | ToolCallPart | ToolResultPart | FilePart | ArtifactPart
    | CitationPart | ProgressPart | ErrorPart,
    Field(discriminator="type"),
]


class Message(BaseModel):
    id: str
    session_id: str
    role: Literal["user", "assistant"]
    parts: list[MessagePart]
    created_at: datetime


class ContextRef(BaseModel):
    id: str
    name: str


class MessageRequest(BaseModel):
    content: str = Field(min_length=1)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    reasoning_effort: Literal["off", "minimal", "low", "medium", "high", "xhigh", "max"] | None = None
    attachments: list[ContextRef] = Field(default_factory=list, max_length=10)
    skills: list[ContextRef] = Field(default_factory=list)
    resources: list[ContextRef] = Field(default_factory=list)


class RunRef(BaseModel):
    run_id: str


class RunStatus(BaseModel):
    run_id: str
    status: Literal["queued", "running", "waiting", "resume_queued", "completed", "failed", "cancelled"]


class ApprovalRef(BaseModel):
    approval_id: str
    status: Literal["approval_required"] = "approval_required"
    estimated_gpu_minutes: int


class ApprovalDecisionRequest(BaseModel):
    decision: Literal["approved", "rejected"]


class ApprovalDecisionResponse(BaseModel):
    approval_id: str
    status: Literal["approved", "rejected"]
    job_id: str | None


class EventBase(BaseModel):
    id: str = ""
    run_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class MessageDeltaData(BaseModel):
    delta: str


class MessageBoundaryData(BaseModel):
    role: Literal["assistant"] = "assistant"


class MessageStartEvent(EventBase):
    type: Literal["message.start"] = "message.start"
    data: MessageBoundaryData = Field(default_factory=MessageBoundaryData)


class MessageEndEvent(EventBase):
    type: Literal["message.end"] = "message.end"
    data: MessageBoundaryData = Field(default_factory=MessageBoundaryData)


class MessageDeltaEvent(EventBase):
    type: Literal["message.delta"] = "message.delta"
    data: MessageDeltaData


class ToolStartedData(BaseModel):
    tool_call_id: str
    tool: str


class ToolStartedEvent(EventBase):
    type: Literal["tool.started"] = "tool.started"
    data: ToolStartedData


class ToolUpdatedEvent(EventBase):
    type: Literal["tool.updated"] = "tool.updated"
    data: ToolStartedData


class ToolFinishedData(ToolStartedData):
    status: Literal["completed", "failed", "pending", "approval_required"]
    summary: str


class ToolFinishedEvent(EventBase):
    type: Literal["tool.finished"] = "tool.finished"
    data: ToolFinishedData


class PlanStep(BaseModel):
    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=160)
    status: Literal["pending", "in_progress", "completed", "blocked"]


class PlanSnapshot(BaseModel):
    steps: list[PlanStep] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_step_ids(self):
        """Require each plan step to have a unique stable ID.

        Returns:
            This validated plan snapshot.

        Raises:
            ValueError: Any two steps share an ID.
        """
        if len({step.id for step in self.steps}) != len(self.steps):
            raise ValueError("Plan step IDs must be unique")
        return self


class PlanCreatedEvent(EventBase):
    type: Literal["plan.created"] = "plan.created"
    data: PlanSnapshot


class PlanUpdatedEvent(EventBase):
    type: Literal["plan.updated"] = "plan.updated"
    data: PlanSnapshot


class RunCompletedData(BaseModel):
    status: Literal["completed"] = "completed"


class RunCompletedEvent(EventBase):
    type: Literal["run.completed"] = "run.completed"
    data: RunCompletedData


class RunFailedData(BaseModel):
    code: str
    message: str


class RunFailedEvent(EventBase):
    type: Literal["run.failed"] = "run.failed"
    data: RunFailedData


class RunCancelledData(BaseModel):
    status: Literal["cancelled"] = "cancelled"


class RunCancelledEvent(EventBase):
    type: Literal["run.cancelled"] = "run.cancelled"
    data: RunCancelledData


class RunRetryingData(BaseModel):
    attempt: int = Field(ge=1, le=100)
    max_attempts: int = Field(ge=1, le=100)
    delay_ms: int = Field(ge=0, le=60000)
    reset_message: bool = False


class RunRetryingEvent(EventBase):
    type: Literal["run.retrying"] = "run.retrying"
    data: RunRetryingData


class ApprovalRequiredData(BaseModel):
    approval_id: str
    capability: str
    estimated_gpu_minutes: int


class ApprovalRequiredEvent(EventBase):
    type: Literal["approval.required"] = "approval.required"
    data: ApprovalRequiredData


class ApprovalResolvedData(BaseModel):
    approval_id: str
    decision: Literal["approved", "rejected"]
    job_id: str | None = None


class ApprovalResolvedEvent(EventBase):
    type: Literal["approval.resolved"] = "approval.resolved"
    data: ApprovalResolvedData


class TaskUpdatedData(BaseModel):
    job_id: str
    label: str | None = None
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    progress: int


class TaskUpdatedEvent(EventBase):
    type: Literal["task.updated"] = "task.updated"
    data: TaskUpdatedData


class UsageUpdatedData(BaseModel):
    gpu_remaining: int


class UsageUpdatedEvent(EventBase):
    type: Literal["usage.updated"] = "usage.updated"
    data: UsageUpdatedData


class ArtifactCreatedData(BaseModel):
    artifact_id: str
    name: str
    kind: str


class ArtifactCreatedEvent(EventBase):
    type: Literal["artifact.created"] = "artifact.created"
    data: ArtifactCreatedData


RunEvent = (
    MessageStartEvent
    | MessageDeltaEvent
    | MessageEndEvent
    | ToolStartedEvent
    | ToolUpdatedEvent
    | ToolFinishedEvent
    | PlanCreatedEvent
    | PlanUpdatedEvent
    | RunCompletedEvent
    | RunFailedEvent
    | RunCancelledEvent
    | RunRetryingEvent
    | ApprovalRequiredEvent
    | ApprovalResolvedEvent
    | TaskUpdatedEvent
    | UsageUpdatedEvent
    | ArtifactCreatedEvent
)
