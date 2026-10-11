"""Reject false compatibility evidence at the qualification protocol boundary."""

import base64
import copy
import io
import json

import pytest
from PIL import Image

from app.contracts.model_runtime import ModelWirePolicy, fingerprint
from app.domain.admin.model_qualification import ModelQualification
from tests.test_model_qualification import good_probe, profile, qualifier, with_evidence


def tool_stream():
    return {"chunks": [
        {"choices": [{"index": 0, "delta": {"tool_calls": [{
            "index": 0, "id": "stable-call", "type": "function", "function": {
                "name": "qualification_echo", "arguments": '{"value":',
            },
        }]}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [{
            "index": 0, "function": {"arguments": '"qualification"}'},
        }]}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 1}},
    ]}


@pytest.mark.parametrize("mutation", [
    "changed_id", "changed_name", "split_name", "wrong_index", "bool_index",
    "wrong_choice", "multiple_choices", "extra_call", "error_after_finish",
    "bad_finish_then_good", "blank_chunk", "wrong_call_type", "missing_id",
    "nonstring_arguments", "truncated_arguments",
])
def test_streaming_tools_rejects_identity_errors_and_malformed_completion(mutation):
    document = tool_stream()
    first = document["chunks"][0]["choices"][0]
    last = document["chunks"][1]["choices"][0]
    first_call = first["delta"]["tool_calls"][0]
    last_call = last["delta"]["tool_calls"][0]
    if mutation == "changed_id":
        last_call["id"] = "different-call"
    elif mutation == "changed_name":
        last_call["function"]["name"] = "different_tool"
    elif mutation == "split_name":
        first_call["function"]["name"] = "qualification_"
        last_call["function"]["name"] = "echo"
    elif mutation == "wrong_index":
        last_call["index"] = 1
    elif mutation == "bool_index":
        last_call["index"] = False
    elif mutation == "wrong_choice":
        last["index"] = 1
    elif mutation == "multiple_choices":
        document["chunks"][1]["choices"].append(copy.deepcopy(last))
    elif mutation == "extra_call":
        first["delta"]["tool_calls"].append(copy.deepcopy(first_call))
    elif mutation == "error_after_finish":
        document["chunks"].append({"error": {"message": "private provider error"}})
    elif mutation == "bad_finish_then_good":
        first["finish_reason"] = "length"
    elif mutation == "blank_chunk":
        document["chunks"].append({"choices": []})
    elif mutation == "wrong_call_type":
        first_call["type"] = "custom"
    elif mutation == "missing_id":
        del first_call["id"]
    elif mutation == "nonstring_arguments":
        last_call["function"]["arguments"] = 42
    else:
        last_call["function"]["arguments"] = '"qualification"'
    assert ModelQualification._status("streaming_tools", document, fingerprint("route"), None) == "failed"


def test_streaming_tools_accepts_stable_repeated_identity_and_argument_fragments():
    document = tool_stream()
    call = document["chunks"][1]["choices"][0]["delta"]["tool_calls"][0]
    call["id"] = "stable-call"
    call["function"]["name"] = "qualification_echo"
    assert ModelQualification._status("streaming_tools", document, fingerprint("route"), None) == "passed"


@pytest.mark.parametrize("nullable", ["type", "function", "primer"])
def test_streaming_tools_accepts_nullable_optional_fields_used_by_the_sdk(nullable):
    document = tool_stream()
    if nullable == "type":
        document["chunks"][1]["choices"][0]["delta"]["tool_calls"][0]["type"] = None
    elif nullable == "function":
        document["chunks"].insert(0, {"choices": [{"index": 0, "delta": {"tool_calls": [{
            "index": 0, "id": "stable-call", "function": None,
        }]}, "finish_reason": None}]})
    else:
        document["chunks"].insert(0, {"choices": [{"index": 0, "delta": {
            "role": "assistant", "content": None, "tool_calls": None,
        }, "finish_reason": None}]})
    assert ModelQualification._status("streaming_tools", document, fingerprint("route"), None) == "passed"


