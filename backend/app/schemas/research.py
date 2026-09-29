from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.tasks import TaskResponse


ResearchRunStatus = Literal["draft", "active", "completed", "failed", "archived"]
ResearchStage = Literal[
    "target_analysis",
    "homology_analysis",
    "binding_assessment",
    "candidate_generation",
    "candidate_evaluation",
    "top10_af3",
    "strategy_feedback",
    "report",
]
CandidateTrackName = Literal["rna", "peptide"]
CandidateTrackStatus = Literal[
    "pending",
    "generating",
    "evaluating",
    "ranked",
    "af3_running",
    "completed",
    "failed",
]
CandidateSelectionStatus = Literal["pending", "qualified", "selected", "rejected"]
ResearchTaskRole = Literal[
    "target_analysis",
    "homology",
    "binding",
    "candidate_generation",
    "candidate_evaluation",
    "af3",
    "strategy_feedback",
    "report",
]


class CreateResearchRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="New Research Run", min_length=1, max_length=200)
    session_id: UUID | None = None
    target: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_target_sequence(self) -> "CreateResearchRunRequest":
        target = dict(self.target)
        raw_sequence = target.get("sequence") or target.get("protein_sequence")
        sequence = "".join(str(raw_sequence or "").split()).upper()
        if not sequence:
            raise ValueError("target protein sequence is required")
        if len(sequence) > 10000:
            raise ValueError("target protein sequence exceeds 10000 residues")
        invalid = sorted(set(sequence) - set("ACDEFGHIKLMNPQRSTVWY"))
        if invalid:
            raise ValueError(
                f"target protein sequence contains unsupported symbols: {''.join(invalid)}"
            )
        target["sequence"] = sequence
        target.pop("protein_sequence", None)
        self.target = target
        return self


class UpdateResearchRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    target: dict | None = None
    evidence: list[dict] | None = None
    metadata: dict | None = None

    @field_validator("target")
    @classmethod
    def reject_explicit_null_target(cls, value: dict | None) -> dict:
        if value is None:
            raise ValueError("target cannot be null")
        return value

    @model_validator(mode="after")
    def validate_optional_target_sequence(self) -> "UpdateResearchRunRequest":
        if self.target is None:
            return self
        target = dict(self.target)
        raw_sequence = target.get("sequence") or target.get("protein_sequence")
        sequence = "".join(str(raw_sequence or "").split()).upper()
        if not sequence or len(sequence) > 10000:
            raise ValueError("target protein sequence must contain 1 to 10000 residues")
        invalid = sorted(set(sequence) - set("ACDEFGHIKLMNPQRSTVWY"))
        if invalid:
            raise ValueError(
                f"target protein sequence contains unsupported symbols: {''.join(invalid)}"
            )
        target["sequence"] = sequence
        target.pop("protein_sequence", None)
        self.target = target
        return self


class CandidateTrackResponse(BaseModel):
    id: str
    track: CandidateTrackName
    status: CandidateTrackStatus
    current_iteration: int
    score_config: dict
    summary: dict
    created_at: str
    updated_at: str


class ResearchRunResponse(BaseModel):
    id: str
    session_id: str | None
    title: str
    status: ResearchRunStatus
    current_stage: ResearchStage
    target: dict
    stage_state: dict
    evidence: list[dict]
    policy_version: str | None
    metadata: dict
    tracks: list[CandidateTrackResponse]
    created_at: str
    updated_at: str


class CreateCandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track: CandidateTrackName
    sequence: str = Field(min_length=1)
    parent_candidate_id: UUID | None = None
    iteration: int = Field(default=0, ge=0)
    note: str | None = Field(default=None, max_length=1000)
    metadata: dict = Field(default_factory=dict)

    @field_validator("sequence")
    @classmethod
    def normalize_sequence(cls, value: str) -> str:
        normalized = "".join(value.split()).upper()
        if not normalized:
            raise ValueError("sequence must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_track_alphabet(self) -> "CreateCandidateRequest":
        allowed = set("ACGU") if self.track == "rna" else set("ACDEFGHIKLMNPQRSTVWY")
        invalid = sorted(set(self.sequence) - allowed)
        if invalid:
            raise ValueError(
                f"{self.track} candidate contains unsupported symbols: {''.join(invalid)}"
            )
        return self


class UpdateCandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    curation_decision: Literal["selected", "rejected", "clear"]
    curator_comment: str = Field(min_length=3, max_length=1000)


class CandidateResponse(BaseModel):
    id: str
    research_run_id: str
    candidate_track_id: str
    schema_version: str
    track: CandidateTrackName
    sequence: str
    sequence_length: int
    generator_name: str
    generator_version: str | None
    generation_task_id: str | None
    raw_artifact_id: str | None
    parent_candidate_id: str | None
    iteration: int
    seed: int | None
    parameters_hash: str | None
    raw_metrics: dict
    normalized_metrics: dict
    total_score: float | None
    score_config_version: str | None
    rank: int | None
    selection_status: CandidateSelectionStatus
    selection_reason: str | None
    af3_task_id: str | None
    af3_status: str | None
    af3_artifact_id: str | None
    af3_result_artifact_id: str | None
    metadata: dict
    created_at: str
    updated_at: str


class LinkResearchTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: ResearchTaskRole
    candidate_id: UUID | None = None


class ManualStageSkipRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    next_stage: ResearchStage
    reason: str = Field(min_length=10, max_length=1000)
    confirmation: Literal["CONFIRM_STAGE_SKIP"]


class ResearchTaskResponse(BaseModel):
    id: str
    role: ResearchTaskRole
    candidate_id: str | None
    task: TaskResponse
    created_at: str


class MetricRule(BaseModel):
    name: str = Field(min_length=1)
    weight: float = Field(gt=0)
    direction: Literal["higher", "lower"] = "higher"
    missing_policy: Literal["reject", "zero", "ignore"] = "reject"

    @field_validator("name")
    @classmethod
    def normalize_metric_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("metric name must not be blank")
        return normalized


class ScoreTrackRequest(BaseModel):
    config_version: str = Field(min_length=1)
    metrics: list[MetricRule] = Field(min_length=1)
    iteration: int | None = Field(default=None, ge=0)
    minimum_total_score: float = Field(default=0.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_unique_metric_names(self) -> "ScoreTrackRequest":
        names = [item.name for item in self.metrics]
        if len(names) != len(set(names)):
            raise ValueError("metric names must be unique")
        self.config_version = self.config_version.strip()
        if not self.config_version:
            raise ValueError("config_version must not be blank")
        return self


class TrackScoreResponse(BaseModel):
    track: CandidateTrackName
    iteration: int | None
    config_version: str
    candidate_count: int
    qualified_count: int
    rejected_count: int
    candidates: list[CandidateResponse]


class CreateAf3BatchRequest(BaseModel):
    max_candidates: int = Field(default=10, ge=1, le=10)
    model_seed: int = 42
    num_diffusion_samples: int = Field(default=5, ge=1)


class Af3BatchResponse(BaseModel):
    track: CandidateTrackName
    submitted_count: int
    reused_count: int
    tasks: list[ResearchTaskResponse]


class UpdateStrategyPolicyRequest(BaseModel):
    enabled: bool | None = None
    alpha: float | None = Field(default=None, gt=0.0, le=1.0)
    gamma: float | None = Field(default=None, ge=0.0, le=1.0)
    epsilon: float | None = Field(default=None, ge=0.0, le=1.0)


class StrategyPolicyResponse(BaseModel):
    id: str
    research_run_id: str
    version: int
    enabled: bool
    alpha: float
    gamma: float
    epsilon: float
    q_table: dict
    action_mask: dict[str, list[str]]
    metrics: dict
    created_at: str
    updated_at: str


class SubmitStrategyFeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_message_id: UUID
    tool_call_id: str = Field(min_length=1, max_length=240)
    reward: float = Field(ge=-10.0, le=10.0)
    comment: str | None = Field(default=None, max_length=1000)


class StrategyFeedbackOptionResponse(BaseModel):
    agent_message_id: str
    tool_call_id: str
    action: str
    stage: ResearchStage
    state_key: str
    dispatch_status: str
    execution_status: Literal[
        "completed",
        "pending",
        "failed",
        "interrupted",
        "unverified",
    ]
    success: bool | None
    invalid_call: bool
    error_type: str | None
    task_ids: list[str]
    task_statuses: dict[str, str]
    artifact_ids: list[str]
    policy_version_at_action: int | None
    action_rank_at_action: int | None
    already_feedback: bool
    created_at: str


class StrategyTransitionResponse(BaseModel):
    id: str
    state_key: str
    action: str
    reward: float
    next_state_key: str
    q_before: float
    q_after: float
    outcome: dict
    policy_version: int
    created_at: str


class StrategyFeedbackResponse(BaseModel):
    policy: StrategyPolicyResponse
    transition: StrategyTransitionResponse


class StrategyReplayResponse(BaseModel):
    matches_current: bool
    transition_count: int
    q_table: dict


class GenerateResearchReportRequest(BaseModel):
    title: str | None = None
