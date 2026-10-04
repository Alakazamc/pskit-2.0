"""Typed control-plane contracts for user CPU sandboxes."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Identity = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
PositiveSeconds = Annotated[int, Field(strict=True, gt=0)]
Revision = Annotated[int, Field(strict=True, ge=0)]


class SandboxLease(BaseModel):
    lease_id: str
    owner_id: Identity
    session_id: Identity
    run_id: Identity
    fencing_token: PositiveSeconds
    lease_seconds: PositiveSeconds
    expires_at: float
    state: Literal["active", "unknown", "released"] = "active"
    purpose: Literal["pi", "transfer"] = "pi"


class SandboxActivity(BaseModel):
    owner_id: Identity
    leases: list[SandboxLease] = Field(default_factory=list)
    state: Literal["idle", "active", "unknown"]
    last_completed_at: float


class SandboxPromptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: Identity
    session_id: Identity
    attempt_id: Identity
    message: str
    model: str = Field(min_length=1)
    session_file: str | None = None
    legacy_session_b64: str | None = None
    environment: dict[str, str] = Field(default_factory=dict)
    allow_handled: bool = False
    system_prompt_suffix: str = ""
    images: list[dict[str, str]] = Field(default_factory=list, max_length=10)


class SandboxAttemptStatus(BaseModel):
    attempt_id: Identity
    session_id: Identity
    state: Literal["queued", "running", "cancelling", "cancelled", "completed", "failed", "unknown"]
    exited: bool = False


class SandboxSummary(BaseModel):
    owner_id: Identity
    instance_id: str
    volume_id: str
    image_digest: str
    state: Literal["ready", "draining", "replacing", "error"]
    runtime_state: Literal["running", "stopped", "unknown", "stopping"]
    active_sessions: list[str] = Field(default_factory=list)
    revision: Revision
    last_completed_at: float


class SandboxOperation(BaseModel):
    operation_id: str
    owner_id: Identity
    kind: Literal["drain", "replace"]
    state: Literal["draining", "waiting", "completed", "failed"]
    revision: Revision
    image_digest: str | None = None


class SandboxMetrics(BaseModel):
    owner_id: Identity
    cpu_core_ms: Annotated[int, Field(strict=True, ge=0)] | None
    memory_bytes: Annotated[int, Field(strict=True, ge=0)] | None
    storage_bytes: Annotated[int, Field(strict=True, ge=0)] | None = None
    sampled_at: float
    scope: Literal["owner"] = "owner"


class WorkspaceFileRef(BaseModel):
    id: str
    name: str
    relative_path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: Annotated[int, Field(strict=True, ge=0)]
