"""Server-published model capabilities and secret-free qualification fingerprints."""

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Reasoning = Literal["off", "minimal", "low", "medium", "high", "xhigh", "max"]
CheckStatus = Literal["passed", "failed", "not_tested", "unknown"]
REASONING_LEVELS = frozenset({"off", "minimal", "low", "medium", "high", "xhigh", "max"})


def reasoning_wire_value(logical: str) -> str:
    """Pi's Chat Completions wire uses none for the public UI level off."""
    if logical not in REASONING_LEVELS:
        raise ValueError("MODEL_REASONING_UNAVAILABLE")
    return "none" if logical == "off" else logical


def reasoning_logical_value(wire: object) -> str | None:
    """Normalize a closed upstream value for current-grant capability checks."""
    if wire == "none":
        return "off"
    return wire if isinstance(wire, str) and wire in REASONING_LEVELS else None


def fingerprint(value: object) -> str:
    """Hash a canonical snapshot; evidence never stores request bodies or credentials."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


class RuntimeContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelWirePolicy(RuntimeContract):
    chat_max_tokens_field: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    stream_usage_mode: Literal["supported", "not_supported", "unknown"] = "unknown"
    developer_role_mode: Literal["supported", "system_fallback", "unknown"] = "unknown"
    reasoning_mode: Literal["omit", "reasoning_effort"] = "omit"
    preserve_openai_parameters: bool = Field(default=False, exclude_if=lambda value: value is False)


class ModelQualificationCase(RuntimeContract):
    case_id: str = Field(min_length=1, max_length=80)
    status: CheckStatus
    request_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    response_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    usage_known: bool | None = None


class ModelQualificationEvidence(RuntimeContract):
    evidence_id: str = Field(min_length=1, max_length=100)
    alias: str = Field(min_length=1, max_length=200)
    profile_revision: int = Field(ge=1)
    profile_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    deployment_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    provider_url_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    pi_version: str = Field(min_length=1, max_length=100)
    gateway_version: str = Field(min_length=1, max_length=100)
    checked_at: str = Field(min_length=1, max_length=50)
    verification_scope: Literal["offline", "gateway", "staging"]
    status: Literal["passed", "failed", "degraded"]
    cases: tuple[ModelQualificationCase, ...] = Field(max_length=40)


class ModelRuntimeProfile(RuntimeContract):
    alias: str = Field(min_length=1, max_length=200)
    revision: int = Field(ge=1)
    protocol: Literal["chat_completions", "responses"] = "chat_completions"
    provider_label: str | None = Field(default=None, min_length=1, max_length=100)
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    supports_tools: bool = False
    supports_tool_streaming: bool = False
    supports_images: bool = False
    reasoning_levels: list[Reasoning] = Field(default_factory=list, max_length=7)
    context_window: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    wire_policy: ModelWirePolicy = Field(default_factory=ModelWirePolicy)
    qualification_status: Literal["untested", "passed", "failed", "degraded"] = "untested"
    qualification_evidence_id: str | None = Field(default=None, max_length=100)
    qualification_evidence: ModelQualificationEvidence | None = None

    def candidate_fingerprint(self) -> str:
        candidate = self.model_dump(mode="json", exclude={
            "qualification_status", "qualification_evidence_id", "qualification_evidence",
        })
        # Absence/False retains the exact pre-policy fingerprint so existing
        # published profiles and their evidence are not retroactively revoked.
        if not self.wire_policy.preserve_openai_parameters:
            candidate["wire_policy"].pop("preserve_openai_parameters", None)
        return fingerprint(candidate)

    @property
    def agent_ready(self) -> bool:
        """Protocol mocks or client declarations cannot stand in for real Pi/artifacts."""
        evidence = self.qualification_evidence
        if (self.protocol != "chat_completions" or not self.supports_tools
                or not self.supports_tool_streaming or evidence is None):
            return False
        required = {"text", "stream", "tools", "tool_continue", "streaming_tools",
                    "errors", "errors:401", "errors:402", "errors:403", "errors:429",
                    "errors:500", "errors:503", "errors:timeout", "alias_routing",
                    "workspace_artifact"}
        required.update(f"reasoning:{level}" for level in self.reasoning_levels)
        if self.supports_images:
            required.add("images")
        passed = {case.case_id for case in evidence.cases if case.status == "passed"}
        return (self.qualification_status == evidence.status == "passed"
                and evidence.verification_scope == "staging"
                and evidence.alias == self.alias
                and evidence.profile_revision == self.revision
                and evidence.profile_fingerprint == self.candidate_fingerprint()
                and evidence.evidence_id == self.qualification_evidence_id
                and required <= passed)
