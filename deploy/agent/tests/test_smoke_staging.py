"""The staging smoke must reject unsafe routes and simulated compute failures."""

from __future__ import annotations

import httpx
import pytest

from deploy.agent.tests import smoke_staging
from deploy.agent.tests.smoke_staging import SmokeFailure, run_smoke


def test_smoke_requires_unchanged_production_snapshot():
    counts = iter([{"auth.users": 2, "pskit.projects": 4, "litellm.spend": 1},
                   {"auth.users": 3, "pskit.projects": 4, "litellm.spend": 1}])
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(401 if request.url.path == "/api/v1/usage" else 404),
    ), base_url="http://10.9.8.1:18132") as client:
        with pytest.raises(SmokeFailure, match="production"):
            run_smoke(client, "staging-user@example.invalid", "secret",
                      production_snapshot=lambda: next(counts),
                      probe=lambda app, email, password, admin: {"simulation": True, "events": 2})


def test_smoke_rejects_real_af3_and_never_calls_a6000():
    calls = []
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: calls.append(str(request.url)) or httpx.Response(404),
    ), base_url="http://10.9.8.1:18132") as client:
        with pytest.raises(SmokeFailure, match="simulation"):
            run_smoke(client, "staging-user@example.invalid", "secret",
                      production_snapshot=lambda: {"auth.users": 2},
                      probe=lambda app, email, password, admin: {"simulation": False, "events": 2})
    assert all("10.9.8.2" not in url for url in calls)


def test_production_snapshot_records_unmigrated_pskit_schema_as_empty(monkeypatch):
    monkeypatch.setattr(smoke_staging, "_docker", lambda command: "pskit-agent-supabase")

    def psql(database, query):
        if "auth.users" in query:
            return "2"
        if database == "postgres" and "pg_catalog.pg_tables" in query:
            return ""
        if database == "litellm" and "pg_catalog.pg_tables" in query:
            return "LiteLLM_SpendLogs"
        if "md5(" in query:
            return "candidate-digest"
        if "LiteLLM_SpendLogs" in query:
            return "0"
        raise AssertionError(query)

    monkeypatch.setattr(smoke_staging, "_production_psql", psql)
    monkeypatch.setattr(smoke_staging, "_legacy_litellm_psql", lambda query: (
        "LiteLLM_SpendLogs" if "pg_catalog.pg_tables" in query else
        "ledger-digest" if "md5(" in query else "3"))
    assert smoke_staging.snapshot_production() == {
        "auth.users": 2, "pskit.__tables__": 0, "litellm.LiteLLM_SpendLogs": 0,
        "litellm.LiteLLM_SpendLogs.digest": "candidate-digest",
        "active_litellm.LiteLLM_SpendLogs": 3,
        "active_litellm.LiteLLM_SpendLogs.digest": "ledger-digest",
    }


def test_smoke_requires_seeded_quota_and_metering():
    usage = {"tokens": {"limit": 20_000, "used": 7},
             "gpu": {"limit": 5, "used": 0, "reserved": 0}}
    entries = [{"resource": "tokens", "kind": "model_attempt", "amount": 7},
               {"resource": "gpu_minutes", "kind": "job", "amount": 0}]
    smoke_staging._verify_usage(usage, entries)
    with pytest.raises(SmokeFailure):
        smoke_staging._verify_usage({**usage, "tokens": {"limit": 1, "used": 0}}, entries)
    with pytest.raises(SmokeFailure):
        smoke_staging._verify_usage(usage, [])


def test_token_quota_probe_restores_limit_after_rejection():
    calls = []

    def respond(request):
        calls.append((request.method, request.url.path))
        if request.method == "PUT":
            return httpx.Response(200, json={"tokens": {"limit": 0}})
        return httpx.Response(409, json={"detail": {"code": "TOKEN_QUOTA_EXCEEDED"}})

    with httpx.Client(transport=httpx.MockTransport(respond),
                      base_url="http://127.0.0.1:18090") as client:
        smoke_staging._verify_token_quota(client, {"Authorization": "Bearer stage"},
                                          "user-stage", "/api/v1/g/g-p-stage/c/session-stage",
                                          "stage-admin")
    assert [method for method, _path in calls] == ["PUT", "POST", "PUT"]


def test_token_quota_probe_restores_after_ambiguous_first_response():
    calls = []

    def respond(request):
        calls.append(request.method)
        return httpx.Response(503 if len(calls) == 1 else 200, json={})

    with httpx.Client(transport=httpx.MockTransport(respond),
                      base_url="http://127.0.0.1:18090") as client:
        with pytest.raises(SmokeFailure):
            smoke_staging._verify_token_quota(client, {"Authorization": "Bearer stage"},
                                              "user-stage", "/api/v1/g/g-p-stage/c/session-stage",
                                              "stage-admin")
    assert calls == ["PUT", "PUT"]
