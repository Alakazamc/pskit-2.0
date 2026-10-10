"""Offline generic protocol probes use real guards and never grant Agent Ready."""

import asyncio
import json
import threading
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from app.contracts.admin import ModelDraft
from app.contracts.model_runtime import ModelRuntimeProfile, ModelWirePolicy
from app.domain.admin.model_policy import ModelPolicy
from app.domain.admin.roles import AdminStore
from app.domain.persistent_conversation import PersistentConversationStore
from app.services.agent import AgentService
from app.services.model_catalog import ModelCatalog


class Fragments(httpx.AsyncByteStream):
    def __init__(self, raw):
        self.raw, self.closed = raw, False

    async def __aiter__(self):
        for offset in range(0, len(self.raw), 7):
            yield self.raw[offset:offset + 7]

    async def aclose(self):
        self.closed = True


class Provider:
    def __init__(self):
        self.deployments = ["deployment-a", "deployment-b"]
        self.requests, self.streams = [], []
        self.usage = {"prompt_tokens": 3, "completion_tokens": 1}
        self.omit_headers_for = None
        self.after_call = None
        self.block = None
        self.oversize = False

    async def __call__(self, request):
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "document-model"}]})
        if request.url.path == "/model/info":
            return httpx.Response(200, json={"data": [{"model_name": "document-model",
                "model_info": {"id": deployment, "supports_function_calling": True,
                               "supports_vision": True, "supports_reasoning": True},
                "litellm_params": {"model": "provider/document", "api_base": "https://provider.example/v1"},
            } for deployment in self.deployments]})
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer synthetic-gateway-key"
        body = json.loads(request.content)
        self.requests.append(body)
        deployment = body["model"]
        assert deployment in self.deployments
        if self.block:
            self.block[0].set()
            await self.block[1].wait()
        headers = {"x-litellm-model-id": deployment, "x-litellm-version": "1.100.3",
                   "x-litellm-model-api-base": "https://provider.example/v1"}
        if deployment == self.omit_headers_for:
            headers = {}
        tool = (body.get("tools") or [{}])[0].get("function", {}).get("name")
        if tool == "qualification_echo":
            call = {"id": f"{deployment}-tool", "type": "function", "function": {
                "name": tool, "arguments": '{"value":"qualification"}'}}
            message = {"role": "assistant", "content": None, "tool_calls": [call]}
            chunks = [{"choices": [{"delta": {"tool_calls": [{"index": 0, "id": call["id"],
                "function": {"name": "qualification_", "arguments": '{"value":'}}]}, "finish_reason": None}]},
                {"choices": [{"delta": {"tool_calls": [{"index": 0,
                    "function": {"name": "echo", "arguments": '"qualification"}'}}]}, "finish_reason": "tool_calls"}]}]
        else:
            if body["messages"][-1]["role"] == "tool":
                assert body["messages"][-1]["tool_call_id"] == f"{deployment}-tool"
            message = {"role": "assistant", "content": "qualification"}
            chunks = [{"choices": [{"delta": {"content": "quali"}, "finish_reason": None}]},
                      {"choices": [{"delta": {"content": "fication"}, "finish_reason": "stop"}]}]
        if self.after_call:
            self.after_call()
        if self.oversize:
            message["content"] = "x" * 4096
        if body.get("stream"):
            if self.usage is not None:
                chunks.append({"choices": [], "usage": self.usage})
            raw = b"".join(f"data: {json.dumps(chunk)}\n\n".encode() for chunk in chunks)
            raw += b"data: [DONE]\n\n"
            stream = Fragments(raw)
            self.streams.append(stream)
            return httpx.Response(200, headers={**headers, "content-type": "text/event-stream"}, stream=stream)
        return httpx.Response(200, headers=headers, json={"choices": [{"message": message}],
                              **({"usage": self.usage} if self.usage is not None else {})})


