"""Bounded protocol qualification through a trusted, budgeted server probe.

No URL, credentials, prompts or claimed results are accepted from an API caller.
The host supplies the probe and its per-request authorization/budget boundary;
without it qualification is unavailable, and offline checks never grant Agent Ready.
"""

import asyncio
import inspect
import json
import uuid
from datetime import UTC, datetime

import httpx

from app.contracts.model_runtime import (
    ModelQualificationCase,
    ModelQualificationEvidence,
    ModelRuntimeProfile,
    fingerprint,
    reasoning_wire_value,
)
from app.services.model_gateway import prepare_model_parameters

TOOL = {"type": "function", "function": {"name": "qualification_echo", "parameters": {
    "type": "object", "properties": {"value": {"type": "string"}},
    "required": ["value"], "additionalProperties": False,
}}}
IMAGE = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAAKElEQVR4nO3NMQEAAAjDMMC/ZzDBvlRA01vZJvwHAAAAAAAAAAAAbx2jxAE/i2AjOgAAAABJRU5ErkJggg=="
_OPTIONAL_CASES = frozenset({"tool_choice:required", "tool_choice:specific"})


def _message(document, *, finish_reason="stop"):
    choices = document["choices"]
    if "error" in document or not isinstance(choices, list) or len(choices) != 1:
        raise ValueError("MODEL_QUALIFICATION_RESPONSE_INVALID")
    choice = choices[0]
    index = choice.get("index", 0)
    if (type(index) is not int or index != 0
            or "finish_reason" in choice and choice["finish_reason"] != finish_reason):
        raise ValueError("MODEL_QUALIFICATION_RESPONSE_INVALID")
    return choice["message"]


def _content_valid(document) -> bool:
    try:
        content = _message(document).get("content")
        return isinstance(content, str) and bool(content.strip())
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        return False


def _image_valid(document) -> bool:
    try:
        return _content_valid(document) and json.loads(_message(document)["content"]) == {"color": "red"}
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        return False


def _tool_call(document):
    try:
        calls = _message(document, finish_reason="tool_calls")["tool_calls"]
        if not isinstance(calls, list) or len(calls) != 1:
            return None
        call = calls[0]
        arguments = json.loads(call["function"]["arguments"])
        if (call.get("type") != "function" or not isinstance(call.get("id"), str)
                or not call["id"] or call["function"]["name"] != "qualification_echo"
                or arguments != {"value": "qualification"}):
            return None
        return call
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        return None


def _stream_valid(document, *, tools=False) -> bool:
    """Validate actual typed chunks; printable tool tags are never interpreted."""
    chunks = document.get("chunks")
    if not isinstance(chunks, list) or not chunks or len(chunks) > 2048:
        return False
    text, call_id, name, arguments = "", "", "", ""
    ended = False
    try:
        for chunk in chunks:
            if not isinstance(chunk, dict) or "error" in chunk:
                return False
            choices = chunk.get("choices")
            if not choices:
                if choices != [] or not ended or not _usage_known(chunk):
                    return False
                continue  # The terminal usage-only chunk carries no new delta.
            if ended:
                return False
            if not isinstance(choices, list) or len(choices) != 1:
                return False
            choice = choices[0]
            if type(choice.get("index", 0)) is not int or choice.get("index", 0) != 0:
                return False
            delta = choice["delta"]
            content = delta.get("content")
            if content is not None and not isinstance(content, str):
                return False
            text += content or ""
            if tools:
                calls = delta.get("tool_calls")
                if calls is None:
                    calls = []
                if not isinstance(calls, list) or len(calls) > 1:
                    return False
                for call in calls:
                    if type(call.get("index")) is not int or call["index"] != 0:
                        return False
                    if call.get("type") not in {None, "function"}:
                        return False
                    identifier = call.get("id")
                    function = call.get("function")
                    if function is None:
                        function = {}
                    next_name = function.get("name")
                    # Pi 0.87.1 retains the first nonempty ID/name; only JSON
                    # arguments append. Fragmented or changing identities fail.
                    for previous, value in ((call_id, identifier), (name, next_name)):
                        if value is not None and (not isinstance(value, str)
                                or previous and value and value != previous):
                            return False
                    call_id = call_id or identifier or ""
                    name = name or next_name or ""
                    fragment = function.get("arguments")
                    if fragment is not None and not isinstance(fragment, str):
                        return False
                    arguments += fragment or ""
            elif delta.get("tool_calls"):
                return False
            if choice.get("finish_reason") is not None:
                if choice["finish_reason"] != ("tool_calls" if tools else "stop"):
                    return False
                ended = True
        if tools:
            return (ended and bool(call_id) and name == "qualification_echo"
                    and json.loads(arguments) == {"value": "qualification"})
        return ended and bool(text.strip())
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        return False


