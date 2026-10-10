"""Fixed protocol cases and exact evidence never self-certify client capabilities."""

import asyncio

import httpx
import pytest
from pydantic import ValidationError

from app.contracts.admin import ModelDraft, RevisionRequest
from app.contracts.model_runtime import ModelRuntimeProfile, ModelWirePolicy, fingerprint
from app.domain.admin.model_policy import ModelPolicy
from app.domain.admin.model_qualification import ModelQualification
from app.domain.admin.roles import AdminStore, RevisionConflict
from app.services.model_catalog import ModelCatalog, ModelOption


def profile(**changes):
    return ModelRuntimeProfile(alias="lab/alias", revision=1, supports_tools=True,
                               supports_tool_streaming=True, **changes)


def response():
    return {"choices": [{"message": {"content": "qualification"}}]}


async def good_probe(case_id, body, *, actor_id, deployment_fingerprint):
    assert actor_id == "operator"
    assert body["model"] == "lab/alias"
    if case_id.startswith("errors:"):
        status = case_id.split(":", 1)[1]
        return {"status": status if status == "timeout" else int(status)}
    if case_id == "errors":
        return {"normalized": True}
    if case_id == "alias_routing":
        return {"all_deployments_verified": True,
                "deployment_fingerprint": deployment_fingerprint}
    if case_id == "workspace_artifact":
        return {name: True for name in ("write_verified", "read_verified", "artifact_verified",
                                       "owner_verified", "usage_settled_once")}
    if case_id == "tools":
        return {"choices": [{"message": {"tool_calls": [{
            "id": "qualified-call", "type": "function", "function": {
                "name": "qualification_echo", "arguments": '{"value":"qualification"}',
            },
        }]}}]}
    if case_id == "tool_continue":
        assert body["messages"][-1] == {
            "role": "tool", "tool_call_id": "qualified-call", "content": "qualification",
        }
        assert body["messages"][-2]["tool_calls"][0]["id"] == "qualified-call"
    if case_id == "stream":
        assert body["stream"] is True
        return {"chunks": [
            {"choices": [{"delta": {"content": "quali"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "fication"}, "finish_reason": "stop"}]},
        ]}
    if case_id == "streaming_tools":
        assert body["stream"] is True
        return {"chunks": [
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "qualified-",
                "function": {"name": "qualification_", "arguments": '{"value":'}}]},
                "finish_reason": None}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call",
                "function": {"name": "echo", "arguments": '"qualification"}'}}]},
                "finish_reason": "tool_calls"}]},
        ]}
    if case_id == "images":
        assert body["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/png")
    if case_id.startswith("reasoning:"):
        assert body["reasoning_effort"] == case_id.split(":", 1)[1]
    result = response()
    if case_id == "usage":
        result["usage"] = {"prompt_tokens": 3, "completion_tokens": 1}
    return result


def qualifier(probe=good_probe, *, scope="offline", **changes):
    return ModelQualification(
        probe, pi_version="0.87.1", gateway_version="1.100.3",
        provider_url_fingerprint=fingerprint("fixture-only"), verification_scope=scope,
        budget_check=(lambda actor, alias, body: None) if scope != "offline" else None,
        **changes,
    )


def with_evidence(candidate, evidence):
    return candidate.model_copy(update={"qualification_evidence": evidence,
        "qualification_evidence_id": evidence.evidence_id,
        "qualification_status": evidence.status})


@pytest.mark.asyncio
async def test_offline_matrix_covers_images_reasoning_stream_tools_and_unknown_usage():
    candidate = profile(supports_images=True, reasoning_levels=["low", "high"],
                        wire_policy=ModelWirePolicy(reasoning_mode="reasoning_effort"))
    evidence = await qualifier().evaluate(candidate, actor_id="operator",
                                           deployment_fingerprint=fingerprint("route"))
    assert evidence.status == "degraded"
    checks = {check.case_id: check for check in evidence.cases}
    for case in ("text", "stream", "tools", "tool_continue", "streaming_tools", "images",
                 "reasoning:low", "reasoning:high", "errors:429", "alias_routing"):
        assert checks[case].status == "passed"
    assert checks["usage_missing"].status == "unknown"
    assert checks["usage_missing"].usage_known is False
    assert checks["workspace_artifact"].status == "not_tested"
    assert not with_evidence(candidate, evidence).agent_ready
    assert "qualification_echo" not in evidence.model_dump_json()
    assert "data:image" not in evidence.model_dump_json()


