"""Verify the isolated staging product flow without touching A6000 or production."""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import httpx

from deploy.agent.scripts.seed_staging import _private_env, _project_key


class SmokeFailure(RuntimeError):
    pass


def _docker(command: list[str]) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SmokeFailure("Production read-only snapshot is unavailable") from exc
    return result.stdout.strip()


def _production_psql(database: str, query: str) -> str:
    return _docker(["docker", "exec", "supabase-db", "psql", "-XqAt", "-v",
                    "ON_ERROR_STOP=1", "-U", "postgres", "-d", database,
                    "-c", f"BEGIN READ ONLY; {query}; COMMIT"])


def _legacy_litellm_psql(query: str) -> str:
    return _docker(["docker", "exec", "pskit-agent-litellm-db-1", "psql", "-XqAt", "-v",
                    "ON_ERROR_STOP=1", "-U", "litellm", "-d", "litellm",
                    "-c", f"BEGIN READ ONLY; {query}; COMMIT"])


def _ledger_digest(database: str, schema: str, table: str, *, legacy: bool = False) -> str:
    safe = table.replace('"', '""')
    query = ("SELECT md5(COALESCE(string_agg(row_to_json(t)::text, '' "
             "ORDER BY row_to_json(t)::text), '')) "
             f'FROM "{schema}"."{safe}" t')
    return (_legacy_litellm_psql(query) if legacy else _production_psql(database, query))


def snapshot_production() -> dict[str, int | str]:
    labels = _docker(["docker", "inspect", "supabase-db", "--format",
                      "{{index .Config.Labels \"com.docker.compose.project\"}}"])
    if labels != "pskit-agent-supabase":
        raise SmokeFailure("Production PostgreSQL container identity is unexpected")
    result = {"auth.users": int(_production_psql("postgres", "SELECT count(*) FROM auth.users"))}
    for database, schema, prefix in (("postgres", "pskit", "pskit"),
                                     ("litellm", "public", "litellm")):
        table_list = _production_psql(database,
            "SELECT tablename FROM pg_catalog.pg_tables "
            f"WHERE schemaname='{schema}' ORDER BY tablename")
        if not table_list:
            if schema == "pskit":
                result["pskit.__tables__"] = 0
                continue
            raise SmokeFailure("Production LiteLLM schema is not ready for staging smoke")
        for table in table_list.splitlines():
            safe = table.replace('"', '""')
            result[f"{prefix}.{table}"] = int(_production_psql(
                database, f'SELECT count(*) FROM "{schema}"."{safe}"'))
            if database == "litellm" and any(term in table for term in
                                               ("Spend", "Budget", "TeamTable", "UserTable", "VerificationToken")):
                result[f"{prefix}.{table}.digest"] = _ledger_digest(database, schema, table)
    active_tables = _legacy_litellm_psql(
        "SELECT tablename FROM pg_catalog.pg_tables "
        "WHERE schemaname='public' ORDER BY tablename")
    if not active_tables:
        raise SmokeFailure("Active production LiteLLM schema is unavailable")
    for table in active_tables.splitlines():
        safe = table.replace('"', '""')
        result[f"active_litellm.{table}"] = int(_legacy_litellm_psql(
            f'SELECT count(*) FROM "public"."{safe}"'))
        if any(term in table for term in
               ("Spend", "Budget", "TeamTable", "UserTable", "VerificationToken")):
            result[f"active_litellm.{table}.digest"] = _ledger_digest(
                "litellm", "public", table, legacy=True)
    return result


def _checked(response: httpx.Response, label: str, status: int = 200):
    if response.status_code != status:
        raise SmokeFailure(f"{label}: HTTP {response.status_code}")
    return response.json() if response.content else None


def _verify_usage(usage: dict, entries: list) -> None:
    tokens = usage.get("tokens", {})
    gpu = usage.get("gpu", {})
    if (tokens.get("limit") != 20_000 or gpu.get("limit") != 5 or
            not isinstance(tokens.get("used"), int) or tokens["used"] <= 0):
        raise SmokeFailure("Staging quota or token metering is incorrect")
    if not any(entry.get("resource") == "tokens" and entry.get("amount", 0) > 0
               for entry in entries):
        raise SmokeFailure("Staging token ledger has no metered entry")
    if not any(entry.get("resource") == "gpu_minutes" and entry.get("kind") == "job"
               for entry in entries):
        raise SmokeFailure("Staging GPU ledger has no simulated job")


