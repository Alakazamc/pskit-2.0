"""The staging smoke must reject unsafe routes and simulated compute failures."""

from __future__ import annotations

import httpx
import pytest

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
                      probe=lambda app, email, password: {"simulation": True, "events": 2})


def test_smoke_rejects_real_af3_and_never_calls_a6000():
    calls = []
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: calls.append(str(request.url)) or httpx.Response(404),
    ), base_url="http://10.9.8.1:18132") as client:
        with pytest.raises(SmokeFailure, match="simulation"):
            run_smoke(client, "staging-user@example.invalid", "secret",
                      production_snapshot=lambda: {"auth.users": 2},
                      probe=lambda app, email, password: {"simulation": False, "events": 2})
    assert all("10.9.8.2" not in url for url in calls)