@pytest.mark.asyncio
async def test_trusted_staging_fixture_binds_exact_revision_and_cases():
    candidate = profile()
    evidence = await qualifier(scope="staging").evaluate(
        candidate, actor_id="operator", deployment_fingerprint=fingerprint("route"),
    )
    qualified = with_evidence(candidate, evidence)
    assert qualified.agent_ready
    for change in ({"revision": 2}, {"alias": "other"}, {"supports_images": True},
                   {"qualification_evidence_id": "caller-value"}, {"protocol": "responses"}):
        assert not qualified.model_copy(update=change).agent_ready
    bad = evidence.model_copy(update={"cases": tuple(c for c in evidence.cases
                                                     if c.case_id != "workspace_artifact")})
    assert not with_evidence(candidate, bad).agent_ready


@pytest.mark.asyncio
@pytest.mark.parametrize("case_id,bad", [
    ("tools", {"choices": [{"message": {"content": "<write_file> fake </write_file>"}}]}),
    ("tools", {"choices": [{"message": {"tool_calls": ["malformed"]}}]}),
    ("stream", {"chunks": [{"choices": [{"delta": {"content": "not finished"}}]}]}),
    ("stream", {"chunks": [
        {"choices": [{"delta": {"content": "ended"}, "finish_reason": "stop"}]},
        {"choices": [{"delta": {"content": "late"}, "finish_reason": None}]},
    ]}),
    ("streaming_tools", {"chunks": [{"choices": [{"delta": {"content": "<write_file>"},
                                                   "finish_reason": "stop"}]}]}),
    ("errors:429", {"status": 200}),
    ("alias_routing", {"all_deployments_verified": True,
                       "deployment_fingerprint": fingerprint("other")}),
])
async def test_protocol_failure_never_passes_agent_ready(case_id, bad):
    async def probe(name, body, **kwargs):
        return bad if name == case_id else await good_probe(name, body, **kwargs)
    candidate = profile()
    evidence = await qualifier(probe, scope="staging").evaluate(
        candidate, actor_id="operator", deployment_fingerprint=fingerprint("route"),
    )
    assert evidence.status == "failed"
    assert not with_evidence(candidate, evidence).agent_ready


@pytest.mark.asyncio
async def test_errors_and_response_limits_are_secret_free():
    async def probe(case_id, body, **kwargs):
        raise httpx.ConnectError("https://secret:provider-key@upstream/private")
    evidence = await qualifier(probe).evaluate(profile(), actor_id="operator",
                                               deployment_fingerprint=fingerprint("route"))
    assert evidence.status == "failed"
    assert "provider-key" not in evidence.model_dump_json()

    async def huge(case_id, body, **kwargs):
        return {"private": "A" * 1000}
    bounded = await qualifier(huge, max_response_bytes=100).evaluate(
        profile(), actor_id="operator", deployment_fingerprint=fingerprint("route"),
    )
    assert all(case.response_fingerprint is None for case in bounded.cases)


@pytest.mark.asyncio
async def test_timeout_and_responses_fail_explicitly():
    async def blocked(*args, **kwargs):
        await asyncio.Event().wait()
    with pytest.raises(TimeoutError):
        await qualifier(blocked, timeout_seconds=0.01).evaluate(
            profile(), actor_id="operator", deployment_fingerprint=fingerprint("route"),
        )
    with pytest.raises(ValueError, match="MODEL_PROTOCOL_UNAVAILABLE"):
        await qualifier(blocked).evaluate(profile(protocol="responses"), actor_id="operator",
                                           deployment_fingerprint=fingerprint("route"))


@pytest.mark.asyncio
async def test_budget_rechecked_before_each_probe_and_denial_never_calls_provider():
    checked = []
    probed = []
    async def budget(actor, alias, body):
        checked.append((actor, alias))
        if len(checked) == 2:
            raise PermissionError("MODEL_QUALIFICATION_BUDGET_EXHAUSTED")
    async def probe(case_id, body, **kwargs):
        probed.append(case_id)
        return response()
    checker = ModelQualification(probe, pi_version="0.87.1", gateway_version="1.100.3",
                                 provider_url_fingerprint=fingerprint("route"),
                                 verification_scope="gateway", budget_check=budget)
    with pytest.raises(PermissionError):
        await checker.evaluate(profile(), actor_id="operator",
                               deployment_fingerprint=fingerprint("route"))
    assert len(checked) == 2
    assert probed == ["text"]


def test_wire_fields_are_closed_and_live_probe_requires_budget():
    with pytest.raises(ValidationError):
        ModelWirePolicy(reasoning_mode="execute_shell")
    with pytest.raises(ValidationError):
        profile(context_window=0)
    with pytest.raises(ValueError, match="BUDGET_REQUIRED"):
        ModelQualification(good_probe, pi_version="0.87.1", gateway_version="1.100.3",
                           provider_url_fingerprint=fingerprint("route"),
                           verification_scope="staging")
    assert ModelOption(id="legacy").model_dump() == {
        "id": "legacy", "supports_images": False, "reasoning_levels": [],
    }


