"""Opt-in strict forwarding cannot change legacy candidate/evidence identity."""

import json

import pytest
from pydantic import ConfigDict, ValidationError, create_model

from app.contracts.model_runtime import (
    ModelQualificationCase,
    ModelRuntimeProfile,
    ModelWirePolicy,
    fingerprint,
)

# Prior published wire/case shapes reject unknown keys. These fixed shapes
# intentionally do not inherit the current classes or require Git at runtime.
LegacyWire = create_model("LegacyWire", __config__=ConfigDict(extra="forbid"),
    chat_max_tokens_field=(str, "max_tokens"), stream_usage_mode=(str, "unknown"),
    developer_role_mode=(str, "unknown"), reasoning_mode=(str, "omit"))
LegacyCase = create_model("LegacyCase", __config__=ConfigDict(extra="forbid"),
    case_id=(str, ...), status=(str, ...), request_fingerprint=(str, ...),
    response_fingerprint=(str | None, None), usage_known=(bool | None, None))


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
def test_absent_case_reason_json_remains_readable_by_prior_extra_forbid_contract(status):
    case = ModelQualificationCase(case_id="text", status=status, request_fingerprint="a" * 64)
    for value in (case.model_dump(mode="json"), json.loads(case.model_dump_json())):
        assert LegacyCase.model_validate(value).model_dump() == value
        assert "not_tested_reason" not in value


def test_nondefault_wire_policy_is_persisted_and_requires_supporting_rollback_contract():
    policy = ModelWirePolicy(preserve_openai_parameters=True)
    assert policy.model_dump()["preserve_openai_parameters"] is True
    with pytest.raises(ValidationError):
        LegacyWire.model_validate(policy.model_dump())


def test_explicit_not_tested_reason_is_retained_for_supporting_contracts():
    case = ModelQualificationCase(case_id="tool_choice:required", status="not_tested",
        request_fingerprint="a" * 64,
        not_tested_reason="strict_parameter_forwarding_unverified")
    payload = case.model_dump(mode="json")
    assert payload["not_tested_reason"] == "strict_parameter_forwarding_unverified"
    assert ModelQualificationCase.model_validate(payload) == case
    with pytest.raises(ValidationError):
        LegacyCase.model_validate(payload)