@pytest.mark.parametrize("content", ["", "  ", "qualification", '{"color":"blue"}', '{"color":"red","guess":true}'])
def test_vision_requires_the_known_fixture_recognition(content):
    document = {"choices": [{"message": {"content": content}}]}
    assert ModelQualification._status("images", document, fingerprint("route"), None) == "failed"


@pytest.mark.asyncio
async def test_vision_probe_uses_decodable_known_input_without_disclosing_answer_in_prompt():
    captured = []
    async def probe(case_id, body, **context):
        if case_id == "images":
            captured.append(body)
        return await good_probe(case_id, body, **context)
    evidence = await qualifier(probe).evaluate(profile(supports_images=True), actor_id="operator",
        deployment_fingerprint=fingerprint("route"))
    assert next(case for case in evidence.cases if case.case_id == "images").status == "passed"
    content = captured[0]["messages"][0]["content"]
    assert "red" not in content[0]["text"].lower()
    image = Image.open(io.BytesIO(base64.b64decode(content[1]["image_url"]["url"].split(",", 1)[1])))
    assert image.format == "PNG" and image.size == (32, 32)
    assert image.convert("RGB").getcolors() == [(1024, (255, 0, 0))]


@pytest.mark.asyncio
async def test_tool_choices_are_independent_strict_requests_without_changing_legacy_fingerprints():
    captured = {}
    async def probe(case_id, body, **context):
        captured[case_id] = body
        return await good_probe(case_id, body, **context)
    candidate = profile(wire_policy=ModelWirePolicy(preserve_openai_parameters=True))
    evidence = await qualifier(probe, scope="staging").evaluate(candidate, actor_id="operator",
        deployment_fingerprint=fingerprint("route"))
    choices = {
        "tool_choice:auto": "auto", "tool_choice:required": "required",
        "tool_choice:specific": {"type": "function", "function": {"name": "qualification_echo"}},
    }
    statuses = {case.case_id: case.status for case in evidence.cases}
    for name, choice in choices.items():
        assert name in captured
        assert captured[name]["tool_choice"] == choice
        assert captured[name]["tools"][0]["function"]["name"] == "qualification_echo"
        assert captured[name]["drop_params"] is False
        assert statuses[name] == "passed"
    assert with_evidence(candidate, evidence).agent_ready
    legacy = evidence.model_copy(update={"cases": tuple(case for case in evidence.cases if not case.case_id.startswith("tool_choice:"))})
    assert with_evidence(candidate, legacy).agent_ready


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_false", [False, True])
@pytest.mark.parametrize("case_id", ["tool_choice:required", "tool_choice:specific"])
async def test_nonpreserving_optional_choices_are_not_tested_without_dispatch(case_id, explicit_false):
    captured = {}
    async def probe(name, body, **context):
        captured[name] = body
        return await good_probe(name, body, **context)
    candidate = profile(**({"wire_policy": ModelWirePolicy(preserve_openai_parameters=False)}
                            if explicit_false else {}))
    evidence = await qualifier(probe, scope="staging").evaluate(candidate, actor_id="operator",
        deployment_fingerprint=fingerprint("route"))
    cases = {case.case_id: case for case in evidence.cases}
    assert cases[case_id].status == "not_tested"
    assert "not_tested_reason" not in cases[case_id].model_dump(mode="json")
    assert cases[case_id].response_fingerprint is None
    assert case_id not in captured
    assert cases["tool_choice:auto"].status == "passed"
    assert captured["tool_choice:auto"]["tool_choice"] == "auto"
    assert "drop_params" not in captured["tool_choice:auto"]
    assert evidence.status == "passed"
    assert with_evidence(candidate, evidence).agent_ready


def test_internal_reasoning_probe_marker_still_records_not_tested():
    assert ModelQualification._status("reasoning:high", {
        "not_tested_reason": "strict_parameter_forwarding_unverified",
    }, fingerprint("route"), None) == "not_tested"


