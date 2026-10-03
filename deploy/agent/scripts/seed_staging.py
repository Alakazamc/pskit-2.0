"""Create one repeatable synthetic account and workspace in isolated staging."""

from __future__ import annotations

import argparse
from pathlib import Path

import httpx


AUTH_URL = "http://127.0.0.1:18131"
API_URL = "http://127.0.0.1:18090"
PROJECT_NAME = "Staging research"
SESSION_TITLE = "Staging sample"
FILE_NAME = "staging-notes.txt"
FILE_CONTENT = "Synthetic staging research notes.\n"


def _private_env(path: Path) -> dict[str, str]:
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError("Staging private configuration is missing or unsafe")
    return dict(line.split("=", 1) for line in path.read_text().splitlines()
                if line and not line.startswith("#") and "=" in line)


def _json(response: httpx.Response, *, expected: tuple[int, ...] = (200,)):
    if response.status_code not in expected:
        raise RuntimeError(f"Staging API failed: {response.request.url.path} HTTP {response.status_code}")
    return response.json()


def _project_key(project_id: str) -> str:
    if not project_id.startswith("project-"):
        raise ValueError("Staging project ID has an unexpected form")
    return "g-p-" + project_id.removeprefix("project-")


def seed_staging(
    auth_url: str, api_url: str, supabase_env: Path, credentials_file: Path,
    *, client: httpx.Client | None = None,
) -> dict[str, str]:
    if auth_url != AUTH_URL or api_url != API_URL:
        raise ValueError("Synthetic data may only target the Staging loopback endpoints")
    supabase = _private_env(Path(supabase_env))
    credentials = _private_env(Path(credentials_file))
    backend = _private_env(Path(supabase_env).parent / "backend.env.base")
    email = credentials.get("STAGING_USER_EMAIL", "")
    password = credentials.get("STAGING_USER_PASSWORD", "")
    secret = supabase.get("SUPABASE_SECRET_KEY", "")
    admin_key = backend.get("RESEARCH_AGENT_ADMIN_API_KEY", "")
    if not email.endswith("@example.invalid") or not password or not secret or not admin_key:
        raise ValueError("Staging synthetic identity or administrator key is incomplete")
    owned = client is None
    app = client or httpx.Client(timeout=20, trust_env=False)
    try:
        auth_headers = {"apikey": secret, "Authorization": f"Bearer {secret}"}
        users = _json(app.get(f"{auth_url}/auth/v1/admin/users", headers=auth_headers,
                              params={"page": 1, "per_page": 100})).get("users", [])
        user = next((item for item in users if item.get("email") == email), None)
        if user is None:
            user = _json(app.post(f"{auth_url}/auth/v1/admin/users", headers=auth_headers,
                                  json={"email": email, "password": password,
                                        "email_confirm": True}), expected=(200, 201))
        user_id = user["id"]
        session = _json(app.post(f"{api_url}/api/v1/auth/login",
                                 json={"email": email, "password": password}))
        token = session.get("access_token")
        if not token or session.get("user", {}).get("id") != user_id:
            raise RuntimeError("Staging test account did not authenticate")
        headers = {"Authorization": f"Bearer {token}"}
        projects = _json(app.get(f"{api_url}/api/v1/g", headers=headers))
        project = next((item for item in projects if item.get("name") == PROJECT_NAME), None)
        if project is None:
            project = _json(app.post(f"{api_url}/api/v1/g", headers=headers,
                                     json={"name": PROJECT_NAME, "description": "Synthetic staging data"}),
                            expected=(201,))
        project_id = project["id"]
        project_key = _project_key(project_id)
        path = f"{api_url}/api/v1/g/{project_key}/c"
        sessions = _json(app.get(path, headers=headers))
        chat = next((item for item in sessions if item.get("title") == SESSION_TITLE), None)
        if chat is None:
            chat = _json(app.post(path, headers=headers, json={"title": SESSION_TITLE}),
                         expected=(201,))
        files = _json(app.get(f"{api_url}/api/v1/files", headers=headers))
        file = next((item for item in files if item.get("name") == FILE_NAME), None)
        if file is None:
            file = _json(app.post(f"{api_url}/api/v1/files", headers=headers,
                                  json={"name": FILE_NAME, "size": len(FILE_CONTENT.encode()),
                                        "content": FILE_CONTENT}), expected=(200, 201))
        _json(app.put(f"{api_url}/api/v1/admin/users/{user_id}/limits",
                      headers={"X-Admin-Key": admin_key},
                      json={"token_monthly_limit": 20_000, "gpu_daily_minutes": 5}))
        return {"user_id": user_id, "project_id": project_id,
                "session_id": chat["id"], "file_id": file["id"]}
    except (httpx.HTTPError, KeyError, TypeError) as exc:
        raise RuntimeError("Staging seed failed; inspect private API health") from exc
    finally:
        if owned:
            app.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.config_dir
    seed_staging(AUTH_URL, API_URL, root / "supabase.env", root / "seed.env")
    print("Staging synthetic account, workspace and quota are ready")


if __name__ == "__main__":
    main()
