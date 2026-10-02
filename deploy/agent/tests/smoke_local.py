"""End-to-end local Docker smoke using Supabase, Pi, and a mock AF3 worker.

Only status and counts are printed. Test credentials and callback keys stay in
memory or ignored 0600 local files.
"""

import argparse
import json
import os
import re
import secrets
import subprocess
import time
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[3]
COMPOSE = [
    "docker", "compose", "-f", str(ROOT / "deploy/agent/compose.yaml"),
    "-f", str(ROOT / "deploy/agent/compose.local.yaml"),
]


class SmokeFailure(Exception):
    pass


def checked(response: httpx.Response, label: str, expected: int = 200) -> dict:
    if response.status_code != expected:
        try:
            detail = response.json().get("detail", {})
            code = detail.get("code", "unknown") if isinstance(detail, dict) else "unknown"
        except ValueError:
            code = "unknown"
        raise SmokeFailure(f"{label}: HTTP {response.status_code}, code={code}")
    return response.json() if response.content else {}


def wait_for(predicate, label: str, timeout: float = 90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.4)
    raise SmokeFailure(f"Timed out waiting for {label}")


def mail_otp(client: httpx.Client, email: str) -> str | None:
    messages = checked(client.get("/api/v1/messages"), "Mailpit list").get("messages", [])
    for item in messages:
        if email not in json.dumps(item.get("To", [])):
            continue
        body = checked(client.get(f"/api/v1/message/{item['ID']}"), "Mailpit message")
        text = body.get("Text") or body.get("HTML") or ""
        match = re.search(r"(?:code|otp|验证码)[^0-9]{0,100}([0-9]{6})", text, re.I)
        if match is None:
            match = re.search(r"(?<![0-9])[0-9]{6}(?![0-9])", text)
        if match:
            return match.group(1) if match.lastindex else match.group(0)
    return None


def events(client: httpx.Client, run_id: str, headers: dict[str, str]) -> list[dict]:
    response = client.get(f"/api/v1/runs/{run_id}/events", params={"follow": "false"},
                          headers=headers, timeout=15)
    if response.status_code != 200:
        raise SmokeFailure(f"Run event replay: HTTP {response.status_code}")
    return [json.loads(line[6:]) for line in response.text.splitlines()
            if line.startswith("data: ")]


def run_status(client: httpx.Client, run_id: str, headers: dict[str, str]) -> str:
    return checked(client.get(f"/api/v1/runs/{run_id}", headers=headers), "Run status")["status"]


def key_from_file(path: Path) -> str:
    if path.stat().st_mode & 0o077:
        raise SmokeFailure("Callback key file must be mode 0600")
    entries = [line.split("=", 1)[1] for line in path.read_text().splitlines()
               if line.startswith("RESEARCH_AGENT_COMPUTE_CALLBACK_KEY=")]
    if len(entries) != 1 or len(entries[0]) < 32:
        raise SmokeFailure("Callback key file is missing a valid key")
    return entries[0]


def artifact_id_for_job(job_id: str) -> str:
    return f"local-result-{job_id}"


def test_credentials(path: Path) -> tuple[str, str, bool]:
    """Reuse one ignored 0600 account while iterating on a local smoke run."""
    if os.getenv("AGENT_SMOKE_EMAIL") and os.getenv("AGENT_SMOKE_PASSWORD"):
        return os.environ["AGENT_SMOKE_EMAIL"], os.environ["AGENT_SMOKE_PASSWORD"], False
    if path.exists():
        if path.stat().st_mode & 0o077:
            raise SmokeFailure("Test credential file must be mode 0600")
        saved = json.loads(path.read_text())
        return saved["email"], saved["password"], False
    email = f"local-agent-{secrets.token_hex(6)}@example.test"
    password = secrets.token_urlsafe(24)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump({"email": email, "password": password}, output)
    return email, password, True