@pytest_asyncio.fixture
async def qualification_system(tmp_path):
    provider = Provider()
    transport = httpx.MockTransport(provider)
    catalog = ModelCatalog(base_url="https://gateway.example", api_key="synthetic-gateway-key",
                           default_model="document-model", transport=transport)
    await catalog.list_models(include_fallback=False)
    path = str(tmp_path / "qualification.sqlite3")
    conversations = PersistentConversationStore(path)
    admin = AdminStore(path)
    admin.grant("operator", "platform_admin", actor="test-bootstrap", reason="Synthetic qualification")
    profile = ModelRuntimeProfile(alias="document-model", revision=1, supports_tools=True,
        supports_tool_streaming=True, supports_images=True, reasoning_levels=["off", "high"],
        wire_policy=ModelWirePolicy(reasoning_mode="reasoning_effort", stream_usage_mode="supported"))
    draft = ModelDraft(expected_revision=0, reason="Synthetic qualification", runtime_profile=profile,
                       allowed_user_ids=["qualification-account"])
    with admin.db:
        admin.db.execute("INSERT INTO admin_models (alias,revision,state,draft_json) VALUES (?,?,?,?)",
                         (profile.alias, 1, "draft", draft.model_dump_json()))
    policy = ModelPolicy(admin, catalog, managed=True)
    agent = AgentService(conversations, None, "http://127.0.0.1:9", 0, 0, af3_executor="disabled")
    client = httpx.AsyncClient(transport=transport, trust_env=False)
    from app.services.model_qualification_driver import ModelQualificationDriver

    def driver(**changes):
        return ModelQualificationDriver(catalog, policy, conversations, agent,
            **{"qualification_user_id": "qualification-account", "enabled": True,
               "pi_version": "0.87.1", "gateway_version": "1.100.3", "http_client": client,
               "max_total_tokens": 128_000, **changes})

    try:
        yield SimpleNamespace(provider=provider, catalog=catalog, conversations=conversations,
                              admin=admin, policy=policy, profile=profile, agent=agent,
                              client=client, driver=driver)
    finally:
        await client.aclose()
        admin.db.close()
        conversations.db.close()


async def evaluate(system, driver=None):
    return await (driver or system.driver()).evaluate(system.profile, actor_id="operator",
        deployment_fingerprint=system.catalog.deployment_fingerprint(system.profile.alias))


@pytest.mark.asyncio
async def test_default_gate_is_disabled_before_any_provider_call(qualification_system):
    system = qualification_system
    from app.services.model_qualification_driver import ModelQualificationDriver

    driver = ModelQualificationDriver(system.catalog, system.policy, system.conversations, system.agent,
        qualification_user_id="qualification-account")
    with pytest.raises(ValueError, match="MODEL_QUALIFICATION_DISABLED"):
        await evaluate(system, driver)
    assert system.provider.requests == []


@pytest.mark.asyncio
async def test_each_deployment_runs_fixed_matrix_with_real_usage_guards(qualification_system):
    system = qualification_system
    evidence = await evaluate(system)
    cases = {case.case_id: case for case in evidence.cases}
    assert evidence.verification_scope == "offline"
    assert cases["alias_routing"].status == "passed"
    for case in ("text", "stream", "tools", "tool_continue", "streaming_tools", "images",
                 "reasoning:off", "reasoning:high", "errors", "errors:timeout"):
        assert cases[case].status == "passed", cases[case]
    assert cases["usage_missing"].status == "unknown"
    assert cases["workspace_artifact"].status == "not_tested"
    assert len(system.provider.requests) == 18
    assert {body["model"] for body in system.provider.requests} == set(system.provider.deployments)
    assert all(body.get("max_tokens", 64) <= 128 for body in system.provider.requests)
    guards = system.conversations.db.execute("SELECT run_id,status FROM agent_model_call_guards").fetchall()
    assert len(guards) == 18 and len({row[0] for row in guards}) == 18
    assert {row[1] for row in guards} == {"reported"}
    assert system.conversations.db.execute("SELECT COUNT(*),SUM(amount) FROM agent_token_entries "
        "WHERE kind='model_attempt'").fetchone() == (18, 72)
    assert all(stream.closed for stream in system.provider.streams)
    assert not system.client.is_closed
    assert not system.profile.model_copy(update={"qualification_evidence": evidence,
        "qualification_evidence_id": evidence.evidence_id, "qualification_status": evidence.status}).agent_ready
    output = evidence.model_dump_json()
    for secret in ("synthetic-gateway-key", "qualification_echo", "provider.example", "gateway.example",
                   "data:image", "deployment-a", "qualification-account"):
        assert secret not in output


