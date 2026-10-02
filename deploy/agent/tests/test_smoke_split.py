"""The split-deployment smoke checks real API paths without leaking secrets."""

import json
import importlib.util
import time
from pathlib import Path

import httpx
import pytest

SCRIPT = Path(__file__).with_name("smoke_split.py")
spec = importlib.util.spec_from_file_location("smoke_split", SCRIPT)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
SmokeFailure = module.SmokeFailure
load_credentials = module.load_credentials
run_smoke = module.run_smoke


class DelayedEvents(httpx.SyncByteStream):
    def __iter__(self):
        yield b'id: 1\nevent: run.started\ndata: {"type":"run.started"}\n\n'
        time.sleep(0.2)
        yield b'id: 2\nevent: run.completed\ndata: {"type":"run.completed"}\n\n'


@pytest.mark.parametrize("verify_public", [True, False])
def test_smoke_checks_migrated_data_upload_quota_and_stream(tmp_path, capsys, verify_public):
    credentials = tmp_path / "test-account.json"
    credentials.write_text(json.dumps({"email": "test@example.test", "password": "secret-password"}))
    credentials.chmod(0o600)
    email, password = load_credentials(credentials)
    seen = []

    def respond(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append((request.method, path))
        if request.url.host == "old.example.test":
            return httpx.Response(200, text="legacy")
        if path == "/login":
            return httpx.Response(200, text="React")
        if path in ("/internal/compute/af3/jobs/claim", "/auth/v1/admin/users"):
            return httpx.Response(404)
        if path == "/api/v1/usage" and "authorization" not in request.headers:
            return httpx.Response(401)
        if path == "/api/v1/auth/login":
            assert request.content == b'{"email":"test@example.test","password":"secret-password"}'
            return httpx.Response(200, json={"access_token": "private-token"})
        assert request.headers.get("authorization") == "Bearer private-token"
        if path == "/api/v1/me":
            return httpx.Response(200, json={"id": "user-1"})
        if path == "/api/v1/g":
            return httpx.Response(200, json=[{"id": "project-abc", "name": "Old project"}])
        if path == "/api/v1/g/g-p-abc/c" and request.method == "GET":
            return httpx.Response(200, json=[{"id": "session-old", "title": "Old chat"}])
        if path == "/api/v1/g/g-p-abc/c/session-old/messages":
            return httpx.Response(200, json=[{"id": "msg-1", "role": "assistant"}])
        if path == "/api/v1/usage":
            return httpx.Response(200, json={"tokens": {"limit": 100},
                                             "gpu": {"limit": 0}})
        if path == "/api/v1/files/content":
            return httpx.Response(200, json={"id": "file-smoke"})
        if path == "/api/v1/g/g-p-abc/c" and request.method == "POST":
            return httpx.Response(201, json={"id": "session-smoke"})
        if path == "/api/v1/g/g-p-abc/c/session-smoke/messages" and request.method == "POST":
            return httpx.Response(200, json={"run_id": "run-smoke"})
        if path == "/api/v1/runs/run-smoke/events":
            return httpx.Response(200, stream=DelayedEvents(),
                                  headers={"content-type": "text/event-stream"})
        if path == "/api/v1/runs/run-smoke":
            return httpx.Response(200, json={"status": "completed"})
        if path == "/api/v1/g/g-p-abc/c/session-smoke/messages" and request.method == "GET":
            return httpx.Response(200, json=[{"id": "msg-smoke", "role": "assistant"}])
        raise AssertionError(f"Unexpected request: {request.method} {path}")

    transport = httpx.MockTransport(respond)
    with httpx.Client(base_url="https://agent.example.test", transport=transport) as app, \
            httpx.Client(base_url="https://old.example.test", transport=transport) as old:
        result = run_smoke(app, old, email, password, "project-abc", "session-old",
                           verify_public=verify_public)

    assert result["events"] == 2
    assert result["stream_seconds"] - result["first_event_seconds"] >= 0.15
    assert ("PUT", "/api/v1/files/content") in seen
    assert ("GET", "/api/v1/g/g-p-abc/c/session-old/messages") in seen
    assert (("GET", "/login") in seen) is verify_public
    assert "secret-password" not in capsys.readouterr().out


def test_smoke_refuses_world_readable_credentials(tmp_path):
    credentials = tmp_path / "test-account.json"
    credentials.write_text('{"email":"test@example.test","password":"secret"}')
    credentials.chmod(0o644)
    with pytest.raises(SmokeFailure, match="0600"):
        load_credentials(credentials)