@pytest.mark.asyncio
async def test_unsupported_optional_tool_choice_is_recorded_without_revoking_core_tools():
    async def probe(case_id, body, **context):
        if case_id == "tool_choice:required":
            return {"choices": [{"message": {"content": "qualification"}}]}
        return await good_probe(case_id, body, **context)
    candidate = profile(wire_policy=ModelWirePolicy(preserve_openai_parameters=True))
    evidence = await qualifier(probe, scope="staging").evaluate(candidate, actor_id="operator",
        deployment_fingerprint=fingerprint("route"))
    cases = {case.case_id: case.status for case in evidence.cases}
    assert cases["tool_choice:required"] == "failed"
    assert cases["tool_choice:auto"] == cases["tool_choice:specific"] == "passed"
    assert evidence.status == "passed"
    assert with_evidence(candidate, evidence).agent_ready


@pytest.mark.parametrize("content", ["", "  "])
def test_text_reasoning_and_continuation_reject_empty_success(content):
    document = {"choices": [{"message": {"content": content}}]}
    for case_id in ("text", "reasoning:high", "tool_continue"):
        assert ModelQualification._status(case_id, document, fingerprint("route"), {"id": "call"}) == "failed"


def test_extra_nonstream_tool_call_cannot_be_ignored():
    call = {"id": "call", "type": "function", "function": {
        "name": "qualification_echo", "arguments": json.dumps({"value": "qualification"}),
    }}
    document = {"choices": [{"message": {"tool_calls": [call, {"id": "unverified"}]}}]}
    assert ModelQualification._status("tools", document, fingerprint("route"), None) == "failed"


@pytest.mark.parametrize("case_id", ["text", "images", "reasoning:high", "tool_continue", "tools", "tool_choice:auto"])
@pytest.mark.parametrize("mutation", ["error_envelope", "length", "content_filter", "wrong_finish", "null_finish", "bool_index", "float_index"])
def test_nonstream_rejects_error_envelopes_and_invalid_terminal_states(case_id, mutation):
    message = {"content": '{"color":"red"}' if case_id == "images" else "qualification"}
    if case_id in {"tools", "tool_choice:auto"}:
        message = {"content": None, "tool_calls": [{"id": "stable-call", "type": "function", "function": {
            "name": "qualification_echo", "arguments": '{"value":"qualification"}',
        }}]}
    choice = {"index": 0, "message": message,
              "finish_reason": "tool_calls" if case_id in {"tools", "tool_choice:auto"} else "stop"}
    document = {"choices": [choice]}
    if mutation == "error_envelope":
        document["error"] = {"code": "provider_failed", "message": "private error"}
    elif mutation == "bool_index":
        choice["index"] = False
    elif mutation == "float_index":
        choice["index"] = 0.0
    elif mutation == "null_finish":
        choice["finish_reason"] = None
    elif mutation == "wrong_finish":
        choice["finish_reason"] = "stop" if case_id in {"tools", "tool_choice:auto"} else "tool_calls"
    else:
        choice["finish_reason"] = mutation
    assert ModelQualification._status(case_id, document, fingerprint("route"), {"id": "stable-call"}) == "failed"


@pytest.mark.parametrize("case_id", ["text", "images", "reasoning:high", "tool_continue", "tools", "tool_choice:auto"])
@pytest.mark.parametrize("legacy", [False, True])
def test_nonstream_accepts_correct_terminal_state_and_legacy_absent_fields(case_id, legacy):
    message = {"content": '{"color":"red"}' if case_id == "images" else "qualification"}
    if case_id in {"tools", "tool_choice:auto"}:
        message = {"content": None, "tool_calls": [{"id": "stable-call", "type": "function", "function": {
            "name": "qualification_echo", "arguments": '{"value":"qualification"}',
        }}]}
    choice = {"message": message}
    if not legacy:
        choice.update({"index": 0, "finish_reason": "tool_calls" if case_id in {"tools", "tool_choice:auto"} else "stop"})
    assert ModelQualification._status(case_id, {"choices": [choice]}, fingerprint("route"), {"id": "stable-call"}) == "passed"