@pytest.mark.asyncio
async def test_missing_route_headers_never_verify_all_deployments(qualification_system):
    system = qualification_system
    system.provider.omit_headers_for = "deployment-b"
    evidence = await evaluate(system)
    assert evidence.status == "failed"
    assert next(case for case in evidence.cases if case.case_id == "alias_routing").status == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("known_zero", [False, True])
async def test_missing_usage_keeps_hold_and_explicit_zero_is_not_fabricated(qualification_system, known_zero):
    system = qualification_system
    system.provider.usage = {"prompt_tokens": 0, "completion_tokens": 0} if known_zero else None
    await evaluate(system)
    statuses = {row[0] for row in system.conversations.db.execute("SELECT status FROM agent_model_call_guards")}
    assert statuses == ({"rejected"} if known_zero else {"unreported"})
    assert system.conversations.db.execute("SELECT COUNT(*) FROM agent_token_entries "
                                         "WHERE kind='model_attempt'").fetchone()[0] == 0
    used = system.conversations.db.execute("SELECT SUM(used) FROM agent_token_usage").fetchone()[0]
    assert used == 0 if known_zero else used > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("drift", ["deployment", "candidate", "permission"])
async def test_current_state_drift_stops_new_attempts_and_invalidates_evidence(qualification_system, drift):
    system = qualification_system
    def change():
        if drift == "deployment":
            system.provider.deployments.append("deployment-c")
        elif drift == "candidate":
            with system.admin.db:
                system.admin.db.execute("UPDATE admin_models SET revision=revision+1")
        else:
            with system.admin.db:
                system.admin.db.execute("DELETE FROM admin_roles WHERE user_id='operator'")
    system.provider.after_call = change
    with pytest.raises((ValueError, PermissionError)):
        await evaluate(system)
    assert len(system.provider.requests) == 1


@pytest.mark.asyncio
async def test_attempt_and_response_bounds_close_resources_and_keep_accounting(qualification_system):
    system = qualification_system
    evidence = await evaluate(system, system.driver(max_attempts=1, max_response_bytes=1024))
    assert evidence.status == "failed" and len(system.provider.requests) == 1
    assert system.conversations.db.execute("SELECT COUNT(*) FROM agent_model_call_guards").fetchone()[0] == 1
    system.provider.requests.clear()
    system.provider.oversize = True
    evidence = await evaluate(system, system.driver(max_response_bytes=1024))
    assert evidence.status == "failed"
    assert system.conversations.db.execute("SELECT COUNT(*) FROM agent_model_call_guards "
                                         "WHERE status='unreported'").fetchone()[0] > 0


