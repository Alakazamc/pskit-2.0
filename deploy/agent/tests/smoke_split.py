"""Public smoke for Aliyun Web/Supabase and A6000 Python/Pi API.

Use only a dedicated test account. This prints counts and status, never
credentials, tokens, messages or uploaded content.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

import httpx


class SmokeFailure(Exception):
    pass


def load_credentials(path: Path) -> tuple[str, str]:
    """Read one existing test account from a private JSON file."""
    if path.stat().st_mode & 0o077:
        raise SmokeFailure("Test credential file must be mode 0600")
    account = json.loads(path.read_text())
    email, password = account.get("email"), account.get("password")
    if not isinstance(email, str) or not email or not isinstance(password, str) or not password:
        raise SmokeFailure("Test credential file needs email and password")
    return email, password


def checked(response: httpx.Response, label: str, status: int = 200):
    if response.status_code != status:
        raise SmokeFailure(f"{label}: HTTP {response.status_code}")
    return response.json() if response.content else None


def project_key(project_id: str) -> str:
    if not project_id.startswith("project-"):
        raise SmokeFailure("Unexpected migrated project ID")
    return "g-p-" + project_id.removeprefix("project-")


def stream_run(app: httpx.Client, run_id: str, headers: dict[str, str]) -> dict:
    """Read actual SSE chunks until the run finishes; keep no event content."""
    started = time.monotonic()
    first: float | None = None
    count = 0
    completed = False
    with app.stream("GET", f"/api/v1/runs/{run_id}/events",
                    params={"follow": "true"}, headers=headers, timeout=120) as response:
        if response.status_code != 200:
            raise SmokeFailure(f"Run event stream: HTTP {response.status_code}")
        if "text/event-stream" not in response.headers.get("content-type", ""):
            raise SmokeFailure("Run event stream has wrong content type")
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            if first is None:
                first = time.monotonic() - started
            event = json.loads(line[6:])
            count += 1
            if event.get("type") == "run.failed":
                raise SmokeFailure("Agent run failed")
            if event.get("type") == "run.completed":
                completed = True
                break
    if not completed or first is None:
        raise SmokeFailure("Agent run did not complete in SSE stream")
    return {"events": count, "first_event_seconds": first,
            "stream_seconds": time.monotonic() - started}


def run_smoke(
    app: httpx.Client,
    legacy: httpx.Client,
    email: str,
    password: str,
    expected_project_id: str | None = None,
    expected_session_id: str | None = None,
) -> dict:
    """Check migrated identity/data and a fresh Pi run through the public route."""
    if app.get("/login").status_code != 200:
        raise SmokeFailure("React login page is unavailable")
    if legacy.get("/").status_code != 200:
        raise SmokeFailure("Legacy PSKit site is unavailable")
    for path in ("/internal/compute/af3/jobs/claim", "/auth/v1/admin/users"):
        if app.get(path).status_code != 404:
            raise SmokeFailure("Private API path is public")
    if app.get("/api/v1/usage").status_code != 401:
        raise SmokeFailure("Unauthenticated usage request was allowed")

    login = checked(app.post("/api/v1/auth/login", json={"email": email,
                                                         "password": password}), "Login")
    token = login.get("access_token")
    if not token:
        raise SmokeFailure("Login returned no access token")
    headers = {"Authorization": f"Bearer {token}"}
    if not checked(app.get("/api/v1/me", headers=headers), "Identity").get("id"):
        raise SmokeFailure("Identity has no user ID")

    projects = checked(app.get("/api/v1/g", headers=headers), "Migrated projects")
    if not isinstance(projects, list) or not projects:
        raise SmokeFailure("No migrated project found")
    if expected_project_id:
        projects = [project for project in projects if project.get("id") == expected_project_id]
        if not projects:
            raise SmokeFailure("Expected project is missing")

    selected = None
    for project in projects:
        key = project_key(project["id"])
        sessions = checked(app.get(f"/api/v1/g/{key}/c", headers=headers),
                           "Migrated sessions")
        if expected_session_id:
            sessions = [session for session in sessions
                        if session.get("id") == expected_session_id]
        for session in sessions:
            messages = checked(app.get(
                f"/api/v1/g/{key}/c/{session['id']}/messages", headers=headers,
            ), "Migrated messages")
            if messages:
                selected = (key, session["id"])
                break
        if selected:
            break
    if selected is None:
        raise SmokeFailure("Expected migrated session/messages are missing")
    key, _old_session_id = selected

    usage = checked(app.get("/api/v1/usage", headers=headers), "Token and GPU quotas")
    if not isinstance(usage.get("tokens"), dict) or not isinstance(usage.get("gpu"), dict):
        raise SmokeFailure("Token or GPU quota is missing")
    uploaded = checked(app.put("/api/v1/files/content", params={"name": "migration-smoke.txt"},
                               headers={**headers, "Content-Type": "text/plain"},
                               content=b"migration smoke"), "Small file upload")
    if not uploaded.get("id"):
        raise SmokeFailure("File upload returned no ID")

    session = checked(app.post(f"/api/v1/g/{key}/c", headers=headers,
                               json={"title": "A6000 migration smoke"}),
                      "Create smoke session", status=201)
    run = checked(app.post(f"/api/v1/g/{key}/c/{session['id']}/messages",
                           headers=headers, json={"content": "Say hello briefly."}),
                  "Start Pi run")
    if not run.get("run_id"):
        raise SmokeFailure("Message returned no run ID")
    result = stream_run(app, run["run_id"], headers)
    state = checked(app.get(f"/api/v1/runs/{run['run_id']}", headers=headers),
                    "Completed Pi run")
    if state.get("status") != "completed":
        raise SmokeFailure("Pi run status is not completed")
    new_messages = checked(app.get(f"/api/v1/g/{key}/c/{session['id']}/messages",
                                   headers=headers), "Pi response")
    if not any(message.get("role") == "assistant" for message in new_messages):
        raise SmokeFailure("No persisted assistant response")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="https://agent.bioailab.net")
    parser.add_argument("--legacy-url", default="https://pskit.bioailab.net")
    parser.add_argument("--credentials-file", type=Path, required=True)
    parser.add_argument("--expected-project-id")
    parser.add_argument("--expected-session-id")
    args = parser.parse_args()
    try:
        email, password = load_credentials(args.credentials_file)
        timeout = httpx.Timeout(30.0, read=120.0)
        with httpx.Client(base_url=args.base_url, timeout=timeout,
                          follow_redirects=True, trust_env=False) as app, \
                httpx.Client(base_url=args.legacy_url, timeout=30,
                             follow_redirects=True, trust_env=False) as old:
            result = run_smoke(app, old, email, password,
                               args.expected_project_id, args.expected_session_id)
    except SmokeFailure as exc:
        raise SystemExit(f"Split smoke failed: {exc}") from None
    except (httpx.HTTPError, OSError, ValueError):
        raise SystemExit("Split smoke failed: network, file, or response error") from None
    print(f"Split smoke passed: login, history, quota, upload, Pi/SSE, private paths, "
          f"legacy site; events={result['events']}")


if __name__ == "__main__":
    main()
