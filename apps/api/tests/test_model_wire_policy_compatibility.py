"""Opt-in strict forwarding cannot change legacy candidate/evidence identity."""

import json
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.contracts.model_runtime import (
    ModelQualificationCase,
    ModelRuntimeProfile,
    ModelWirePolicy,
    fingerprint,
)


# Prior published shapes reject unknown keys. These fixed shapes from f16
# intentionally do not inherit the current classes or require Git at runtime.
class LegacyContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LegacyWire(LegacyContract):
    chat_max_tokens_field: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    stream_usage_mode: Literal["supported", "not_supported", "unknown"] = "unknown"
    developer_role_mode: Literal["supported", "system_fallback", "unknown"] = "unknown"
    reasoning_mode: Literal["omit", "reasoning_effort"] = "omit"


class LegacyCase(LegacyContract):
    case_id: str = Field(min_length=1, max_length=80)
    status: Literal["passed", "failed", "not_tested", "unknown"]
    request_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    response_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    usage_known: bool | None = None


class LegacyEvidence(LegacyContract):
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
    cases: tuple[LegacyCase, ...] = Field(max_length=40)


class LegacyProfile(LegacyContract):
    alias: str = Field(min_length=1, max_length=200)
    revision: int = Field(ge=1)
    protocol: Literal["chat_completions", "responses"] = "chat_completions"
    provider_label: str | None = Field(default=None, min_length=1, max_length=100)
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    supports_tools: bool = False
    supports_tool_streaming: bool = False
    supports_images: bool = False
    reasoning_levels: list[Literal["off", "minimal", "low", "medium", "high", "xhigh", "max"]] = Field(
        default_factory=list, max_length=7)
    context_window: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    wire_policy: LegacyWire = Field(default_factory=LegacyWire)
    qualification_status: Literal["untested", "passed", "failed", "degraded"] = "untested"
    qualification_evidence_id: str | None = Field(default=None, max_length=100)
    qualification_evidence: LegacyEvidence | None = None


def legacy_candidate(profile):
    value = profile.model_dump(mode="json", exclude={
        "qualification_status", "qualification_evidence_id", "qualification_evidence",
    })
    value["wire_policy"].pop("preserve_openai_parameters", None)
    return value


@pytest.mark.parametrize("wire", [{}, {"reasoning_mode": "reasoning_effort"},
                                  {"chat_max_tokens_field": "max_completion_tokens"}])
def test_default_policy_retains_exact_legacy_candidate_hash(wire):
    profile = ModelRuntimeProfile(alias="legacy", revision=2, wire_policy=wire)
    assert profile.wire_policy.preserve_openai_parameters is False
    assert profile.candidate_fingerprint() == fingerprint(legacy_candidate(profile))


def test_explicit_strict_policy_changes_candidate_hash_and_is_not_a_hidden_default():
    legacy = ModelRuntimeProfile(alias="model", revision=1)
    strict = legacy.model_copy(update={"wire_policy": ModelWirePolicy(preserve_openai_parameters=True)})
    assert strict.candidate_fingerprint() != legacy.candidate_fingerprint()
    assert strict.candidate_fingerprint() == fingerprint(strict.model_dump(mode="json", exclude={
        "qualification_status", "qualification_evidence_id", "qualification_evidence",
    }))


@pytest.mark.parametrize("wire", [{}, {"preserve_openai_parameters": False},
    {"reasoning_mode": "reasoning_effort", "preserve_openai_parameters": False}])
def test_default_wire_json_remains_readable_by_prior_extra_forbid_contract(wire):
    profile = ModelRuntimeProfile(alias="legacy", revision=2, wire_policy=wire)
    for payload in (profile.model_dump(mode="json"), json.loads(profile.model_dump_json())):
        value = payload["wire_policy"]
        assert LegacyWire.model_validate(value).model_dump() == value
        assert "preserve_openai_parameters" not in value


@pytest.mark.parametrize("status", ["passed", "not_tested", "unknown"])
def test_public_case_json_matches_prior_extra_forbid_contract(status):
    case = ModelQualificationCase(case_id="text", status=status, request_fingerprint="a" * 64)
    for value in (case.model_dump(mode="json"), json.loads(case.model_dump_json())):
        assert LegacyCase.model_validate(value).model_dump() == value
        assert "not_tested_reason" not in value


def test_nondefault_wire_policy_is_persisted_and_requires_supporting_rollback_contract():
    policy = ModelWirePolicy(preserve_openai_parameters=True)
    assert policy.model_dump()["preserve_openai_parameters"] is True
    with pytest.raises(ValidationError):
        LegacyWire.model_validate(policy.model_dump())


def test_internal_probe_reason_is_not_a_public_case_field():
    with pytest.raises(ValidationError):
        ModelQualificationCase(case_id="tool_choice:required", status="not_tested",
            request_fingerprint="a" * 64,
            not_tested_reason="strict_parameter_forwarding_unverified")


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_false", [False, True])
@pytest.mark.parametrize("reasoning", [False, True])
async def test_new_false_qualification_evidence_and_run_snapshot_match_complete_legacy_schema(
        explicit_false, reasoning):
    from tests.test_model_qualification import good_probe, qualifier, with_evidence

    policy = {"reasoning_mode": "reasoning_effort"}
    if explicit_false:
        policy["preserve_openai_parameters"] = False
    candidate = ModelRuntimeProfile(alias="lab/alias", revision=1, supports_tools=True,
        supports_tool_streaming=True, supports_images=True,
        reasoning_levels=["off", "high"] if reasoning else [], wire_policy=policy)
    captured = []
    async def probe(case_id, body, **context):
        captured.append(case_id)
        return await good_probe(case_id, body, **context)
    evidence = await qualifier(probe, scope="staging").evaluate(candidate, actor_id="operator",
        deployment_fingerprint=fingerprint("synthetic-route"))
    qualified = with_evidence(candidate, evidence)
    cases = {case.case_id: case for case in evidence.cases}
    assert cases["tool_choice:auto"].status == "passed"
    for name in ("tool_choice:required", "tool_choice:specific",
                 *(f"reasoning:{level}" for level in candidate.reasoning_levels)):
        assert cases[name].status == "not_tested"
        assert name not in captured
    assert evidence.status == ("degraded" if reasoning else "passed")
    assert qualified.agent_ready is not reasoning
    for value in (evidence.model_dump(mode="json"), json.loads(evidence.model_dump_json())):
        assert LegacyEvidence.model_validate(value).model_dump(mode="json") == value
        assert all(set(case) == set(LegacyCase.model_fields) for case in value["cases"])
    for value in (qualified.model_dump(mode="json"), json.loads(qualified.model_dump_json())):
        old = LegacyProfile.model_validate(value)
        assert old.model_dump(mode="json") == value
        assert fingerprint(legacy_candidate(old)) == qualified.candidate_fingerprint()
        context = json.loads(json.dumps({"model_id": candidate.alias, "model_runtime_profile": value}))
        assert LegacyProfile.model_validate(context["model_runtime_profile"]) == old