def policy_fixture(tmp_path, *, legacy=False):
    metadata = {"data": [{"model_name": "lab/alias", "model_info": {},
                          "litellm_params": {"model": "openai/actual", "api_base": "http://a"}}]}
    def gateway(request):
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "lab/alias"}]})
        return httpx.Response(200, json=metadata)
    catalog = ModelCatalog(base_url="http://fixture", api_key="fixture-only", default_model="lab/alias",
                           transport=httpx.MockTransport(gateway))
    store = AdminStore(str(tmp_path / "policy.db"))
    if legacy:
        published = ModelDraft(expected_revision=0, reason="Pre-upgrade persisted route",
                               allowed_user_ids=["user"]).model_dump_json()
        store.db.execute("INSERT INTO admin_models VALUES (?,?,'published',?,?)",
                         ("lab/alias", 0, published, published))
        store.db.commit()
    return ModelPolicy(store, catalog, managed=True), metadata


def draft(**changes):
    return ModelDraft(expected_revision=0, reason="Fixture publication", allowed_user_ids=["user"],
                      runtime_profile=profile(), **changes)


def revision(number):
    return RevisionRequest(expected_revision=number, reason="Fixture qualification review")


@pytest.mark.asyncio
async def test_admin_cannot_self_certify_or_publish_untested_profile(tmp_path):
    policy, _ = policy_fixture(tmp_path)
    candidate = profile()
    evidence = await qualifier(scope="staging").evaluate(candidate, actor_id="operator",
                                                         deployment_fingerprint=fingerprint("route"))
    saved = await policy.save("operator", "lab/alias", draft().model_copy(update={
        "runtime_profile": with_evidence(candidate, evidence),
    }))
    assert saved.draft.runtime_profile.qualification_status == "untested"
    assert saved.draft.runtime_profile.qualification_evidence is None
    assert not await policy.visible_for("user")
    with pytest.raises(ValueError, match="MODEL_NOT_QUALIFIED"):
        await policy.publish("operator", "lab/alias", revision(1))


@pytest.mark.asyncio
async def test_qualified_profile_publishes_and_later_draft_does_not_revoke_published(tmp_path):
    policy, _ = policy_fixture(tmp_path)
    await policy.save("operator", "lab/alias", draft())
    checked = await policy.qualify("operator", "lab/alias", revision(1), qualifier(scope="staging"))
    assert checked.revision == 2
    assert checked.draft.runtime_profile.revision == 1
    await policy.publish("operator", "lab/alias", revision(2))
    selected = await policy.authorize("user", "lab/alias")
    assert selected.runtime_profile.agent_ready
    await policy.save("operator", "lab/alias", draft().model_copy(update={"expected_revision": 3}))
    assert (await policy.authorize("user", "lab/alias")).runtime_profile.revision == 1
    assert policy.admin_item("lab/alias", selected).draft.runtime_profile.revision == 4
    assert not policy.admin_item("lab/alias", selected).draft.runtime_profile.agent_ready
    await policy.publish("operator", "lab/alias", revision(4), retire=True)
    with pytest.raises(PermissionError, match="MODEL_FORBIDDEN"):
        await policy.authorize("user", "lab/alias")


@pytest.mark.asyncio
async def test_deployment_change_invalidates_qualification_not_private_key_rotation(tmp_path):
    policy, metadata = policy_fixture(tmp_path)
    await policy.save("operator", "lab/alias", draft())
    await policy.qualify("operator", "lab/alias", revision(1), qualifier(scope="staging"))
    metadata["data"][0]["litellm_params"]["api_key"] = "private-rotated-key"
    policy.catalog.invalidate()
    await policy.available_gateway("lab/alias")
    await policy.publish("operator", "lab/alias", revision(2))
    assert await policy.visible_for("user")
    metadata["data"][0]["litellm_params"]["api_base"] = "http://new-route"
    policy.catalog.invalidate()
    assert not await policy.visible_for("user")
    with pytest.raises(PermissionError):
        await policy.authorize("user", "lab/alias")


@pytest.mark.asyncio
async def test_known_false_metadata_cannot_be_masked_by_manual_profile(tmp_path):
    policy, metadata = policy_fixture(tmp_path)
    metadata["data"][0]["model_info"]["supports_vision"] = False
    with pytest.raises(ValueError, match="MODEL_CAPABILITY_UNSUPPORTED"):
        await policy.save("operator", "lab/alias", draft().model_copy(update={
            "runtime_profile": profile(supports_images=True), "supports_images": True,
        }))