def run_smoke(base_url: str, mailpit_url: str, proxy_url: str,
              proxy_env_file: Path, credentials_file: Path, restart: bool) -> None:
    with httpx.Client(base_url=base_url, timeout=20, trust_env=False) as app, httpx.Client(
        base_url=mailpit_url, timeout=10, trust_env=False,
    ) as mailpit, httpx.Client(base_url=proxy_url, timeout=20, trust_env=False) as proxy:
        # The public web route must reject internal callbacks before any identity exists.
        assert app.get("/internal/compute/af3/jobs/claim").status_code == 404
        assert app.get("/api/v1/usage").status_code == 401

        email, password, fresh = test_credentials(credentials_file)
        login_response = app.post("/api/v1/auth/login", json={
            "email": email, "password": password,
        }) if not fresh else None
        if login_response is None or login_response.status_code == 401:
            signup = checked(app.post("/api/v1/auth/signup", json={
                "email": email, "password": password,
            }), "Signup")
            if signup["status"] == "check_email":
                otp = wait_for(lambda: mail_otp(mailpit, email), "signup OTP", timeout=40)
                checked(app.post("/api/v1/auth/verify", json={
                    "email": email, "token": otp, "type": "signup",
                }), "OTP verification")
            login_response = app.post("/api/v1/auth/login", json={
                "email": email, "password": password,
            })
        login = checked(login_response, "Python login")
        headers = {"Authorization": f"Bearer {login['access_token']}"}
        checked(app.get("/api/v1/me", headers=headers), "JWT identity")

        project = checked(app.post("/api/v1/g", headers=headers, json={
            "name": "Local Docker smoke", "description": "Isolated test project",
        }), "Create project", expected=201)
        project_id = project["id"]
        project_key = "g-p-" + project_id.removeprefix("project-")
        session = checked(app.post(f"/api/v1/g/{project_key}/c", headers=headers,
                                   json={"title": "Local Pi smoke"}),
                          "Create session", expected=201)
        session_id = session["id"]

        normal = checked(app.post(f"/api/v1/g/{project_key}/c/{session_id}/messages",
                                  headers=headers, json={"content": "Say hello."}),
                         "Normal Pi message")
        normal_run = normal["run_id"]
        wait_for(lambda: run_status(app, normal_run, headers) == "completed",
                 "normal Pi completion")
        normal_events = events(app, normal_run, headers)
        assert any(event["type"] == "run.completed" for event in normal_events)
        messages = checked(app.get(f"/api/v1/g/{project_key}/c/{session_id}/messages",
                                   headers=headers), "Session messages")
        assert any(message["role"] == "assistant" for message in messages)

        file = checked(app.put("/api/v1/files/content", params={"name": "smoke.txt"},
                               headers={**headers, "Content-Type": "text/plain"},
                               content=b"local upload through nginx"), "File upload")
        assert file["id"]

        pre_af3_usage = checked(app.get("/api/v1/usage", headers=headers), "Usage before AF3")
        af3 = checked(app.post(f"/api/v1/g/{project_key}/c/{session_id}/messages",
                               headers=headers, json={"content": "Run AF3 on a small protein."}),
                      "AF3 Pi message")
        af3_run = af3["run_id"]
        wait_for(lambda: run_status(app, af3_run, headers) == "waiting",
                 "AF3 suspension")
        queued = wait_for(
            lambda: next((event["data"]["job_id"] for event in events(app, af3_run, headers)
                          if event["type"] == "task.updated"
                          and event["data"]["status"] == "queued"), None),
            "queued AF3 job",
        )
        before_restart = checked(app.get("/api/v1/usage", headers=headers), "Usage before restart")
        if restart:
            subprocess.run([*COMPOSE, "restart", "backend", "af3-callback-proxy"],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            wait_for(lambda: app.get("/api/v1/usage", headers=headers).status_code == 200,
                     "backend recovery")
            assert run_status(app, af3_run, headers) == "waiting"
            assert any(p["id"] == project_id for p in checked(
                app.get("/api/v1/g", headers=headers), "Persisted projects"))

        worker_key = key_from_file(proxy_env_file)
        worker_headers = {"X-Compute-Key": worker_key}
        claims = checked(proxy.post("/internal/compute/af3/jobs/claim", headers=worker_headers,
                                    json={"worker_id": "af3-local-test", "max_jobs": 8,
                                          "resources": {
                                              "capabilities": ["af3"], "gpu_count": 8,
                                              "gpu_memory_mb": 49152,
                                          }}), "Mock worker claim")
        claim = next((item for item in claims if item["id"] == queued), None)
        if claim is None:
            raise SmokeFailure("Mock worker did not claim the suspended AF3 job")
        lease = claim["lease_token"]
        attempt = claim["attempt"]
        artifact_id = artifact_id_for_job(queued)
        artifact = checked(proxy.put(
            f"/internal/af3/jobs/{queued}/artifacts/{artifact_id}",
            params={"name": "local-result.json", "kind": "data", "attempt": attempt},
            headers={**worker_headers, "X-Compute-Lease": lease,
                     "Content-Type": "application/json"},
            content=b'{"simulation":true,"source":"local-smoke"}',
        ), "Mock artifact upload")
        checked(proxy.post(f"/internal/af3/jobs/{queued}/result", headers=worker_headers,
                           json={"status": "completed", "actual_gpu_minutes": 0,
                                 "simulation": True, "attempt": attempt,
                                 "lease_token": lease,
                                 "artifacts": [{"id": artifact_id, "name": "local-result.json",
                                                "kind": "data"}]}), "Mock AF3 result")
        wait_for(lambda: run_status(app, af3_run, headers) == "completed",
                 "automatic Pi resume", timeout=120)
        after_events = events(app, af3_run, headers)
        assert any(event["type"] == "run.completed" for event in after_events)
        assert any(event["type"] == "artifact.created" for event in after_events)
        after_usage = checked(app.get("/api/v1/usage", headers=headers), "Final usage")
        assert before_restart["gpu"]["reserved"] > pre_af3_usage["gpu"]["reserved"]
        assert after_usage["gpu"]["reserved"] == pre_af3_usage["gpu"]["reserved"]
        assert after_usage["storage"]["used"] >= len(b"local upload through nginx")
        assert artifact["id"]
        print("Local Docker smoke passed: signup, JWT, project, session, Pi/SSE, upload, "
              "AF3 suspend/restart/mock result/resume, quota and artifact.")
        print(f"Observed: normal_events={len(normal_events)}, af3_events={len(after_events)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18085")
    parser.add_argument("--mailpit-url", default="http://127.0.0.1:18131")
    parser.add_argument("--proxy-url", default="http://127.0.0.1:18185")
    parser.add_argument("--proxy-env-file", type=Path,
                        default=ROOT / "deploy/agent/local.proxy.env")
    parser.add_argument("--credentials-file", type=Path,
                        default=ROOT / "deploy/agent/local.smoke.credentials")
    parser.add_argument("--no-restart", action="store_true")
    args = parser.parse_args()
    try:
        run_smoke(args.base_url, args.mailpit_url, args.proxy_url,
                  args.proxy_env_file, args.credentials_file, not args.no_restart)
    except (SmokeFailure, httpx.HTTPError, AssertionError) as exc:
        raise SystemExit(f"Local Docker smoke failed: {type(exc).__name__}: {exc}") from None


if __name__ == "__main__":
    main()