def _probe(app: httpx.Client, email: str, password: str) -> dict[str, object]:
    if app.base_url.host == "10.9.8.1" and app.get("/login").status_code != 200:
        raise SmokeFailure("Staging React login page is unavailable")
    for path in ("/internal/compute/af3/jobs/claim", "/auth/v1/admin/users"):
        if app.get(path).status_code != 404:
            raise SmokeFailure("Staging private route was exposed")
    if app.get("/api/v1/usage").status_code != 401:
        raise SmokeFailure("Staging usage was available without login")
    login = _checked(app.post("/api/v1/auth/login", json={"email": email, "password": password}),
                     "Staging login")
    token = login.get("access_token")
    if not token:
        raise SmokeFailure("Staging login returned no access token")
    headers = {"Authorization": f"Bearer {token}"}
    usage = _checked(app.get("/api/v1/usage", headers=headers), "Staging quotas")
    if not isinstance(usage.get("tokens"), dict) or not isinstance(usage.get("gpu"), dict):
        raise SmokeFailure("Staging quotas are missing")
    projects = _checked(app.get("/api/v1/g", headers=headers), "Staging projects")
    project = next((item for item in projects if item.get("name") == "Staging research"), None)
    if project is None:
        raise SmokeFailure("Synthetic staging project is missing")
    key = _project_key(project["id"])
    uploaded = _checked(app.put("/api/v1/files/content", params={"name": "smoke-staging.txt"},
                                headers={**headers, "Content-Type": "text/plain"},
                                content=b"staging smoke"), "Staging file upload")
    if not uploaded.get("id"):
        raise SmokeFailure("Staging file upload returned no ID")
    session = _checked(app.post(f"/api/v1/g/{key}/c", headers=headers,
                                json={"title": "Staging release smoke"}),
                       "Staging session", 201)
    run = _checked(app.post(f"/api/v1/g/{key}/c/{session['id']}/messages", headers=headers,
                            json={"content": "Say hello briefly."},
                            ), "Staging Agent run")
    if not run.get("run_id"):
        raise SmokeFailure("Staging Agent run ID is missing")
    events = 0
    complete = False
    with app.stream("GET", f"/api/v1/runs/{run['run_id']}/events",
                    params={"follow": "true"}, headers=headers, timeout=120) as stream:
        if stream.status_code != 200 or "text/event-stream" not in stream.headers.get("content-type", ""):
            raise SmokeFailure("Staging event stream is unavailable")
        for line in stream.iter_lines():
            if not line.startswith("data: "):
                continue
            events += 1
            event = json.loads(line[6:])
            if event.get("type") == "run.failed":
                raise SmokeFailure("Staging Agent run failed")
            if event.get("type") == "run.completed":
                complete = True
                break
    if not complete or events < 2:
        raise SmokeFailure("Staging Agent did not complete through SSE")
    status = _checked(app.get(f"/api/v1/runs/{run['run_id']}", headers=headers),
                      "Staging Agent state")
    if status.get("status") != "completed":
        raise SmokeFailure("Staging Agent state is incomplete")
    job = _checked(app.post("/api/v1/af3/jobs", headers={**headers,
                        "Idempotency-Key": "staging-smoke-af3-v1"},
                            json={"estimated_gpu_minutes": 1}), "Staging AF3 mock")
    if job.get("simulation") is not True:
        raise SmokeFailure("Staging AF3 simulation flag is false")
    _checked(app.delete(f"/api/v1/af3/jobs/{job['id']}", headers=headers),
             "Release staging GPU reservation")
    exhausted = app.post("/api/v1/af3/jobs", headers=headers,
                         json={"estimated_gpu_minutes": 6})
    if exhausted.status_code != 409 or exhausted.json().get("detail", {}).get("code") != "GPU_DAILY_QUOTA_EXCEEDED":
        raise SmokeFailure("Staging GPU quota enforcement is missing")
    entries = _checked(app.get("/api/v1/usage/entries", headers=headers),
                       "Staging usage entries")
    if not isinstance(entries, list):
        raise SmokeFailure("Staging usage entries are unavailable")
    final_usage = _checked(app.get("/api/v1/usage", headers=headers), "Staging final quotas")
    _verify_usage(final_usage, entries)
    return {"simulation": True, "events": events}


def run_smoke(
    app: httpx.Client, email: str, password: str,
    *, production_snapshot: Callable[[], dict[str, int | str]] = snapshot_production,
    probe: Callable[[httpx.Client, str, str], dict[str, object]] = _probe,
) -> dict[str, object]:
    if str(app.base_url).rstrip("/") not in {"http://10.9.8.1:18132", "http://127.0.0.1:18090"}:
        raise SmokeFailure("Smoke target must be the staging private endpoint")
    baseline = production_snapshot()
    result = probe(app, email, password)
    if result.get("simulation") is not True:
        raise SmokeFailure("Staging AF3 simulation was not confirmed")
    if production_snapshot() != baseline:
        raise SmokeFailure("Staging smoke changed the production snapshot")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", required=True, type=Path)
    parser.add_argument("--private-api", action="store_true")
    args = parser.parse_args()
    credentials = _private_env(args.config_dir / "seed.env")
    url = "http://127.0.0.1:18090" if args.private_api else "http://10.9.8.1:18132"
    try:
        with httpx.Client(base_url=url, timeout=30, trust_env=False) as app:
            result = run_smoke(app, credentials["STAGING_USER_EMAIL"],
                               credentials["STAGING_USER_PASSWORD"])
    except (SmokeFailure, httpx.HTTPError, OSError, ValueError, KeyError) as exc:
        raise SystemExit(f"Staging smoke failed: {exc if isinstance(exc, SmokeFailure) else 'request or configuration error'}") from None
    print(f"Staging smoke passed: login, file, SSE, quota, simulated AF3; events={result['events']}")


if __name__ == "__main__":
    main()
