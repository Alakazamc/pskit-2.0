"""Secret-free management API contracts shared with the React client."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.contracts.compute import CapabilityVersion, ResourceCounter, UsageReport
from app.services.model_catalog import ModelOption

Role = Literal["platform_admin", "service_maintainer", "quota_operator", "auditor"]


class AdminContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AdminMe(AdminContract):
    user_id: str
    roles: list[str]
    permissions: list[str]
    service_ids: list[str]


class AdminPage[T](AdminContract):
    items: list[T]
    next_cursor: str | None = None


class RevisionRequest(AdminContract):
    expected_revision: int = Field(ge=0)
    reason: str = Field(min_length=5, max_length=500)


class ModelDraft(RevisionRequest):
    allowed_user_ids: list[str] = Field(default_factory=list)
    allowed_group_ids: list[str] = Field(default_factory=list)
    purposes: list[Literal["chat", "analysis"]] = Field(default_factory=lambda: ["chat"])
    supports_images: bool = False
    reasoning_levels: list[Literal["off", "minimal", "low", "medium", "high", "xhigh", "max"]] = (
        Field(default_factory=list)
    )
    default_for_purposes: list[Literal["chat", "analysis"]] = Field(default_factory=list)


class AdminModel(AdminContract):
    id: str
    revision: int = 0
    state: Literal["draft", "published", "retired"] = "draft"
    gateway_available: bool
    gateway: ModelOption
    draft: ModelDraft | None = None
    published: ModelDraft | None = None


class ServiceDraft(RevisionRequest):
    name: str = Field(min_length=1, max_length=120)
    owner_user_id: str = Field(min_length=1, max_length=200)
    transport: Literal["http", "mcp", "worker_pull"]
    endpoint_ref: str = Field(min_length=1, max_length=200)
    credential_ref: str | None = Field(default=None, max_length=200)
    model_version: str = Field(min_length=1, max_length=200)
    capabilities: list[CapabilityVersion] = Field(min_length=1, max_length=64)


class AdminService(AdminContract):
    service_id: str
    revision: int
    state: Literal["draft", "validated", "published", "retired"]
    name: str
    owner_user_id: str
    transport: Literal["http", "mcp", "worker_pull"]
    endpoint_ref: str
    credential_ref: str | None = None
    model_version: str
    capabilities: list[CapabilityVersion]
    published_revision: int | None = None
    schema_digest: str | None = None


class ServiceCheckRequest(RevisionRequest):
    kind: Literal["connectivity", "schema"] = "connectivity"


class ServiceCheck(AdminContract):
    service_id: str
    revision: int
    kind: str
    status: Literal["passed", "failed"]
    checked_at: str
    message: str
    schema_digest: str | None = None
    discovered_capabilities: list[dict[str, Any]] = Field(default_factory=list)


class ReleaseServiceVersion(AdminContract):
    service_id: str
    revision: int = Field(gt=0)


class ConfigReleaseRequest(RevisionRequest):
    services: list[ReleaseServiceVersion] = Field(min_length=1, max_length=64)


class ConfigRelease(AdminContract):
    release_id: str
    revision: int
    state: Literal["draft", "published"]
    services: list[ReleaseServiceVersion]
    impact: list[str]


class LimitsUpdate(RevisionRequest):
    token_monthly_limit: int = Field(ge=0)
    gpu_daily_minutes: int = Field(ge=0, le=1440)
    cpu_daily_core_ms: int = Field(default=0, ge=0)
    concurrency_limit: int = Field(default=1, ge=1, le=1000)
    storage_limit_bytes: int = Field(default=0, ge=0)


class AdminUser(AdminContract):
    user_id: str
    revision: int
    tier: str
    token_monthly_limit: int
    gpu_daily_minutes: int
    cpu_daily_core_ms: int
    concurrency_limit: int
    storage_limit_bytes: int | None
    tokens: ResourceCounter
    gpu: ResourceCounter
    cpu: ResourceCounter


class AdminJob(AdminContract):
    job_id: str
    user_id: str
    service_id: str | None = None
    capability_id: str | None = None
    status: str
    accounting_status: str
    progress: int
    revision: int
    cancellation_state: Literal["none", "requested", "confirmed"]


class AdminOperation(AdminContract):
    operation_id: str
    resource_id: str
    kind: str
    state: Literal["requested", "confirmed", "failed"]
    revision: int


class ReconcileRequest(RevisionRequest):
    usage: UsageReport
    terminal_status: Literal["completed", "failed", "cancelled"]
    stopped: Literal[True]
    evidence: str = Field(min_length=5, max_length=2000)


class LegacyAf3ReconcileRequest(RevisionRequest):
    actual_gpu_minutes: int = Field(ge=0, le=1440, strict=True)
    stopped: Literal[True]
    evidence: str = Field(min_length=5, max_length=2000)
    source: Literal["legacy_wall"] = "legacy_wall"


class AuditEvent(AdminContract):
    event_id: str
    actor_user_id: str
    action: str
    resource_id: str
    reason: str
    request_id: str
    created_at: str
    before: dict[str, Any]
    after: dict[str, Any]