@pytest.mark.asyncio
async def test_cancellation_keeps_unreported_call_hold(qualification_system):
    system = qualification_system
    system.provider.block = (asyncio.Event(), asyncio.Event())
    task = asyncio.create_task(evaluate(system))
    await asyncio.wait_for(system.provider.block[0].wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert system.conversations.db.execute("SELECT status FROM agent_model_call_guards").fetchall() == [("unreported",)]
    assert system.conversations.db.execute("SELECT SUM(used) FROM agent_token_usage").fetchone()[0] > 0
    assert not system.client.is_closed


@pytest.mark.asyncio
async def test_injected_stage_configuration_is_forced_offline(qualification_system):
    system = qualification_system
    evidence = await evaluate(system, system.driver(deployment_environment="staging"))
    assert evidence.verification_scope == "offline"
    assert not system.profile.model_copy(update={"qualification_evidence": evidence,
        "qualification_evidence_id": evidence.evidence_id, "qualification_status": evidence.status}).agent_ready


@pytest.mark.asyncio
async def test_audit_account_quota_prevents_every_provider_call_without_unknown_usage(qualification_system):
    system = qualification_system
    system.conversations.set_token_limit("qualification-account", 1)
    evidence = await evaluate(system)
    assert evidence.status == "failed" and system.provider.requests == []
    assert system.conversations.db.execute("SELECT COUNT(*) FROM agent_model_call_guards").fetchone()[0] == 0
    assert system.conversations.db.execute("SELECT SUM(used) FROM agent_token_usage").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_shared_client_factory_resolves_at_evaluation_and_preserves_pool(qualification_system):
    system = qualification_system
    resolved = []
    def shared_client():
        resolved.append(True)
        return system.client
    await evaluate(system, system.driver(http_client=shared_client))
    assert resolved == [True] and not system.client.is_closed


@pytest.mark.asyncio
async def test_owned_pool_closes_and_unregistered_runs_never_use_qualification_delegate(qualification_system, monkeypatch):
    system = qualification_system
    from app.services import model_qualification_driver as module
    original = module.acquire_gateway_client
    leases = []
    def acquire(**arguments):
        lease = original(**arguments)
        leases.append(lease)
        return lease
    monkeypatch.setattr(module, "acquire_gateway_client", acquire)
    driver = system.driver(http_client=None, max_total_tokens=1)
    request = SimpleNamespace(body=lambda: pytest.fail("Ordinary run body must not be inspected by qualifier"))
    assert await driver.proxy(request, authorization="Bearer ordinary-run.invalid") is None
    evidence = await evaluate(system, driver)
    assert evidence.status == "failed" and system.provider.requests == []
    assert len(leases) == 1 and leases[0].owned and leases[0].client.is_closed


@pytest.mark.asyncio
async def test_observed_over_budget_usage_is_charged_and_stops_further_requests(qualification_system):
    system = qualification_system
    system.provider.usage = {"prompt_tokens": 128_000, "completion_tokens": 1}
    evidence = await evaluate(system)
    assert evidence.status == "failed" and len(system.provider.requests) == 1
    assert system.conversations.db.execute("SELECT SUM(amount) FROM agent_token_entries "
                                         "WHERE kind='model_attempt'").fetchone()[0] == 128_001
    assert system.conversations.db.execute("SELECT SUM(used) FROM agent_token_usage").fetchone()[0] == 128_001


@pytest.mark.asyncio
async def test_trusted_stage_gate_requires_configured_sandbox_runner_and_excludes_loopback(qualification_system):
    system = qualification_system
    driver = system.driver(deployment_environment="staging")
    system.catalog.transport = None
    routes = ({"metadata": {"id": "deployment-a"}, "model": "provider/document",
               "api_base": "https://provider.example/v1"},)
    async with httpx.AsyncClient(trust_env=False) as client:
        with pytest.raises(ValueError, match="STAGING_UNAVAILABLE"):
            driver._scope(client, routes)
        system.catalog.base_url = "http://127.0.0.1:9"
        assert driver._scope(client, routes) == "offline"
    assert system.provider.requests == []


async def registered_proxy(system, driver):
    """Use a real dedicated audit Run and its existing HMAC, never a bypass."""
    from app.services.model_qualification_driver import _Evaluation

    routes = await driver._discovery(system.client, system.profile.alias)
    project = system.conversations.create_project(driver.user_id, "Synthetic qualification", "Fixed local protocol fixture")
    session = system.conversations.create_session(driver.user_id, project.id, "Synthetic qualification")
    state = _Evaluation(system.profile, "operator", system.catalog.deployment_fingerprint(system.profile.alias),
        driver._candidate(system.profile, "operator"), routes, system.client, session.id, "offline")
    run_id = driver._new_run(state, "workspace_artifact")
    driver._active_pi[run_id] = (state, routes[0])
    body = {"model": system.profile.alias, "messages": [{"role": "user", "content": "qualification"}],
            "max_tokens": 64}
    async def request_body():
        return json.dumps(body).encode()
    request = SimpleNamespace(body=request_body)
    authorization = f"Bearer {run_id}.{system.agent.tool_token(run_id)}"
    return state, request, authorization, body


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", ["attempts", "tokens"])
async def test_authenticated_concurrent_proxy_admission_cannot_exceed_evaluation_bounds(qualification_system, bound, monkeypatch):
    system = qualification_system
    driver = system.driver(max_attempts=1 if bound == "attempts" else 64)
    state, request, authorization, body = await registered_proxy(system, driver)
    if bound == "tokens":
        from app.services.model_qualification_driver import _WORKSPACE_TOOLS
        payload = {**body, "model": "deployment-a", "user": driver.user_id, "tools": _WORKSPACE_TOOLS}
        driver.max_tokens = len(json.dumps(payload, ensure_ascii=False).encode()) * 2 + 64
    # Pause real Run creation after the budget check. Both authenticated calls
    # can reach that check before the first ledger side effect completes.
    entered, release = threading.Event(), threading.Event()
    ledger_lock = threading.RLock()
    for name in ("reserve_resume_tokens", "reserve_model_call", "record_model_attempt",
                 "release_model_call", "settle_current_tokens", "set_run_status",
                 "owner_for_run", "run_status_for", "run_context"):
        operation = getattr(system.conversations, name)
        def serialized(*args, _operation=operation, **kwargs):
            with ledger_lock:
                return _operation(*args, **kwargs)
        monkeypatch.setattr(system.conversations, name, serialized)
    original_candidate = driver._candidate
    def serialized_candidate(*args):
        with ledger_lock:
            return original_candidate(*args)
    monkeypatch.setattr(driver, "_candidate", serialized_candidate)
    original = driver._new_run
    def paused_run(*args):
        entered.set()
        assert release.wait(5)
        # SQLite shares one connection; keep real transactions independent as
        # PostgreSQL does without substituting the accounting implementation.
        with ledger_lock:
            return original(*args)
    monkeypatch.setattr(driver, "_new_run", paused_run)
    tasks = [asyncio.create_task(driver.proxy(request, authorization=authorization)) for _ in range(2)]
    try:
        assert await asyncio.to_thread(entered.wait, 3), (state.reserved_tokens, driver.max_tokens,
            [task.result().status_code for task in tasks if task.done()])
        await asyncio.sleep(0.05)
        release.set()
        results = await asyncio.gather(*tasks)
        assert sorted(response.status_code for response in results) == [200, 403]
        assert len(system.provider.requests) == 1
        assert state.reserved_tokens <= driver.max_tokens
        assert len(state.attempts) == 1
        guards = system.conversations.db.execute("SELECT status FROM agent_model_call_guards").fetchall()
        assert guards == [("reported",)]
        assert system.conversations.db.execute("SELECT COUNT(*),SUM(amount) FROM agent_token_entries "
            "WHERE kind='model_attempt'").fetchone() == (1, 4)
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["run", "resume", "guard"])
async def test_cancelled_ledger_admission_waits_for_side_effect_and_never_restores_slot(qualification_system, phase, monkeypatch):
    system = qualification_system
    driver = system.driver(max_attempts=1)
    _state, request, authorization, _body = await registered_proxy(system, driver)
    target, name = ((driver, "_new_run") if phase == "run" else
                    (system.conversations, "reserve_resume_tokens" if phase == "resume" else "reserve_model_call"))
    original = getattr(target, name)
    entered, release = threading.Event(), threading.Event()
    def delayed_result(*args):
        result = original(*args)
        entered.set()
        assert release.wait(2)
        return result
    monkeypatch.setattr(target, name, delayed_result)
    task = asyncio.create_task(driver.proxy(request, authorization=authorization))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done(), "Cancellation must wait for the actual ledger transaction before finalization"
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done(), "Repeated cancellation must not detach a committed ledger side effect"
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        response = await driver.proxy(request, authorization=authorization)
        assert response.status_code == 403
        assert system.provider.requests == []
        guards = system.conversations.db.execute("SELECT status FROM agent_model_call_guards").fetchall()
        assert guards == ([("rejected",)] if phase == "guard" else [])
        used = system.conversations.db.execute("SELECT COALESCE(SUM(used),0) FROM agent_token_usage").fetchone()[0]
        assert used == 0
        statuses = system.conversations.db.execute("SELECT status FROM agent_runs WHERE id<>?",
            (authorization.removeprefix("Bearer ").partition(".")[0],)).fetchall()
        assert statuses == [("failed",)]
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_workspace_run_cancellation_finishes_committed_run_before_propagating(qualification_system, monkeypatch):
    system = qualification_system
    driver = system.driver(offline_workspace=True)
    state, _request, authorization, _body = await registered_proxy(system, driver)
    original = driver._new_run
    entered, release = threading.Event(), threading.Event()
    def delayed_run(*args):
        result = original(*args)
        entered.set()
        assert release.wait(2)
        return result
    monkeypatch.setattr(driver, "_new_run", delayed_run)
    task = asyncio.create_task(driver._workspace_one(state, state.routes[0]))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        statuses = system.conversations.db.execute("SELECT status FROM agent_runs WHERE id<>?",
            (authorization.removeprefix("Bearer ").partition(".")[0],)).fetchall()
        assert statuses == [("failed",)]
        assert system.provider.requests == []
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_permission_revocation_after_ledger_admission_prevents_provider_dispatch(qualification_system, monkeypatch):
    system = qualification_system
    driver = system.driver(max_attempts=1)
    _state, request, authorization, _body = await registered_proxy(system, driver)
    original = system.conversations.reserve_model_call
    def revoke_after_guard(*args):
        result = original(*args)
        system.admin.revoke("operator", "platform_admin", actor="test-bootstrap", reason="Synthetic revocation")
        return result
    monkeypatch.setattr(system.conversations, "reserve_model_call", revoke_after_guard)
    response = await driver.proxy(request, authorization=authorization)
    assert response.status_code == 403 and system.provider.requests == []
    assert system.conversations.db.execute("SELECT status FROM agent_model_call_guards").fetchall() == [("rejected",)]
    assert system.conversations.db.execute("SELECT SUM(used) FROM agent_token_usage").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_proxy_source_closed_during_current_check_cannot_start_provider_call(qualification_system, monkeypatch):
    system = qualification_system
    driver = system.driver()
    _state, request, authorization, _body = await registered_proxy(system, driver)
    source = authorization.removeprefix("Bearer ").partition(".")[0]
    original = driver._current
    entered, release = asyncio.Event(), asyncio.Event()
    async def paused_current(state):
        entered.set()
        await release.wait()
        await original(state)
    monkeypatch.setattr(driver, "_current", paused_current)
    task = asyncio.create_task(driver.proxy(request, authorization=authorization))
    await asyncio.wait_for(entered.wait(), 2)
    driver._active_pi.pop(source)
    system.conversations.set_run_status(source, "failed")
    release.set()
    response = await task
    assert response.status_code == 403
    assert system.provider.requests == []
    assert system.conversations.db.execute("SELECT COUNT(*) FROM agent_model_call_guards").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_source_closed_after_guard_reservation_refunds_without_dispatch(qualification_system, monkeypatch):
    system = qualification_system
    driver = system.driver()
    _state, request, authorization, _body = await registered_proxy(system, driver)
    source = authorization.removeprefix("Bearer ").partition(".")[0]
    original = system.conversations.reserve_model_call
    def close_after_guard(*args):
        result = original(*args)
        driver._active_pi.pop(source)
        system.conversations.set_run_status(source, "failed")
        return result
    monkeypatch.setattr(system.conversations, "reserve_model_call", close_after_guard)
    response = await driver.proxy(request, authorization=authorization)
    assert response.status_code == 403 and system.provider.requests == []
    assert system.conversations.db.execute("SELECT status FROM agent_model_call_guards").fetchall() == [("rejected",)]
    assert system.conversations.db.execute("SELECT SUM(used) FROM agent_token_usage").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_workspace_proxy_drain_waits_for_unreported_settlement(qualification_system):
    system = qualification_system
    driver = system.driver()
    _state, request, authorization, _body = await registered_proxy(system, driver)
    source = authorization.removeprefix("Bearer ").partition(".")[0]
    entered, release = asyncio.Event(), asyncio.Event()
    system.provider.block = entered, release
    task = asyncio.create_task(driver.proxy(request, authorization=authorization))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await driver._drain_proxy(source)
        with pytest.raises(asyncio.CancelledError):
            await task
        assert source not in driver._active_pi and source not in driver._proxy_tasks
        assert system.conversations.db.execute("SELECT status FROM agent_model_call_guards").fetchall() == [("unreported",)]
        assert system.conversations.db.execute("SELECT SUM(used) FROM agent_token_usage").fetchone()[0] > 0
        assert len(system.provider.requests) == 1
        assert await driver.proxy(request, authorization=authorization) is None
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
