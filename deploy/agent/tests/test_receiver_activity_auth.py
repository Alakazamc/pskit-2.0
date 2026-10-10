"""Authenticated receiver evidence uses private synthetic files and loopback HTTP."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from deploy.agent.scripts import release_control as rc


@pytest.fixture
def controller_config(tmp_path):
    paths = {name: tmp_path / name for name in ("releases", "state", "static")}
    for path in paths.values():
        path.mkdir(mode=0o700)
    token = tmp_path / "activity.token"
    token.write_text("synthetic-activity-token-" + "x" * 32)
    token.chmod(0o600)
    url = "http://127.0.0.1:19001/activity"
    config = {"schema_version": rc.CONTROL_VERSION, "environment": "prod", "repository": str(rc.ROOT),
        "cloud_env_file": str(tmp_path / "cloud.env"), "backend_env_file": str(tmp_path / "backend.env"),
        "release_directory": str(paths["releases"]), "state_directory": str(paths["state"]),
        "static_directory": str(paths["static"]), "backend_url": "http://127.0.0.1:18088",
        "public_url": "https://release.invalid", "critical_smoke_command": ["synthetic-noop"],
        "receiver_activity_urls": [url], "receiver_activity_required": True,
        "receiver_activity_auth": {url: {"token_file": str(token)}}}
    path = tmp_path / "control.json"
    def save():
        path.write_text(json.dumps(config))
        path.chmod(0o600)
        return path
    return config, token, save


def evidence(**counts):
    return json.dumps({"schema_version": "pskit.receiver-activity.v1", "observed_at": time.time(),
        "counts": {"active": 0, "unknown": 0, "journal_pending": 0, "outbox_pending": 0, **counts}}).encode()


def test_receiver_activity_example_is_disabled_and_has_a_separate_empty_identity():
    source = (rc.ROOT / "deploy/agent/mcp.receiver.env.example").read_text()
    values = dict(line.split("=", 1) for line in source.splitlines() if line and not line.startswith("#"))
    assert values["PSKIT_RECEIVER_ACTIVITY_ENABLED"] == "false"
    assert values["PSKIT_RECEIVER_ACTIVITY_HOST"] == "127.0.0.1"
    assert values["PSKIT_RECEIVER_ACTIVITY_PORT"] == "18188"
    assert values["PSKIT_RECEIVER_ACTIVITY_TOKEN"] == ""


def test_authenticated_adapter_preserves_legacy_http_injection(controller_config):
    config, token, save = controller_config
    received = []
    def activity_http(url, bearer):
        received.append((url, bearer))
        return 200, evidence()
    controller = rc.Controller(save(), "prod", http=lambda _url: pytest.fail("Do not send receiver bearer to legacy probe"),
                               receiver_http=activity_http)
    controller._receiver_activity(required=True)
    assert received == [(config["receiver_activity_urls"][0], token.read_text())]
    config.pop("receiver_activity_auth")
    calls = []
    controller = rc.Controller(save(), "prod", http=lambda url: (calls.append(url) or 200, evidence()))
    controller._receiver_activity(required=True)
    assert calls == config["receiver_activity_urls"]


@pytest.mark.parametrize("fault", ["mode", "symlink", "parent-symlink", "oversize", "multiline", "missing"])
def test_token_file_is_private_bounded_and_never_followed(controller_config, tmp_path, fault):
    config, token, save = controller_config
    if fault == "mode":
        token.chmod(0o644)
    elif fault == "symlink":
        link = tmp_path / "link"
        link.symlink_to(token)
        config["receiver_activity_auth"][config["receiver_activity_urls"][0]]["token_file"] = str(link)
    elif fault == "parent-symlink":
        link = tmp_path / "linked"
        link.symlink_to(tmp_path, target_is_directory=True)
        config["receiver_activity_auth"][config["receiver_activity_urls"][0]]["token_file"] = str(link / token.name)
    elif fault == "oversize":
        token.write_text("x" * 1024)
    elif fault == "multiline":
        token.write_text("x" * 40 + "\n" + "y" * 40)
    else:
        token.unlink()
    with pytest.raises(rc.ReleaseError):
        controller = rc.Controller(save(), "prod", receiver_http=lambda *_args: pytest.fail("No HTTP with unsafe secret"))
        controller._receiver_activity(required=True)


@pytest.mark.parametrize("url", ["http://8.8.8.8/activity", "http://public.invalid/activity", "http://user:pass@10.9.8.2/activity",
                                  "http://10.9.8.2/activity?token=x", "http://0.0.0.0/activity"])
def test_authenticated_receiver_url_must_name_exact_private_literal(controller_config, url):
    config, token, save = controller_config
    config["receiver_activity_urls"] = [url]
    config["receiver_activity_auth"] = {url: {"token_file": str(token)}}
    with pytest.raises(rc.ReleaseError):
        rc.Controller(save(), "prod")


@pytest.mark.parametrize("body", [b"null", b"[]", b"{}", b" " * (64 * 1024) + b"{}"],
                         ids=["null", "list", "empty", "oversized"])
def test_invalid_or_oversized_evidence_fails_closed(controller_config, body):
    _config, _token, save = controller_config
    controller = rc.Controller(save(), "prod", receiver_http=lambda *_args: (200, body))
    with pytest.raises(rc.ReleaseError):
        controller._receiver_activity(required=True)


@pytest.mark.parametrize("fault", ["duplicate-count", "duplicate-root", "deep-json"])
def test_receiver_evidence_json_is_strict_and_bounded(controller_config, fault):
    _config, _token, save = controller_config
    body = evidence()
    if fault == "duplicate-count":
        body = body.replace(b'"active": 0', b'"active": 1, "active": 0')
    elif fault == "duplicate-root":
        body = body.replace(b'"schema_version":', b'"schema_version":"unexpected", "schema_version":')
    else:
        body = b"[" * 10000 + b"]" * 10000
    controller = rc.Controller(save(), "prod", receiver_http=lambda *_args: (200, body))
    with pytest.raises(rc.ReleaseError):
        controller._receiver_activity(required=True)


def test_authenticated_http_uses_no_proxy_and_never_redirects(monkeypatch):
    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append((self.path, self.headers.get("Authorization")))
            self.send_response(302 if self.path == "/redirect" else 200)
            if self.path == "/redirect":
                self.send_header("Location", "/leaked")
            self.end_headers()
            self.wfile.write(evidence())
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    token = "synthetic-activity-token-" + "x" * 32
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        assert rc.get_receiver_http(base + "/activity", token)[0] == 200
        assert rc.get_receiver_http(base + "/redirect", token)[0] == 302
        assert received == [("/activity", "Bearer " + token), ("/redirect", "Bearer " + token)]
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
