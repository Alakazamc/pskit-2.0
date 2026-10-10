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

TOOL = {"type": "function", "function": {"name": "qualification_echo", "parameters": {
    "type": "object", "properties": {"value": {"type": "string"}},
    "required": ["value"], "additionalProperties": False,
}}}
IMAGE = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aR3cAAAAASUVORK5CYII="


def _message(document):
    return document["choices"][0]["message"]


def _content_valid(document) -> bool:
    try:
        return isinstance(_message(document).get("content"), str)
    except (KeyError, IndexError, TypeError, AttributeError):
        return False


def _tool_call(document):
    try:
        call = _message(document)["tool_calls"][0]
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
            choices = chunk.get("choices", [])
            if not choices:
                continue  # A final usage-only chunk is permitted.
            if ended:
                return False
            choice = choices[0]
            delta = choice["delta"]
            text += delta.get("content") or ""
            if tools:
                for call in delta.get("tool_calls", []):
                    if call.get("index") != 0:
                        return False
                    call_id += call.get("id") or ""
                    name += call.get("function", {}).get("name") or ""
                    arguments += call.get("function", {}).get("arguments") or ""
            if choice.get("finish_reason") is not None:
                ended = choice["finish_reason"] == ("tool_calls" if tools else "stop")
        if tools:
            return (ended and bool(call_id) and name == "qualification_echo"
                    and json.loads(arguments) == {"value": "qualification"})
        return ended and bool(text)
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
            raise ValueError("MODEL_PROTOCOL_UNAVAILABLE")
        cases = []
        tool_call = None
        base = {"model": profile.alias,
                "messages": [{"role": "user", "content": "Reply with qualification."}],
                profile.wire_policy.chat_max_tokens_field: min(profile.max_output_tokens or 64, 64)}
        names = ["text", "stream", "usage", "usage_missing", "errors:401", "errors:402",
                 "errors:403", "errors:429", "errors:500", "errors:503", "errors:timeout",
                 "errors", "alias_routing"]
        if profile.supports_tools:
            names += ["tools", "tool_continue"]
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
                if case_id in {"tools", "streaming_tools"}:
                    request["tools"] = [TOOL]
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
                        {"type": "text", "text": "Describe this test pixel."},
                        {"type": "image_url", "image_url": {"url": IMAGE}},
                    ]
                if case_id.startswith("reasoning:"):
                    if profile.wire_policy.reasoning_mode != "reasoning_effort":
                        cases.append(ModelQualificationCase(case_id=case_id, status="failed",
                                                           request_fingerprint=fingerprint(request)))
                        continue
                    request["reasoning_effort"] = reasoning_wire_value(case_id.split(":", 1)[1])
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
                    ))
                except (TimeoutError, ValueError, TypeError, KeyError, IndexError,
                        AttributeError, OSError, httpx.HTTPError):
                    # Do not copy provider exception strings; they may contain credentials.
                    cases.append(ModelQualificationCase(case_id=case_id, status="failed",
                                                       request_fingerprint=fingerprint(request)))
        failed = any(case.status == "failed" for case in cases)
        complete = all(case.status in {"passed", "unknown"} for case in cases)
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
        if case_id in {"text", "images"} or case_id.startswith("reasoning:"):
            passed = _content_valid(result)
        elif case_id == "stream":
            passed = _stream_valid(result)
        elif case_id == "streaming_tools":
            passed = _stream_valid(result, tools=True)
        elif case_id == "tools":
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