@pytest.mark.asyncio
async def test_concurrent_candidate_edit_rejects_old_probe_evidence(tmp_path):
    policy, _ = policy_fixture(tmp_path)
    await policy.save("operator", "lab/alias", draft())
    edited = False
    async def probe(case_id, body, **kwargs):
        nonlocal edited
        if not edited:
            edited = True
            await policy.save("operator", "lab/alias", draft().model_copy(update={"expected_revision": 1}))
        return await good_probe(case_id, body, **kwargs)
    with pytest.raises(RevisionConflict):
        await policy.qualify("operator", "lab/alias", revision(1), qualifier(probe, scope="staging"))
    current = policy.admin_item("lab/alias", None)
    assert current.revision == 2
    assert current.draft.runtime_profile.qualification_evidence is None


@pytest.mark.asyncio
@pytest.mark.parametrize("prior_failed_candidate", [False, True])
async def test_profile_none_cannot_admit_new_or_failed_candidate(tmp_path, prior_failed_candidate):
    policy, _ = policy_fixture(tmp_path)
    expected = 0
    if prior_failed_candidate:
        await policy.save("operator", "lab/alias", draft())
        expected = 1
    await policy.save("operator", "lab/alias", draft().model_copy(update={
        "expected_revision": expected, "runtime_profile": None,
    }))
    with pytest.raises(ValueError, match="MODEL_RUNTIME_PROFILE_REQUIRED"):
        await policy.publish("operator", "lab/alias", revision(expected + 1))
    assert not await policy.visible_for("user")


@pytest.mark.asyncio
async def test_only_persisted_legacy_routes_are_grandfathered_and_retire_restart_can_republish(tmp_path):
    policy, _ = policy_fixture(tmp_path, legacy=True)
    assert [item.id for item in await policy.visible_for("user")] == ["lab/alias"]
    await policy.publish("operator", "lab/alias", revision(0), retire=True)
    assert not await policy.visible_for("user")
    restarted = ModelPolicy(policy.store, policy.catalog, managed=True)
    await restarted.publish("operator", "lab/alias", revision(1))
    assert [item.id for item in await restarted.visible_for("user")] == ["lab/alias"]


@pytest.mark.asyncio
async def test_qualified_legacy_route_cannot_downgrade_profile_before_or_after_restart(tmp_path):
    policy, _ = policy_fixture(tmp_path, legacy=True)
    stale_instance = ModelPolicy(policy.store, policy.catalog, managed=True)
    # A failed candidate leaves the still-published legacy route available.
    await policy.save("operator", "lab/alias", draft())
    assert await policy.visible_for("user")
    await policy.qualify("operator", "lab/alias", revision(1), qualifier(scope="staging"))
    await policy.publish("operator", "lab/alias", revision(2))
    await policy.save("operator", "lab/alias", draft().model_copy(update={
        "expected_revision": 3, "runtime_profile": None,
    }))
    for service in (policy, stale_instance, ModelPolicy(policy.store, policy.catalog, managed=True)):
        with pytest.raises(ValueError, match="MODEL_RUNTIME_PROFILE_REQUIRED"):
            await service.publish("operator", "lab/alias", revision(4))
        assert (await service.authorize("user", "lab/alias")).runtime_profile.agent_ready


@pytest.mark.asyncio
async def test_later_profile_none_row_does_not_gain_legacy_visibility(tmp_path):
    policy, _ = policy_fixture(tmp_path)
    await policy.save("operator", "lab/alias", draft().model_copy(update={"runtime_profile": None}))
    policy.store.db.execute("UPDATE admin_models SET state='published',published_json=draft_json")
    policy.store.db.commit()
    assert not await policy.visible_for("user")


@pytest.mark.asyncio
async def test_qualification_off_matches_pi_none_wire_but_evidence_keeps_ui_level():
    seen = []
    async def probe(case_id, body, **kwargs):
        if case_id == "reasoning:off":
            seen.append(body["reasoning_effort"])
            assert body["reasoning_effort"] == "none"
            return response()
        return await good_probe(case_id, body, **kwargs)
    candidate = profile(reasoning_levels=["off"],
                        wire_policy=ModelWirePolicy(reasoning_mode="reasoning_effort"))
    evidence = await qualifier(probe).evaluate(candidate, actor_id="operator",
                                               deployment_fingerprint=fingerprint("route"))
    assert seen == ["none"]
    assert next(item for item in evidence.cases if item.case_id == "reasoning:off").status == "passed"
