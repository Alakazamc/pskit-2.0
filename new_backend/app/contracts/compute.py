"""Versioned business protocol shared by the API and the model SDK."""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from app.contracts.catalog import ArtifactRef

Count = Annotated[StrictInt, Field(ge=0)]
Positive = Annotated[StrictInt, Field(gt=0)]
ComputeJobStatus = Literal["queued", "running", "cancelling", "completed", "failed", "cancelled"]
Metric = Literal["wall_ms", "cpu_core_ms", "gpu_device_ms", "peak_memory_bytes",
                 "peak_gpu_memory_bytes", "gpu_count"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UsageReport(Contract):
    wall_ms: Count | None = None
    cpu_core_ms: Count | None = None
    gpu_device_ms: Count | None = None
    peak_memory_bytes: Count | None = None
    peak_gpu_memory_bytes: Count | None = None
    gpu_count: Count | None = None
    source: Literal["service_reported", "measured", "estimated", "unknown"]


class ExecutionError(Contract):
    code: str = Field(min_length=1, max_length=100)
    message: str = Field(max_length=2000)


class Completed(Contract):
    status: Literal["completed"] = "completed"
    result: dict[str, Any]
    usage: UsageReport
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    job_id: str | None = None


class Pending(Contract):
    status: Literal["pending"] = "pending"
    job_id: str = Field(min_length=1, max_length=200)


class Failed(Contract):
    status: Literal["failed"] = "failed"
    error: ExecutionError
    usage: UsageReport
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    job_id: str | None = None


ExecutionReport = Annotated[Completed | Pending | Failed, Field(discriminator="status")]
ExecutionResult = ExecutionReport


class ComputeBudget(Contract):
    cpu_core_ms: Count = 0
    gpu_device_ms: Count = 0


class CapabilityVersion(Contract):
    id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,120}$")
    version: str = Field(min_length=1, max_length=100)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    required_usage: list[Metric] = Field(default_factory=list)
    accepted_sources: list[Literal["service_reported", "measured", "estimated", "unknown"]] = Field(
        default_factory=lambda: ["service_reported", "measured"], min_length=1,
    )
    visibility: Literal["draft", "published"] = "draft"
    allowed_users: list[str] | None = None
    gpu_count: Count = 0
    max_budget: ComputeBudget = Field(default_factory=ComputeBudget)
    concurrency: Positive = 1
    max_execution_seconds: Positive = 1800
    cancellation: Literal["none", "cooperative", "confirmed_stop"] = "cooperative"
    limit_mode: Literal["soft", "hard"] = "soft"
    exclusive_process: bool = False

    @model_validator(mode="after")
    def honest_limits(self):
        if self.limit_mode == "hard" and (
            self.cancellation != "confirmed_stop" or not self.exclusive_process
        ):
            raise ValueError("Hard limits require an independently stoppable executor")
        if self.gpu_count and "gpu_device_ms" not in self.required_usage:
            raise ValueError("GPU capabilities must require gpu_device_ms")
        if self.max_budget.gpu_device_ms and "gpu_device_ms" not in self.required_usage:
            raise ValueError("GPU budgets must require gpu_device_ms")
        if self.max_budget.cpu_core_ms and "cpu_core_ms" not in self.required_usage:
            raise ValueError("CPU budgets must require cpu_core_ms")
        return self


class ComputeServiceManifest(Contract):
    schema_version: Literal["pskit.compute.v1"] = "pskit.compute.v1"
    service_id: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,120}$")
    model_version: str = Field(min_length=1)
    capabilities: list[CapabilityVersion] = Field(min_length=1)


class ComputeJobRequest(Contract):
    capability_id: str
    version: str
    arguments: dict[str, Any]
    budget: ComputeBudget = Field(default_factory=ComputeBudget)


