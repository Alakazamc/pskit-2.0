"""Seeding repeats safely and cannot target a production address."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from deploy.agent.scripts.seed_staging import seed_staging


def _private(path: Path, content: str) -> Path:
    path.write_text(content)
    path.chmod(0o600)
    return path


def test_seed_twice_creates_only_one_synthetic_sample(tmp_path):
    config = tmp_path / "private"
    config.mkdir(mode=0o700)
    supabase = _private(config / "supabase.env", "SUPABASE_SECRET_KEY=sb_secret_stage-only\n")
    _private(config / "backend.env.base", "RESEARCH_AGENT_ADMIN_API_KEY=stage-admin-only\n")
    credentials = _private(config / "seed.env", "STAGING_USER_EMAIL=staging-user@example.invalid\n"
                           "STAGING_USER_PASSWORD=stage-test-password\n")
    state = {"users": [], "projects": [], "sessions": [], "files": [], "limits": []}

    def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/auth/v1/admin/users":
            assert request.headers["apikey"] == "sb_secret_stage-only"
            if request.method == "GET":
                return httpx.Response(200, json={"users": state["users"]})
            state["users"].append({"id": "user-stage", "email": "staging-user@example.invalid"})
            return httpx.Response(200, json=state["users"][0])
        if path == "/api/v1/auth/login":
            return httpx.Response(200, json={"access_token": "stage-access", "user": {"id": "user-stage"}})
        if path == "/api/v1/g":
            if request.method == "GET":
                return httpx.Response(200, json=state["projects"])
            state["projects"].append({"id": "project-stage", "name": "Staging research"})
            return httpx.Response(201, json=state["projects"][0])
        if path == "/api/v1/g/g-p-stage/c":
            if request.method == "GET":
                return httpx.Response(200, json=state["sessions"])
            state["sessions"].append({"id": "session-stage", "title": "Staging sample"})
            return httpx.Response(201, json=state["sessions"][0])
        if path == "/api/v1/files":
            if request.method == "GET":
                return httpx.Response(200, json=state["files"])
            state["files"].append({"id": "file-stage", "name": "staging-notes.txt"})
            return httpx.Response(200, json=state["files"][0])
        if path == "/api/v1/admin/users/user-stage/limits":
            assert request.headers["x-admin-key"] == "stage-admin-only"
            state["limits"].append(1)
            return httpx.Response(200, json={"tokens": {}, "gpu": {}})
        raise AssertionError(f"Unexpected request path: {path}")

    with httpx.Client(transport=httpx.MockTransport(respond), trust_env=False) as client:
        first = seed_staging("http://127.0.0.1:18131", "http://127.0.0.1:18090",
                             supabase, credentials, client=client)
        second = seed_staging("http://127.0.0.1:18131", "http://127.0.0.1:18090",
                              supabase, credentials, client=client)
    assert first == second == {"user_id": "user-stage", "project_id": "project-stage",
                               "session_id": "session-stage", "file_id": "file-stage"}
    assert [len(state[name]) for name in ("users", "projects", "sessions", "files")] == [1, 1, 1, 1]
    assert len(state["limits"]) == 2


@pytest.mark.parametrize("auth_url,api_url", [
    ("https://agent.bioailab.net", "http://127.0.0.1:18090"),
    ("http://127.0.0.1:18130", "http://127.0.0.1:18090"),
    ("http://127.0.0.1:18131", "http://10.9.8.1:4000"),
    ("http://127.0.0.1:18131", "http://10.9.8.1:4001"),
])
def test_seed_rejects_any_non_staging_target_before_http(tmp_path, auth_url, api_url):
    supabase = _private(tmp_path / "supabase.env", "SUPABASE_SECRET_KEY=sb_secret_stage\n")
    credentials = _private(tmp_path / "seed.env", "STAGING_USER_EMAIL=staging-user@example.invalid\n"
                           "STAGING_USER_PASSWORD=stage-test-password\n")
    with httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("sent HTTP"))) as client:
        with pytest.raises(ValueError):
            seed_staging(auth_url, api_url, supabase, credentials, client=client)
