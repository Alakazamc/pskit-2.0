"""Strict, versioned contracts for config-driven scientific Tool Products."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.contracts.catalog import ArtifactRef
from app.contracts.compute import ComputeBudget, Metric, UsageReport

Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,119}$")]
JsonSchema = dict[str, Any]
FieldComponent = Literal[
    "text-input",
    "number-input",
    "textarea",
    "select",
    "segmented-control",
    "checkbox",
    "switch",
    "file-upload",
    "protein-input",
    "sequence-input",
    "parameter-group",
    "advanced-section",
]
ResultComponent = Literal[
    "metric-grid",
    "stage-flow",
    "sequence-table",
    "data-table",
    "line-chart",
    "scatter-plot",
    "heatmap",
    "structure-viewer",
    "artifact-list",
    "json-inspector",
]


class ToolProductContract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, populate_by_name=True)


class LocalizedText(ToolProductContract):
    en: str = Field(min_length=1, max_length=2000)
    zh_cn: str = Field(alias="zh-CN", min_length=1, max_length=2000)


class ToolUiProduct(ToolProductContract):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    title: LocalizedText
    description: LocalizedText


class ToolUiPageLayout(ToolProductContract):
    layout: Literal["split-workspace", "single-column"]
    input_width: int = Field(default=5, ge=3, le=9)
    result_width: int = Field(default=7, ge=3, le=9)

    @model_validator(mode="after")
    def widths_fit_grid(self):
        if self.layout == "split-workspace" and self.input_width + self.result_width != 12:
            raise ValueError("split-workspace widths must add up to 12")
        return self


class ToolUiOption(ToolProductContract):
    value: str | int | bool
    label: LocalizedText


class ToolUiCondition(ToolProductContract):
    source: str | None = None
    equals: Any = None
    in_values: list[Any] | None = Field(default=None, alias="in", max_length=100)
    exists: bool | None = None
    and_conditions: list[ToolUiCondition] | None = Field(default=None, alias="and", min_length=1)
    or_conditions: list[ToolUiCondition] | None = Field(default=None, alias="or", min_length=1)
    not_condition: ToolUiCondition | None = Field(default=None, alias="not")

    @model_validator(mode="after")
    def one_allowlisted_predicate(self):
        selected = [
            "equals" in self.model_fields_set,
            self.in_values is not None,
            self.exists is not None,
            self.and_conditions is not None,
            self.or_conditions is not None,
            self.not_condition is not None,
        ]
        if sum(selected) != 1:
            raise ValueError("condition must use exactly one allowlisted predicate")
        scalar = selected[0] or selected[1] or selected[2]
        if scalar and not self.source:
            raise ValueError("scalar condition requires a source JSON Pointer")
        if not scalar and self.source is not None:
            raise ValueError("compound condition cannot declare a source")
        return self


class ToolUiField(ToolProductContract):
    id: Identifier
    component: FieldComponent
    label: LocalizedText
    input_pointer: str | None = None
    help: LocalizedText | None = None
    required: bool = False
    default: Any = None
    options: list[ToolUiOption] = Field(default_factory=list, max_length=200)
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = Field(default=None, gt=0)
    accepted_types: list[str] = Field(default_factory=list, max_length=50)
    max_files: int | None = Field(default=None, ge=1, le=100)
    visible_when: ToolUiCondition | None = None
    fields: list[ToolUiField] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def group_shape(self):
        is_group = self.component in {"parameter-group", "advanced-section"}
        if is_group and not self.fields:
            raise ValueError("parameter groups require child fields")
        if not is_group and self.fields:
            raise ValueError("only parameter groups may contain child fields")
        if not is_group and not self.input_pointer:
            raise ValueError("input fields require an input_pointer")
        return self


class ToolUiSection(ToolProductContract):
    id: Identifier
    title: LocalizedText
    description: LocalizedText | None = None
    visible_when: ToolUiCondition | None = None
    fields: list[ToolUiField] = Field(min_length=1, max_length=100)


class ToolUiTransform(ToolProductContract):
    name: Literal["identity", "limit", "sort", "number-format", "rename-fields"]
    limit: int | None = Field(default=None, ge=1, le=10_000)
    field: str | None = Field(default=None, max_length=200)
    direction: Literal["asc", "desc"] | None = None
    digits: int | None = Field(default=None, ge=0, le=12)
    renames: dict[str, str] = Field(default_factory=dict, max_length=100)


class ToolUiResultView(ToolProductContract):
    id: Identifier
    component: ResultComponent
    source: str
    title: LocalizedText | None = None
    empty: LocalizedText | None = None
    preview_limit: int = Field(default=100, ge=1, le=10_000)
    full_data_artifact: str | None = None
    visible_when: ToolUiCondition | None = None
    transforms: list[ToolUiTransform] = Field(default_factory=list, max_length=10)


class DirectActionTarget(ToolProductContract):
    action_id: Identifier


class StateActionSelection(ToolProductContract):
    source: str
    map: dict[str, Identifier] = Field(min_length=1, max_length=100)


class StateActionTarget(ToolProductContract):
    by_state: StateActionSelection


ToolUiActionTarget = DirectActionTarget | StateActionTarget


class ToolUiAction(ToolProductContract):
    id: Identifier
    label: LocalizedText
    kind: Literal["start_run"] = "start_run"
    target: ToolUiActionTarget
    visible_when: ToolUiCondition | None = None


class ToolUiHandoff(ToolProductContract):
    id: Identifier
    label: LocalizedText
    summary_source: str
    artifact_sources: list[str] = Field(default_factory=list, max_length=20)


class ToolUiSchema(ToolProductContract):
    schema_version: Literal["pskit.tool-ui.v1"] = "pskit.tool-ui.v1"
    product: ToolUiProduct
    page: ToolUiPageLayout
    state: dict[str, dict[str, Any]] = Field(default_factory=dict, max_length=100)
    sections: list[ToolUiSection] = Field(default_factory=list, max_length=100)
    actions: list[ToolUiAction] = Field(min_length=1, max_length=100)
    result_views: list[ToolUiResultView] = Field(default_factory=list, max_length=100)
    handoffs: list[ToolUiHandoff] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def unique_ui_ids(self):
        def require_unique(name: str, values: list[str]):
            if len(values) != len(set(values)):
                raise ValueError(f"{name} IDs must be unique")

        require_unique("section", [item.id for item in self.sections])
        require_unique("action", [item.id for item in self.actions])
        require_unique("result view", [item.id for item in self.result_views])
        require_unique("handoff", [item.id for item in self.handoffs])

        field_ids: list[str] = []

        def visit(fields: list[ToolUiField]):
            for field in fields:
                field_ids.append(field.id)
                visit(field.fields)

        for section in self.sections:
            visit(section.fields)
        require_unique("field", field_ids)
        return self


class ResultMapping(ToolProductContract):
    kind: Literal["json_pointer"] = "json_pointer"
    pointer: str
    renames: dict[str, str] = Field(default_factory=dict, max_length=100)
    transforms: list[ToolUiTransform] = Field(default_factory=list, max_length=10)


class CapabilityBinding(ToolProductContract):
    binding_id: Identifier
    product_action_id: Identifier
    service_id: Identifier
    service_revision: int = Field(gt=0)
    capability_id: Identifier
    capability_version: str = Field(min_length=1, max_length=100)
    adapter: Literal["immediate_mcp", "job_mcp", "mcp_tasks"]
    submit_tool: str = Field(min_length=1, max_length=200)
    status_tool: str | None = Field(default=None, max_length=200)
    cancel_tool: str | None = Field(default=None, max_length=200)
    remote_output_schema: JsonSchema
    result_schema: JsonSchema
    result_mapping: ResultMapping
    required_usage: list[Metric] = Field(default_factory=list)
    gpu_count: int = Field(default=0, ge=0)
    max_budget: ComputeBudget = Field(default_factory=ComputeBudget)
    concurrency: int = Field(default=1, gt=0)
    max_execution_seconds: int = Field(default=1800, gt=0)
    limit_mode: Literal["soft", "hard"] = "soft"
    exclusive_process: bool = False
    cancellation: Literal["none", "cooperative", "confirmed_stop"] = "cooperative"

    @model_validator(mode="after")
    def adapter_tools(self):
        if self.adapter == "job_mcp" and not self.status_tool:
            raise ValueError("job_mcp bindings require status_tool")
        if self.limit_mode == "hard" and (
            self.cancellation != "confirmed_stop" or not self.exclusive_process
        ):
            raise ValueError("Hard limits require an independently stoppable executor")
        if self.gpu_count and "gpu_device_ms" not in self.required_usage:
            raise ValueError("GPU bindings must require gpu_device_ms")
        if self.max_budget.gpu_device_ms and "gpu_device_ms" not in self.required_usage:
            raise ValueError("GPU budgets must require gpu_device_ms")
        if self.max_budget.cpu_core_ms and "cpu_core_ms" not in self.required_usage:
            raise ValueError("CPU budgets must require cpu_core_ms")
        if "gpu_device_ms" in self.required_usage and self.max_budget.gpu_device_ms == 0:
            raise ValueError("GPU usage requires a positive reservation budget")
        if "cpu_core_ms" in self.required_usage and self.max_budget.cpu_core_ms == 0:
            raise ValueError("CPU usage requires a positive reservation budget")
        return self


class ProductAction(ToolProductContract):
    id: Identifier
    label: LocalizedText
    description: LocalizedText | None = None
    kind: Literal["capability", "workflow"]
    binding_ids: list[Identifier] = Field(min_length=1, max_length=50)
    input_schema: JsonSchema = Field(default_factory=lambda: {"type": "object"})

    @model_validator(mode="after")
    def valid_binding_count(self):
        if len(self.binding_ids) != len(set(self.binding_ids)):
            raise ValueError("action binding IDs must be unique")
        if self.kind == "capability" and len(self.binding_ids) != 1:
            raise ValueError("capability actions require exactly one binding")
        return self


class ProductVisibility(ToolProductContract):
    audience: Literal["public", "members", "restricted"] = "members"
    allowed_user_ids: list[str] = Field(default_factory=list, max_length=10_000)

    @model_validator(mode="after")
    def restricted_has_users(self):
        if self.audience == "restricted" and not self.allowed_user_ids:
            raise ValueError("restricted visibility requires allowed users")
        return self


class AgentHandoff(ToolProductContract):
    id: Identifier
    label: LocalizedText
    prompt_template_id: Identifier
    summary_pointer: str
    artifact_pointers: list[str] = Field(default_factory=list, max_length=20)


class ToolProductDraft(ToolProductContract):
    product_id: Identifier
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    revision: int = Field(gt=0)
    owner_user_id: str = Field(min_length=1, max_length=200)
    title: LocalizedText
    description: LocalizedText
    ui_schema: ToolUiSchema
    bindings: list[CapabilityBinding] = Field(min_length=1, max_length=100)
    actions: list[ProductAction] = Field(min_length=1, max_length=100)
    visibility: ProductVisibility = Field(default_factory=ProductVisibility)
    acceptance_suite_id: Identifier
    acceptance_suite_revision: int = Field(gt=0)
    handoffs: list[AgentHandoff] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def coherent_release_unit(self):
        if self.slug != self.ui_schema.product.slug:
            raise ValueError("product slug must match UI schema slug")
        binding_ids = [item.binding_id for item in self.bindings]
        action_ids = [item.id for item in self.actions]
        if len(binding_ids) != len(set(binding_ids)):
            raise ValueError("binding IDs must be unique")
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("product action IDs must be unique")
        known_bindings = set(binding_ids)
        known_actions = set(action_ids)
        for action in self.actions:
            missing = set(action.binding_ids) - known_bindings
            if missing:
                raise ValueError(f"unknown action binding: {min(missing)}")
        for item in self.bindings:
            if item.product_action_id not in known_actions:
                raise ValueError(f"unknown product action: {item.product_action_id}")
            if item.binding_id not in next(
                action.binding_ids for action in self.actions if action.id == item.product_action_id
            ):
                raise ValueError(f"binding {item.binding_id} is not owned by its product action")
        return self


class PublishedToolProduct(ToolProductContract):
    release_id: Identifier
    product_id: Identifier
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,79}$")
    revision: int = Field(gt=0)
    title: LocalizedText
    description: LocalizedText
    ui_schema: ToolUiSchema
    actions: list[ProductAction]
    state: Literal["published", "suspended"]
    published_at: datetime


class ToolProductPage(ToolProductContract):
    items: list[PublishedToolProduct]
    next_cursor: str | None = None


class QualificationCaseResult(ToolProductContract):
    case_id: Identifier
    status: Literal["passed", "failed"]
    protocol_assertions: dict[str, bool] = Field(default_factory=dict)
    scientific_assertions: dict[str, bool] = Field(default_factory=dict)
    usage_source: Literal["service_reported", "measured", "estimated", "unknown"] | None = None
    message: str = Field(default="", max_length=2000)


class ResultAssertion(ToolProductContract):
    pointer: str
    predicate: Literal["equals", "exists", "min_items", "maximum", "minimum"]
    value: Any = None


class AcceptanceCase(ToolProductContract):
    case_id: Identifier
    action_id: Identifier
    arguments: dict[str, Any]
    invalid_arguments: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    result_assertions: list[ResultAssertion] = Field(default_factory=list, max_length=100)
    required_progress_types: list[str] = Field(default_factory=list, max_length=50)
    check_idempotency: bool = True
    check_cancellation: bool = False


class AcceptanceSuite(ToolProductContract):
    suite_id: Identifier
    revision: int = Field(gt=0)
    cases: list[AcceptanceCase] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_case_ids(self):
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("acceptance case IDs must be unique")
        return self


class DiscoveredTool(ToolProductContract):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    input_schema: JsonSchema
    remote_output_schema: JsonSchema


class EndpointSnapshot(ToolProductContract):
    uri: str
    hostname: str
    port: int = Field(ge=1, le=65535)
    scheme: Literal["http", "https"]
    network_zone: str
    addresses: list[str] = Field(min_length=1, max_length=32)


class ProbeSnapshot(ToolProductContract):
    probe_id: Identifier
    endpoint_id: Identifier
    service_id: Identifier
    service_revision: int = Field(gt=0)
    uri: str
    transport: Literal["streamable_http", "sse"]
    credential_ref: str | None = None
    network_zone: str
    protocol: dict[str, Any]
    addresses: list[str]
    checked_at: datetime


class DiscoverySnapshot(ToolProductContract):
    discovery_id: Identifier
    service_id: Identifier
    service_revision: int = Field(gt=0)
    protocol: dict[str, Any]
    tools: list[DiscoveredTool]
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    discovered_at: datetime


class QualificationReport(ToolProductContract):
    report_id: Identifier
    product_id: Identifier
    product_revision: int = Field(gt=0)
    service_revision: int = Field(gt=0)
    binding_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    ui_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    suite_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: Literal["passed", "failed"]
    protocol_passed: bool
    scientific_passed: bool
    cases: list[QualificationCaseResult]
    qualified_at: datetime


ToolRunStatus = Literal["queued", "running", "cancelling", "completed", "failed", "cancelled"]


class ToolRunSnapshot(ToolProductContract):
    run_id: Identifier
    product_slug: str
    release_id: Identifier
    action_id: Identifier
    user_id: str
    status: ToolRunStatus
    progress: int = Field(ge=0, le=100)
    result: dict[str, Any] | None
    artifacts: list[ArtifactRef]
    usage: UsageReport | None
    created_at: datetime
    updated_at: datetime


class ToolRunEvent(ToolProductContract):
    event_id: Identifier
    run_id: Identifier
    sequence: int = Field(gt=0)
    type: Literal[
        "run.queued",
        "run.started",
        "stage.started",
        "stage.progress",
        "artifact.created",
        "usage.updated",
        "stage.completed",
        "run.cancelling",
        "run.completed",
        "run.failed",
        "run.cancelled",
    ]
    data: dict[str, Any]
    created_at: datetime


class ToolRunHandoffContext(ToolProductContract):
    run_id: Identifier
    handoff_id: Identifier
    summary: str = Field(max_length=4000)
    artifacts: list[ArtifactRef] = Field(default_factory=list, max_length=20)