def _usage_known(document) -> bool:
    usage = document.get("usage")
    return (isinstance(usage, dict)
            and all(isinstance(usage.get(field), int) and not isinstance(usage[field], bool)
                    and usage[field] >= 0 for field in ("prompt_tokens", "completion_tokens")))


class ModelQualification:
    """Evaluate fixed cases; actual transport/Pi/ledger probes remain host-owned."""

    def __init__(self, probe, *, pi_version: str, gateway_version: str,
                 provider_url_fingerprint: str, verification_scope="offline",
                 budget_check=None, timeout_seconds: float = 60,
                 max_response_bytes: int = 256 * 1024) -> None:
        if verification_scope != "offline" and budget_check is None:
            raise ValueError("MODEL_QUALIFICATION_BUDGET_REQUIRED")
        if verification_scope not in {"offline", "gateway", "staging"}:
            raise ValueError("MODEL_QUALIFICATION_SCOPE_INVALID")
        if not 0 < timeout_seconds <= 120 or not 0 < max_response_bytes <= 1024 * 1024:
            raise ValueError("MODEL_QUALIFICATION_LIMIT_INVALID")
        self.probe = probe
        self.pi_version = pi_version
        self.gateway_version = gateway_version
        self.provider_url_fingerprint = provider_url_fingerprint
        self.verification_scope = verification_scope
        self.budget_check = budget_check
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes

    async def evaluate(self, profile: ModelRuntimeProfile, *, actor_id: str,
                       deployment_fingerprint: str) -> ModelQualificationEvidence:
        """Return hashes and outcomes only; never persist private upstream text/errors."""
        if profile.protocol != "chat_completions":
            raise ValueError("MODEL_PROTOCOL_UNSUPPORTED")
        cases = []
        tool_call = None
        base = {"model": profile.alias,
                "messages": [{"role": "user", "content": "Reply with qualification."}],
                profile.wire_policy.chat_max_tokens_field: min(profile.max_output_tokens or 64, 64)}
        names = ["text", "stream", "usage", "usage_missing", "errors:401", "errors:402",
                 "errors:403", "errors:429", "errors:500", "errors:503", "errors:timeout",
                 "errors", "alias_routing"]
        if profile.supports_tools:
            names += ["tools", "tool_continue", "tool_choice:auto", "tool_choice:required", "tool_choice:specific"]
        if profile.supports_tool_streaming:
            names += ["streaming_tools"]
        if profile.supports_images:
            names += ["images"]
        names += [f"reasoning:{level}" for level in profile.reasoning_levels]
        names += ["workspace_artifact"]
        async with asyncio.timeout(self.timeout_seconds):
            for case_id in names:
                request = json.loads(json.dumps(base))
                if case_id in {"stream", "streaming_tools"}:
                    request["stream"] = True
                if case_id in {"tools", "streaming_tools"} or case_id.startswith("tool_choice:"):
                    request["tools"] = [TOOL]
                    request["messages"][0]["content"] = (
                        'Call qualification_echo exactly once with value "qualification".'
                    )
                    request["tool_choice"] = "auto"
                    if case_id == "tool_choice:required":
                        request["tool_choice"] = "required"
                    elif case_id == "tool_choice:specific":
                        request["tool_choice"] = {"type": "function", "function": {"name": "qualification_echo"}}
                if case_id == "tool_continue" and tool_call:
                    request["messages"] += [{"role": "assistant", "content": None,
                                              "tool_calls": [tool_call]},
                                             {"role": "tool", "tool_call_id": tool_call["id"],
                                              "content": "qualification"}]
                elif case_id == "tool_continue":
                    cases.append(ModelQualificationCase(case_id=case_id, status="failed",
                                                       request_fingerprint=fingerprint(request)))
                    continue
                if case_id == "images":
                    request["messages"][0]["content"] = [
                        {"type": "text", "text": 'Identify the solid color in this image. Reply only with JSON {"color":"<color>"}.'},
                        {"type": "image_url", "image_url": {"url": IMAGE}},
                    ]
                if case_id.startswith("reasoning:"):
                    if profile.wire_policy.reasoning_mode != "reasoning_effort":
                        cases.append(ModelQualificationCase(case_id=case_id, status="failed",
                                                           request_fingerprint=fingerprint(request)))
                        continue
                    request["reasoning_effort"] = reasoning_wire_value(case_id.split(":", 1)[1])
                    if not profile.wire_policy.preserve_openai_parameters:
                        cases.append(ModelQualificationCase(case_id=case_id, status="not_tested",
                            request_fingerprint=fingerprint(request),
                            not_tested_reason="strict_parameter_forwarding_unverified"))
                        continue
                if case_id in _OPTIONAL_CASES and not profile.wire_policy.preserve_openai_parameters:
                    cases.append(ModelQualificationCase(case_id=case_id, status="not_tested",
                        request_fingerprint=fingerprint(request),
                        not_tested_reason="strict_parameter_forwarding_unverified"))
                    continue
                request = prepare_model_parameters(request, profile)
                if case_id == "workspace_artifact" and self.verification_scope != "staging":
                    cases.append(ModelQualificationCase(case_id=case_id, status="not_tested",
                                                       request_fingerprint=fingerprint(request)))
                    continue
                if self.budget_check:
                    budget = self.budget_check(actor_id, profile.alias, request)
                    if inspect.isawaitable(budget):
                        await budget
                try:
                    result = await self.probe(case_id, request, actor_id=actor_id,
                                              deployment_fingerprint=deployment_fingerprint)
                    encoded = json.dumps(result, ensure_ascii=False).encode()
                    if len(encoded) > self.max_response_bytes:
                        raise ValueError("MODEL_QUALIFICATION_RESPONSE_TOO_LARGE")
                    status = self._status(case_id, result, deployment_fingerprint, tool_call)
                    if case_id == "tools":
                        tool_call = _tool_call(result)
                    cases.append(ModelQualificationCase(
                        case_id=case_id, status=status,
                        request_fingerprint=fingerprint(request),
                        response_fingerprint=fingerprint(result),
                        usage_known=_usage_known(result) if case_id.startswith("usage") else None,
                        not_tested_reason=(result.get("not_tested_reason") if status == "not_tested" else None),
                    ))
                except (TimeoutError, ValueError, TypeError, KeyError, IndexError,
                        AttributeError, OSError, httpx.HTTPError):
                    # Do not copy provider exception strings; they may contain credentials.
                    cases.append(ModelQualificationCase(case_id=case_id, status="failed",
                                                       request_fingerprint=fingerprint(request)))
        # Required/specific are recorded capabilities, not requirements of Pi's
        # automatic tool mode. Existing profiles never declared either mode.
        core = [case for case in cases if case.case_id not in _OPTIONAL_CASES]
        failed = any(case.status == "failed" for case in core)
        complete = all(case.status in {"passed", "unknown"} for case in core)
        return ModelQualificationEvidence(
            evidence_id=f"model-qualification-{uuid.uuid4().hex}", alias=profile.alias,
            profile_revision=profile.revision, profile_fingerprint=profile.candidate_fingerprint(),
            deployment_fingerprint=deployment_fingerprint,
            provider_url_fingerprint=self.provider_url_fingerprint,
            pi_version=self.pi_version, gateway_version=self.gateway_version,
            checked_at=datetime.now(UTC).isoformat(), verification_scope=self.verification_scope,
            status="failed" if failed else "passed" if complete else "degraded",
            cases=tuple(cases),
        )

    @staticmethod
    def _status(case_id, result, deployment_fingerprint, tool_call):
        if not isinstance(result, dict):
            return "failed"
        if case_id.startswith("reasoning:") and result.get("not_tested_reason") == "strict_parameter_forwarding_unverified":
            return "not_tested"
        if case_id == "images":
            passed = _image_valid(result)
        elif case_id == "text" or case_id.startswith("reasoning:"):
            passed = _content_valid(result)
        elif case_id == "stream":
            passed = _stream_valid(result)
        elif case_id == "streaming_tools":
            passed = _stream_valid(result, tools=True)
        elif case_id == "tools" or case_id.startswith("tool_choice:"):
            passed = _tool_call(result) is not None
        elif case_id == "tool_continue":
            passed = bool(tool_call) and _content_valid(result)
        elif case_id == "usage":
            return "passed" if _usage_known(result) else "unknown"
        elif case_id == "usage_missing":
            return "unknown" if result.get("usage") is None else "failed"
        elif case_id.startswith("errors:"):
            expected = case_id.split(":", 1)[1]
            passed = (result.get("status") == expected if expected == "timeout"
                      else result.get("status") == int(expected))
        elif case_id == "errors":
            passed = result.get("normalized") is True
        elif case_id == "alias_routing":
            passed = (result.get("deployment_fingerprint") == deployment_fingerprint
                      and result.get("all_deployments_verified") is True)
        elif case_id == "workspace_artifact":
            passed = all(result.get(key) is True for key in (
                "write_verified", "read_verified", "artifact_verified",
                "owner_verified", "usage_settled_once",
            ))
        else:
            passed = False
        return "passed" if passed else "failed"