class ComputeJob(Contract):
    id: str
    user_id: str
    service_id: str
    capability: CapabilityVersion
    arguments: dict[str, Any]
    budget: ComputeBudget
    status: ComputeJobStatus
    accounting_status: Literal["reserved", "settled", "released", "pending_reconciliation"]
    progress: Count = 0
    run_id: str | None = None
    tool_call_id: str | None = None
    report: ExecutionReport | None = None


class ExecutionResultMapping(Contract):
    kind: Literal["json_pointer"] = "json_pointer"
    pointer: str = Field(max_length=500)
    renames: dict[str, str] = Field(default_factory=dict)
    transforms: list[dict[str, Any]] = Field(default_factory=list, max_length=10)


class ExecutionBindingSnapshot(Contract):
    """Private immutable transport policy delivered only to trusted workers."""

    adapter: Literal["immediate_mcp", "job_mcp", "mcp_tasks"]
    endpoint_url: str = Field(min_length=1, max_length=2000)
    credential_ref: str | None = Field(default=None, max_length=200)
    submit_tool: str = Field(min_length=1, max_length=200)
    status_tool: str | None = Field(default=None, max_length=200)
    cancel_tool: str | None = Field(default=None, max_length=200)
    remote_output_schema: dict[str, Any]
    result_mapping: ExecutionResultMapping


class ComputeJobSummary(Contract):
    """Owned history without potentially large sequence reports or worker details."""

    id: str
    capability_id: str
    version: str
    arguments: dict[str, Any]
    status: ComputeJobStatus
    progress: Count
    created_at: datetime


class WorkerIdentity(Contract):
    service_id: str
    worker_id: str = Field(min_length=1, max_length=120)


class WorkerResources(Contract):
    gpu_uuids: list[str] = Field(default_factory=list, max_length=64)


class ComputeClaimRequest(WorkerIdentity):
    resources: WorkerResources = Field(default_factory=WorkerResources)


class ExecutionGrant(Contract):
    job: ComputeJob
    worker_id: str
    attempt: Positive
    fencing_token: str
    stop_at: datetime
    lease_expires_at: datetime
    gpu_uuids: list[str] = Field(default_factory=list)
    execution_binding: ExecutionBindingSnapshot | None = None
    recovered: bool = False


class UsageWindow(Contract):
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def valid_interval(self):
        if (self.start.tzinfo is None or self.end.tzinfo is None or self.end < self.start):
            raise ValueError("Usage windows require ordered timezone-aware timestamps")
        return self


class ComputeResultRequest(Contract):
    worker_id: str
    attempt: Positive
    fencing_token: str
    seq: Positive
    report: ExecutionReport
    window: UsageWindow | None = None
    stopped: bool


class ComputeHeartbeatRequest(Contract):
    worker_id: str
    attempt: Positive
    fencing_token: str
    seq: Positive
    progress: Annotated[StrictInt, Field(ge=0, le=100)] = 0
    usage: UsageReport | None = None
    window: UsageWindow | None = None


class UsageReceipt(Contract):
    receipt_id: str
    job_id: str
    accepted_seq: Positive
    payload_hash: str
    status: Literal["completed", "failed", "cancelled", "pending"]
    committed: Literal[True] = True


class GrantUpdate(Contract):
    stop_at: datetime
    lease_expires_at: datetime
    cancel_requested: bool


class ResourceCounter(Contract):
    limit: Count
    used: Count
    reserved: Count
    remaining: Count


class ComputeUsage(Contract):
    day: str
    cpu: ResourceCounter
    gpu: ResourceCounter
    sources: list[str]
    allocation_policy: str = "proportional_usage_window"


class Reservation(Contract):
    job_id: str
    budget: ComputeBudget
    period: str


class ReceiverOutcome(Contract):
    status: Literal["idle", "acknowledged", "pending", "unknown"]
    job_id: str | None = None


class InternalComputeSubmit(ComputeJobRequest):
    run_id: str
    tool_call_id: str = Field(min_length=1, max_length=200)


class ComputeResumeContext(Contract):
    job: ComputeJob
    related: list[ComputeJob]
