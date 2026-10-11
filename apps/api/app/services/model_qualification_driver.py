"""Trusted, bounded qualification over the existing gateway, ledger and Pi boundary.

The deployment gate and audit account are server configuration. API callers can
select a stored candidate revision, never a URL, credential, prompt or outcome.
Injected transports and loopback fixtures always produce offline evidence.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

import httpx
from starlette.responses import JSONResponse, Response

from app.adapters.live.sandbox_pi import SandboxPiRunner
from app.contracts.admin import ModelDraft
from app.contracts.conversation import ArtifactCreatedData, ArtifactCreatedEvent
from app.contracts.model_runtime import ModelQualificationCase, ModelRuntimeProfile, fingerprint
from app.domain.admin.model_qualification import (
    ModelQualification,
    _content_valid,
    _tool_call,
    _usage_known,
)
from app.domain.quota import TokenQuotaExceeded
from app.services.model_gateway import (
    acquire_gateway_client,
    prepare_model_parameters,
    read_gateway_error,
)
from app.services.workspace_files import WorkspaceOperationContext

_FAULT_CODES = {401: "MODEL_GATEWAY_AUTH_FAILED", 402: "MODEL_GATEWAY_QUOTA_EXHAUSTED",
                403: "MODEL_GATEWAY_AUTH_FAILED", 429: "MODEL_GATEWAY_RATE_LIMITED",
                500: "MODEL_GATEWAY_UNAVAILABLE", 503: "MODEL_GATEWAY_UNAVAILABLE"}
_ROUTE_FIELDS = {"api_base", "api_key", "base_url", "deployment_id", "litellm_params",
                 "provider", "custom_llm_provider", "model_list", "fallbacks", "retries",
                 "drop_params", "allowed_openai_params", "additional_drop_params", "extra_body"}
_WORKSPACE_TOOLS = [
    {"type": "function", "function": {"name": "write_file", "description": "Write the fixed qualification artifact.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"},
            "content": {"type": "string"}}, "required": ["path", "content"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read the fixed qualification artifact.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
            "required": ["path"], "additionalProperties": False}}},
]


def _url(value):
    if not isinstance(value, str):
        raise TypeError("MODEL_QUALIFICATION_ROUTE_UNVERIFIED")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("MODEL_QUALIFICATION_ROUTE_UNVERIFIED")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _loopback(value):
    hostname = urlsplit(value).hostname
    if hostname in {"localhost", "localhost.localdomain"}:
        return True
    try:
        return ipaddress.ip_address(hostname or "").is_loopback
    except ValueError:
        return False


def _strict_openai_route(route):
    # Native providers can remap effort to thinking/other fields. Only these
    # pinned adapters have offline proof of unchanged OpenAI parameter wire.
    return route["model"].split("/", 1)[0] in {"openai", "custom_openai"}


def _verify_parameter_policy(profile, routes):
    if (profile.wire_policy.preserve_openai_parameters is True
            and any(not _strict_openai_route(route) for route in routes)):
        raise ValueError("MODEL_QUALIFICATION_PARAMETER_POLICY_UNVERIFIED")


async def _finish(task, *, completed=None):
    """Wait for a started side effect, then propagate deferred cancellation.

    Cancelling to_thread only cancels its waiter. A committed reservation must
    be observed before the caller decides which guard to release or retain.
    """
    cancellation = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            cancellation = exc
    result = task.result()
    if completed is not None:
        completed(result)
    if cancellation is not None:
        raise cancellation
    return result


@dataclass
class _Evaluation:
    profile: ModelRuntimeProfile
    actor_id: str
    deployment_fingerprint: str
    revision: int
    routes: tuple[dict, ...]
    client: httpx.AsyncClient
    session_id: str
    scope: str
    evaluation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    verified: set[str] = field(default_factory=set)
    tool_calls: dict[str, dict] = field(default_factory=dict)
    attempts: list[tuple[str, int | None]] = field(default_factory=list)
    admission_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    admitted_attempts: int = 0
    reserved_tokens: int = 0
    observed_tokens: int = 0
    poisoned: bool = False
    closed: bool = False


class ModelQualificationDriver:
    def __init__(self, catalog, model_policy, conversations, agent_service, *, qualification_user_id,
                 enabled=False, deployment_environment="offline", pi_version="unverified",
                 gateway_version="unverified", http_client=None, max_attempts=48,
                 max_total_tokens=32_768, timeout_seconds=120, max_response_bytes=256 * 1024,
                 max_request_bytes=16 * 1024, offline_workspace=False):
        if (deployment_environment not in {"offline", "staging"} or not 1 <= max_attempts <= 64
                or not 1 <= max_total_tokens <= 131_072 or not 0 < timeout_seconds <= 120
                or not 256 <= max_response_bytes <= 1024 * 1024
                or not 256 <= max_request_bytes <= 64 * 1024):
            raise ValueError("MODEL_QUALIFICATION_LIMIT_INVALID")
        self.catalog, self.policy, self.store, self.agent = catalog, model_policy, conversations, agent_service
        self.user_id, self.enabled, self.environment = qualification_user_id, enabled, deployment_environment
        self.pi_version, self.gateway_version, self.http_client = pi_version, gateway_version, http_client
        self.max_attempts, self.max_tokens, self.timeout = max_attempts, max_total_tokens, timeout_seconds
        self.max_response_bytes, self.max_request_bytes = max_response_bytes, max_request_bytes
        self.offline_workspace = offline_workspace
        self._lock = asyncio.Lock()
        self._active_pi: dict[str, tuple[_Evaluation, dict]] = {}
        self._proxy_tasks: dict[str, set[asyncio.Task]] = {}
        self._lease_owner = "qualification-" + uuid.uuid4().hex

    async def _ledger(self, operation, *args, completed=None):
        return await _finish(asyncio.create_task(asyncio.to_thread(operation, *args)), completed=completed)

    def _candidate(self, profile, actor_id, revision=None):
        permissions = set(self.policy.store.principal(actor_id).permissions)
        if not {"models:write", "models:publish"} <= permissions:
            raise PermissionError("MODEL_QUALIFICATION_FORBIDDEN")
        row = self.policy.policy(profile.alias)
        if not row or not row[2]:
            raise ValueError("MODEL_QUALIFICATION_STALE")
        current = ModelDraft.model_validate_json(row[2]).runtime_profile
        if (current is None or current.candidate_fingerprint() != profile.candidate_fingerprint()
                or revision is not None and row[0] != revision):
            raise ValueError("MODEL_QUALIFICATION_STALE")
        return row[0]

    async def _read(self, response):
        raw = bytearray()
        async for fragment in response.aiter_bytes():
            if len(raw) + len(fragment) > self.max_response_bytes:
                raise ValueError("MODEL_QUALIFICATION_RESPONSE_TOO_LARGE")
            raw.extend(fragment)
        return bytes(raw)

    async def _discovery(self, client, alias):
        async def get(path):
            async with client.stream("GET", self.catalog.base_url + path,
                                     headers={"Authorization": f"Bearer {self.catalog.api_key}"},
                                     timeout=min(self.timeout, 10)) as response:
                if response.status_code != 200:
                    raise ValueError("MODEL_QUALIFICATION_DISCOVERY_FAILED")
                return json.loads(await self._read(response))
        listing = await get("/v1/models")
        if not any(item.get("id") == alias for item in listing.get("data", []) if isinstance(item, dict)):
            raise ValueError("MODEL_QUALIFICATION_STALE")
        details = await get("/model/info")
        routes = []
        for item in details.get("data", []):
            if not isinstance(item, dict) or (item.get("model_name") or item.get("id")) != alias:
                continue
            metadata, params = item.get("model_info") or {}, item.get("litellm_params") or {}
            if not isinstance(metadata, dict) or not isinstance(params, dict):
                raise TypeError("MODEL_QUALIFICATION_ROUTE_UNVERIFIED")
            identifier = metadata.get("id")
            if (not isinstance(identifier, str) or not 0 < len(identifier) <= 200
                    or not isinstance(params.get("model"), str)):
                raise ValueError("MODEL_QUALIFICATION_ROUTE_UNVERIFIED")
            _url(params.get("api_base"))
            routes.append({"metadata": metadata, "model": params["model"], "api_base": params["api_base"]})
        if not routes or len(routes) > 8 or len({route["metadata"]["id"] for route in routes}) != len(routes):
            raise ValueError("MODEL_QUALIFICATION_ROUTE_UNVERIFIED")
        return tuple(routes)

    def _routes_fingerprint(self, alias, routes):
        return fingerprint({"gateway": self.catalog.base_url, "alias": alias, "deployments": routes})

    async def _current(self, state):
        if state.poisoned or state.closed:
            raise ValueError("MODEL_QUALIFICATION_STALE")
        try:
            await asyncio.to_thread(self._candidate, state.profile, state.actor_id, state.revision)
            routes = await self._discovery(state.client, state.profile.alias)
            if (self._routes_fingerprint(state.profile.alias, routes) != state.deployment_fingerprint
                    or self.catalog.deployment_fingerprint(state.profile.alias) != state.deployment_fingerprint):
                raise ValueError("MODEL_QUALIFICATION_STALE")
        except (ValueError, TypeError, PermissionError):
            state.poisoned = True
            raise

    async def _source_current(self, state, source_run_id):
        if source_run_id is None:
            return
        registered = self._active_pi.get(source_run_id)
        if registered is None or registered[0] is not state or state.closed:
            raise ValueError("MODEL_QUALIFICATION_FORBIDDEN")
        status = await asyncio.to_thread(self.store.run_status_for, self.user_id, source_run_id)
        if (status is None or status.status != "running"
                or self._active_pi.get(source_run_id) is not registered or state.closed):
            raise ValueError("MODEL_QUALIFICATION_FORBIDDEN")

    async def _drain_proxy(self, run_id):
        self._active_pi.pop(run_id, None)
        tasks = tuple(task for task in self._proxy_tasks.pop(run_id, ())
                      if task is not asyncio.current_task())
        for task in tasks:
            task.cancel()
        if tasks:
            async def wait():
                await asyncio.gather(*tasks, return_exceptions=True)
            await _finish(asyncio.create_task(wait()))

    def _scope(self, client, routes):
        injected = (self.catalog.transport is not None
                    or not isinstance(client._transport, httpx.AsyncHTTPTransport)
                    or any(transport is not None and not isinstance(transport, httpx.AsyncHTTPTransport)
                           for transport in client._mounts.values()))
        if (self.environment != "staging" or injected or _loopback(self.catalog.base_url)
                or isinstance(self.agent.runner, SandboxPiRunner) and _loopback(self.agent.runner.manager_url)
                or any(_loopback(route["api_base"]) for route in routes)):
            return "offline"
        if (not isinstance(self.agent.runner, SandboxPiRunner)
                or self.agent.runner.manager_transport is not None
                or self.agent.runner.bridge_transport is not None
                or self.pi_version == "unverified" or self.gateway_version == "unverified"
                or self.agent.workspace_sandbox_provider is None
                or self.agent.workspace_attempts is None or self.agent.workspace_transfer is None):
            raise ValueError("MODEL_QUALIFICATION_STAGING_UNAVAILABLE")
        identity = getattr(self.store, "identity_policy", None)
        if identity is None or identity.tier_for(self.user_id) != "member":
            raise PermissionError("MODEL_QUALIFICATION_ACCOUNT_REQUIRED")
        return "staging"

    def _new_run(self, state, case_id):
        run_id = str(uuid.uuid4())
        now = datetime.now(UTC)
        context = {"purpose": "model_qualification", "model_id": state.profile.alias,
                   "profile_fingerprint": state.profile.candidate_fingerprint(),
                   "deployment_fingerprint": state.deployment_fingerprint,
                   "evaluation_id": state.evaluation_id, "case_id": case_id}
        # Running from the initial commit prevents the normal Agent scheduler
        # from claiming these internal ledger scopes. No prompt/body is stored.
        with self.store._immediate_transaction():
            self.store.check_admission()
            self.store._require_not_deleting(self.user_id)
            self.store.db.execute(
                "INSERT INTO agent_runs (id,user_id,session_id,status,created_at,context_json,"
                "lease_owner,lease_expires_at) VALUES (?,?,?,'running',?,?,?,?)",
                (run_id, self.user_id, state.session_id, now.isoformat(), json.dumps(context),
                 self._lease_owner, (now + timedelta(seconds=self.timeout + 30)).isoformat()),
            )
        return run_id

    def _decode(self, raw, streaming):
        if not streaming:
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError("MODEL_QUALIFICATION_RESPONSE_INVALID")
            return result
        chunks, usage, done = [], None, False
        normalized = raw.replace(b"\r\n", b"\n")
        for event in normalized.split(b"\n\n"):
            data = b"\n".join(line[5:].lstrip(b" ") for line in event.split(b"\n") if line.startswith(b"data:"))
            if not data:
                continue
            if data == b"[DONE]":
                if done or not normalized.endswith(b"\n\n"):
                    raise ValueError("MODEL_QUALIFICATION_STREAM_INVALID")
                done = True
                continue
            if done or len(chunks) >= 2048:
                raise ValueError("MODEL_QUALIFICATION_STREAM_INVALID")
            chunk = json.loads(data)
            if not isinstance(chunk, dict) or "error" in chunk:
                raise ValueError("MODEL_QUALIFICATION_STREAM_INVALID")
            chunks.append(chunk)
            if chunk.get("usage") is not None:
                if usage is not None and usage != chunk["usage"]:
                    raise ValueError("MODEL_QUALIFICATION_USAGE_AMBIGUOUS")
                usage = chunk["usage"]
        if not done:
            raise ValueError("MODEL_QUALIFICATION_STREAM_INVALID")
        return {"chunks": chunks, **({"usage": usage} if usage is not None else {})}

    async def _request(self, state, route, body, case_id, *, raw_response=False, source_run_id=None,
                       use_alias=False):
        await self._current(state)
        _verify_parameter_policy(state.profile, state.routes)
        payload = prepare_model_parameters(json.loads(json.dumps(body)), state.profile)
        payload["model"] = state.profile.alias if use_alias else route["metadata"]["id"]
        payload["user"] = self.user_id
        field_name = state.profile.wire_policy.chat_max_tokens_field
        requested = payload.pop("max_tokens", payload.pop("max_completion_tokens", 64))
        if type(requested) is not int or requested < 1:
            raise ValueError("MODEL_QUALIFICATION_REQUEST_INVALID")
        payload[field_name] = min(requested, state.profile.max_output_tokens or 128, 128)
        if payload.get("stream") and state.profile.wire_policy.stream_usage_mode == "supported":
            payload["stream_options"] = {"include_usage": True}
        else:
            payload.pop("stream_options", None)
        encoded = json.dumps(payload, ensure_ascii=False).encode()
        estimate = len(encoded) * 2 + payload[field_name]
        async with state.admission_lock:
            await self._source_current(state, source_run_id)
            if (len(encoded) > self.max_request_bytes or state.admitted_attempts >= self.max_attempts
                    or state.reserved_tokens + estimate > self.max_tokens
                    or state.observed_tokens > self.max_tokens):
                raise ValueError("MODEL_QUALIFICATION_BUDGET_EXCEEDED")
            # Claim before the first ledger await. Claims remain consumed even
            # when quota rejects or cancellation prevents provider dispatch.
            state.admitted_attempts += 1
            state.reserved_tokens += estimate
        run_id = None
        call_id = uuid.uuid4().hex
        dispatched, rejected, observed = False, False, None
        reserved = False
        def created(value):
            nonlocal run_id
            run_id = value
        def held(_value):
            nonlocal reserved
            reserved = True
        try:
            await self._ledger(self._new_run, state, case_id, completed=created)
            await self._ledger(self.store.reserve_resume_tokens, self.user_id, run_id, 1, completed=held)
            maximum = await self._ledger(self.store.reserve_model_call, self.user_id, run_id,
                                         call_id, len(encoded), payload[field_name])
            payload[field_name] = maximum
            state.attempts.append((run_id, None))
            index = len(state.attempts) - 1
            await self._current(state)
            await self._source_current(state, source_run_id)
            dispatched = True
            async with state.client.stream("POST", self.catalog.base_url + "/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.catalog.api_key}", "X-LiteLLM-End-User-ID": self.user_id},
                    json=payload, timeout=min(self.timeout, 30)) as response:
                if response.status_code >= 400:
                    error = await read_gateway_error(response)
                    rejected = error.definite_rejection
                    raise ValueError(error.code)
                raw = await self._read(response)
                result = self._decode(raw, bool(payload.get("stream")))
                if _usage_known(result):
                    observed = result["usage"]["prompt_tokens"] + result["usage"]["completion_tokens"]
                    state.observed_tokens += observed
                    state.attempts[index] = (run_id, observed)
                if use_alias:
                    route = next((candidate for candidate in state.routes
                                  if candidate["metadata"]["id"] == response.headers.get("x-litellm-model-id")), None)
                    if route is None:
                        raise ValueError("MODEL_QUALIFICATION_ROUTE_UNVERIFIED")
                if (response.headers.get("x-litellm-model-id") != route["metadata"]["id"]
                        or response.headers.get("x-litellm-version") != self.gateway_version
                        or _url(response.headers.get("x-litellm-model-api-base")) != _url(route["api_base"])):
                    raise ValueError("MODEL_QUALIFICATION_ROUTE_UNVERIFIED")
                state.verified.add(route["metadata"]["id"])
                return (result, raw, response.headers.get("content-type", "application/json")) if raw_response else result
        except TokenQuotaExceeded as exc:
            raise ValueError("MODEL_QUALIFICATION_BUDGET_EXCEEDED") from exc
        finally:
            if run_id is not None:
                await self._ledger(self._settle_request, run_id, call_id, reserved, observed, rejected, dispatched)

    def _settle_request(self, run_id, call_id, reserved, observed, rejected, dispatched):
        # One actual call per Run makes retry/unknown settlement independent.
        # This whole cleanup finishes before cancellation leaves the caller.
        try:
            if reserved:
                if observed is not None and observed > 0:
                    self.store.record_model_attempt(self.user_id, run_id, observed, "completed")
                elif rejected or observed == 0 or not dispatched:
                    self.store.release_model_call(self.user_id, run_id, call_id)
                else:
                    self.store.record_unreported_model_call(self.user_id, run_id)
                self.store.settle_current_tokens(self.user_id, run_id,
                    refund_if_unreported=rejected or observed == 0 or not dispatched)
        finally:
            self.store.set_run_status(run_id, "completed" if observed is not None else "failed")

    async def _fault(self, case_id):
        # Negative protocol cases exercise the same local normalization adapter;
        # no failed credential, artificial timeout or bad status reaches a provider.
        async def adapter(request):
            if case_id == "errors:timeout":
                raise httpx.ReadTimeout("local qualification timeout", request=request)
            status = int(case_id.split(":")[1]) if case_id.startswith("errors:") else 200
            return httpx.Response(status, json={"error": {"code": "local_adapter"}} if status >= 400 else
                                  {"choices": [{"message": {"content": "qualification"}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(adapter), trust_env=False) as client:
            try:
                async with client.stream("POST", "http://qualification.invalid/v1/chat/completions", json={}) as response:
                    if response.status_code >= 400:
                        error = await read_gateway_error(response)
                        return {"status": error.status_code, "normalized": error.code == _FAULT_CODES[error.status_code]}
                    return self._decode(await self._read(response), False)
            except httpx.TimeoutException:
                return {"status": "timeout", "normalized": True}

    async def _probe(self, state, case_id, body, **_context):
        if case_id.startswith("errors:") or case_id == "usage_missing":
            return await self._fault(case_id)
        if case_id == "errors":
            checks = [await self._fault(f"errors:{status}") for status in (*_FAULT_CODES, "timeout")]
            return {"normalized": all(check.get("normalized") is True for check in checks)}
        if case_id == "alias_routing":
            await self._current(state)
            result = await self._request(state, state.routes[0], body, case_id, use_alias=True)
            expected = {route["metadata"]["id"] for route in state.routes}
            return {"deployment_fingerprint": state.deployment_fingerprint,
                    "all_deployments_verified": (bool(expected) and state.verified == expected
                                                 and _content_valid(result)),
                    "response_fingerprint": fingerprint(result)}
        if case_id == "workspace_artifact":
            return await self._workspace(state)
        if case_id.startswith("reasoning:") and (
                not state.profile.wire_policy.preserve_openai_parameters
                or any(not _strict_openai_route(route) for route in state.routes)):
            return {"not_tested_reason": "strict_parameter_forwarding_unverified"}
        results = []
        for route in state.routes:
            request = json.loads(json.dumps(body))
            identifier = route["metadata"]["id"]
            if case_id == "tool_continue":
                call = state.tool_calls.get(identifier)
                if call is None:
                    raise ValueError("MODEL_QUALIFICATION_TOOL_CONTINUE_INVALID")
                request["messages"] = request["messages"][:1] + [
                    {"role": "assistant", "content": None, "tool_calls": [call]},
                    {"role": "tool", "tool_call_id": call["id"], "content": "qualification"},
                ]
            result = await self._request(state, route, request, case_id)
            if ModelQualification._status(case_id, result, state.deployment_fingerprint,
                                          state.tool_calls.get(identifier)) == "failed":
                raise ValueError("MODEL_QUALIFICATION_PROTOCOL_FAILED")
            if case_id == "tools":
                state.tool_calls[identifier] = _tool_call(result)
            results.append(result)
        result = results[0]
        if not all(_usage_known(item) for item in results):
            result = {key: value for key, value in result.items() if key != "usage"}
        return {**result, "deployment_checks": [
            {"deployment_fingerprint": fingerprint(route["metadata"]["id"]),
             "response_fingerprint": fingerprint(item)} for route, item in zip(state.routes, results, strict=True)
        ]}

    async def proxy(self, request, *, authorization=None):
        bearer = authorization.removeprefix("Bearer ") if authorization else ""
        run_id, separator, token = bearer.partition(".")
        registered = self._active_pi.get(run_id)
        if registered is None:
            return None
        state, route = registered
        task = asyncio.current_task()
        self._proxy_tasks.setdefault(run_id, set()).add(task)
        try:
            context = await asyncio.to_thread(self.store.run_context, run_id)
            status = await asyncio.to_thread(self.store.run_status_for, self.user_id, run_id)
            owner = await asyncio.to_thread(self.store.owner_for_run, run_id)
            if (not separator or not self.agent.verify_tool_token(run_id, token)
                    or owner != self.user_id
                    or context.get("purpose") != "model_qualification"
                    or context.get("evaluation_id") != state.evaluation_id
                    or status is None or status.status != "running"):
                return JSONResponse({"error": {"code": "MODEL_QUALIFICATION_FORBIDDEN"}}, status_code=403)
            raw = await request.body()
            if not raw or len(raw) > self.max_request_bytes:
                raise ValueError("MODEL_QUALIFICATION_REQUEST_INVALID")
            body = json.loads(raw)
            if (not isinstance(body, dict) or body.get("model") != state.profile.alias
                    or _ROUTE_FIELDS.intersection(body)):
                raise ValueError("MODEL_QUALIFICATION_REQUEST_INVALID")
            body["tools"] = _WORKSPACE_TOOLS
            _result, raw, content_type = await self._request(state, route, body, "workspace_artifact",
                raw_response=True, source_run_id=run_id)
            return Response(raw, media_type=content_type)
        except (ValueError, TypeError, LookupError, PermissionError, httpx.HTTPError):
            return JSONResponse({"error": {"code": "MODEL_QUALIFICATION_FAILED"}}, status_code=403)
        finally:
            self._proxy_tasks.get(run_id, set()).discard(task)

    def _settled(self, attempts):
        if not attempts:
            return False
        for run_id, observed in attempts:
            if observed is None:
                return False
            guard = self.store.db.execute("SELECT status FROM agent_model_call_guards WHERE run_id=?", (run_id,)).fetchall()
            entries = self.store.db.execute("SELECT amount FROM agent_token_entries WHERE run_id=? AND kind='model_attempt'", (run_id,)).fetchall()
            _id, _period, _reserved, _observed, charged = self.store._latest_token_reservation(self.user_id, run_id)
            if (guard != [("reported" if observed > 0 else "rejected",)]
                    or entries != ([(observed,)] if observed > 0 else []) or charged != observed):
                return False
        return True

    def _artifact_created(self, run_id, artifact):
        event = ArtifactCreatedEvent(run_id=run_id, data=ArtifactCreatedData(
            artifact_id=artifact.id, name=artifact.name, kind=artifact.kind))
        if not self.store.append_events(self.user_id, run_id, [event], lease_owner=self._lease_owner):
            return False
        created = [item for item in self.store.events_for(self.user_id, run_id, None) or []
                   if isinstance(item, ArtifactCreatedEvent) and item.data.artifact_id == artifact.id]
        return len(created) == 1 and created[0].data == event.data

    async def _workspace(self, state):
        if state.scope != "staging" and not self.offline_workspace:
            raise ValueError("MODEL_QUALIFICATION_WORKSPACE_UNAVAILABLE")
        results = [await self._workspace_one(state, route) for route in state.routes]
        return {key: all(result.get(key) is True for result in results) for key in (
            "write_verified", "read_verified", "artifact_verified", "owner_verified", "usage_settled_once")}

    async def _workspace_one(self, state, route):
        await self._current(state)
        run_id = None
        turn, succeeded = None, False
        start = len(state.attempts)
        starts, ends = set(), set()
        def created(value):
            nonlocal run_id
            run_id = value
        def opened(value):
            nonlocal turn
            turn = value
        try:
            await self._ledger(self._new_run, state, "workspace_artifact", completed=created)
            await _finish(asyncio.create_task(self.agent._open_workspace_attempt(
                self.user_id, state.session_id, run_id, required=True)), completed=opened)
            if turn is None:
                raise ValueError("MODEL_QUALIFICATION_WORKSPACE_UNAVAILABLE")
            attempt_id, capabilities = turn
            path = f"artifacts/{attempt_id}/qualification.txt"
            capabilities = replace(capabilities, command_execution=False, command_events=False, cancellation=False)
            environment = {"PSKIT_USER_ID": self.user_id, "PSKIT_RUN_ID": run_id,
                "PSKIT_SESSION_ID": state.session_id, "PSKIT_ATTEMPT_ID": attempt_id,
                "PSKIT_AGENT_TOOL_TOKEN": self.agent.tool_token(run_id),
                "PSKIT_WORKSPACE_TOOL_TOKEN": self.agent.workspace_tool_token(user_id=self.user_id,
                    run_id=run_id, session_id=state.session_id, attempt_id=attempt_id),
                "PSKIT_WORKSPACE_CAPABILITIES_JSON": json.dumps({"file_access": capabilities.file_access,
                    "command_execution": False, "command_events": False, "cancellation": False,
                    "runtime": capabilities.runtime, "session_mount_namespace": capabilities.session_mount_namespace}),
                "PSKIT_INTERNAL_API_URL": self.agent.internal_api_url,
                "PSKIT_MODEL_ID": state.profile.alias,
                "PSKIT_MODEL_RUNTIME_PROFILE": state.profile.model_dump_json(),
                "MODEL_GATEWAY_API_KEY": f"{run_id}.{self.agent.tool_token(run_id)}",
                                "PSKIT_AF3_ENABLED": "0", "PSKIT_MCP_TOOLS_JSON": "[]",
                                "PSKIT_COMPUTE_CAPABILITIES_JSON": "[]", "PSKIT_COMPUTE_TOOLS_JSON": "[]"}
            def event(item):
                name = item.get("toolName")
                if item.get("type") == "tool_execution_start":
                    expected = {"path": path, **({"content": "qualification"} if name == "write_file" else {})}
                    if name not in {"write_file", "read_file"} or item.get("args") != expected:
                        raise ValueError("MODEL_QUALIFICATION_WORKSPACE_INVALID")
                    starts.add(name)
                elif item.get("type") == "tool_execution_end":
                    if name in starts and item.get("isError") is not True:
                        ends.add(name)
            self._active_pi[run_id] = (state, route)
            await self.agent.runner.prompt(state.session_id,
                f'Call write_file with path "{path}" and content "qualification", then call read_file on that exact path. Reply only qualification.',
                event, environment=environment, system_prompt_suffix="Use only the fixed write_file/read_file tools. Never execute commands.")
            if starts != {"write_file", "read_file"} or ends != starts:
                raise ValueError("MODEL_QUALIFICATION_WORKSPACE_INVALID")
            operation = WorkspaceOperationContext(self.user_id, state.session_id, attempt_id)
            transfer = self.agent.workspace_transfer
            raw = await transfer.files.read_all(operation, path, max_bytes=128)
            if raw != b"qualification":
                raise ValueError("MODEL_QUALIFICATION_WORKSPACE_INVALID")
            refs = await transfer.collect(self.user_id, state.session_id, attempt_id)
            if len(refs) != 1 or refs[0].name != "qualification.txt":
                raise ValueError("MODEL_QUALIFICATION_ARTIFACT_INVALID")
            ref = refs[0]
            owned = await asyncio.to_thread(transfer.artifacts.list, self.user_id, state.session_id)
            downloaded = await asyncio.to_thread(transfer.artifacts.read, self.user_id, ref.id)
            denied = await asyncio.to_thread(transfer.artifacts.read, "qualification-denied-" + uuid.uuid4().hex, ref.id)
            metadata = next((item for item in owned if item.id == ref.id), None)
            if (metadata is None or not metadata.available or metadata.size != len(raw)
                    or metadata.sha256 != hashlib.sha256(raw).hexdigest()
                    or downloaded != ("qualification.txt", raw) or denied is not None
                    or self.store.project_id_for_session(self.user_id, state.session_id) is None):
                raise ValueError("MODEL_QUALIFICATION_ARTIFACT_INVALID")
            if not await self._ledger(self._artifact_created, run_id, ref):
                raise ValueError("MODEL_QUALIFICATION_ARTIFACT_INVALID")
            await self._current(state)
            succeeded = True
            return {"write_verified": "write_file" in ends, "read_verified": "read_file" in ends,
                    "artifact_verified": metadata is not None, "owner_verified": denied is None,
                    "usage_settled_once": await asyncio.to_thread(self._settled, state.attempts[start:])}
        finally:
            async def close():
                try:
                    await self._drain_proxy(run_id)
                    if turn is not None:
                        await self.agent._close_workspace_attempt_safely(
                            self.user_id, state.session_id, run_id, turn, succeeded=succeeded)
                finally:
                    if run_id is not None:
                        await self._ledger(self.store.set_run_status, run_id, "completed" if succeeded else "failed")
            await _finish(asyncio.create_task(close()))

    async def evaluate(self, profile, *, actor_id, deployment_fingerprint):
        if not self.enabled:
            raise ValueError("MODEL_QUALIFICATION_DISABLED")
        if not self.user_id or self.user_id == actor_id:
            raise ValueError("MODEL_QUALIFICATION_ACCOUNT_REQUIRED")
        async with self._lock, asyncio.timeout(self.timeout):
            revision = await asyncio.to_thread(self._candidate, profile, actor_id)
            shared_client = self.http_client() if callable(self.http_client) else self.http_client
            lease = acquire_gateway_client(client=shared_client, transport=self.catalog.transport)
            state = None
            try:
                routes = await self._discovery(lease.client, profile.alias)
                if self._routes_fingerprint(profile.alias, routes) != deployment_fingerprint:
                    raise ValueError("MODEL_QUALIFICATION_STALE")
                _verify_parameter_policy(profile, routes)
                scope = self._scope(lease.client, routes)
                project = await self._ledger(self.store.create_project, self.user_id, "Model qualification audit", "Internal fixed protocol probes")
                session = await self._ledger(self.store.create_session, self.user_id, project.id, "Model qualification audit")
                state = _Evaluation(profile, actor_id, deployment_fingerprint, revision, routes, lease.client, session.id, scope)
                async def probe(case_id, body, **context):
                    return await self._probe(state, case_id, body, **context)
                async def gate(_actor, _alias, _body):
                    await self._current(state)
                qualifier = ModelQualification(probe, pi_version=self.pi_version, gateway_version=self.gateway_version,
                    provider_url_fingerprint=fingerprint([_url(route["api_base"]) for route in routes]),
                    verification_scope=scope, budget_check=gate, timeout_seconds=self.timeout,
                    max_response_bytes=self.max_response_bytes)
                evidence = await qualifier.evaluate(profile, actor_id=actor_id, deployment_fingerprint=deployment_fingerprint)
                if scope == "offline" and self.offline_workspace:
                    result = await self._workspace(state)
                    case = ModelQualificationCase(case_id="workspace_artifact",
                        status=ModelQualification._status("workspace_artifact", result, deployment_fingerprint, None),
                        request_fingerprint=fingerprint("fixed workspace artifact"), response_fingerprint=fingerprint(result))
                    cases = tuple(case if item.case_id == "workspace_artifact" else item for item in evidence.cases)
                    core = [item for item in cases if item.case_id not in {"tool_choice:required", "tool_choice:specific"}]
                    status = ("failed" if any(item.status == "failed" for item in core) else
                              "passed" if all(item.status in {"passed", "unknown"} for item in core) else "degraded")
                    evidence = evidence.model_copy(update={"cases": cases, "status": status})
                await self._current(state)
                return evidence
            finally:
                async def close():
                    try:
                        if state is not None:
                            state.closed = True
                            for run_id, registered in tuple(self._active_pi.items()):
                                if registered[0] is state:
                                    await self._drain_proxy(run_id)
                    finally:
                        await lease.aclose()
                await _finish(asyncio.create_task(close()))
