"""Bounded real-model file write/read/download through isolated Staging."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import uuid
from pathlib import Path

import httpx

from deploy.agent.scripts.seed_staging import _private_env, _project_key
from deploy.agent.tests.smoke_staging import SmokeFailure, _checked


def run_smoke(config_dir: Path, production_env_file: Path, gateway_url: str, model: str) -> dict:
    if gateway_url not in {"http://10.9.8.1:4000", "http://10.9.8.1:4001"}:
        raise ValueError("Use the existing private production or candidate gateway")
    staging = _private_env(config_dir / "litellm.env")
    production = _private_env(production_env_file)
    identity = _private_env(config_dir / "seed.env")
    backend = _private_env(config_dir / "backend.env.base")
    if staging["LITELLM_MASTER_KEY"] == production["LITELLM_MASTER_KEY"]:
        raise ValueError("Staging and production must have independent credentials")
    if not identity["STAGING_USER_EMAIL"].endswith("@example.invalid"):
        raise ValueError("Only the synthetic Staging identity is allowed")
    prefix = "tool-wire-" + uuid.uuid4().hex[:12]
    content = f"# Markdown download verification\n\n{prefix}\n"
    key = route = run_id = None
    headers = {}
    original_token_limit = None
    original_gpu_limit = None
    admin_headers = {"X-Admin-Key": backend["RESEARCH_AGENT_ADMIN_API_KEY"]}
    limits_url = None
    cleanup_errors = []
    with (
        httpx.Client(base_url=gateway_url, timeout=45, trust_env=False,
                     headers={"Authorization": f"Bearer {production['LITELLM_MASTER_KEY']}"}) as prod,
        httpx.Client(base_url="http://10.9.8.1:4002", timeout=45, trust_env=False,
                     headers={"Authorization": f"Bearer {staging['LITELLM_MASTER_KEY']}"}) as gateway,
        httpx.Client(base_url="http://10.9.8.1:18132", timeout=45, trust_env=False) as app,
    ):
        try:
            key = _checked(prod.post("/key/generate", json={
                "key_alias": prefix, "models": [model], "team_id": "pskit-lab",
                "max_budget": 0.25, "duration": "20m", "rpm_limit": 12,
                "tpm_limit": 80_000, "default_estimated_output_tokens": 512,
            }), "Create bounded tool qualification key")["key"]
            deployed = _checked(gateway.post("/model/new", json={
                "model_name": prefix,
                "litellm_params": {"model": f"openai/{model}", "api_base": gateway_url + "/v1", "api_key": key},
                "model_info": {"base_model": model, "mode": "chat", "supports_function_calling": True},
            }), "Create temporary Staging tool route")
            route = deployed["model_info"]["id"]
            login = _checked(app.post("/api/v1/auth/login", json={
                "email": identity["STAGING_USER_EMAIL"], "password": identity["STAGING_USER_PASSWORD"],
            }), "Synthetic Staging login")
            headers = {"Authorization": f"Bearer {login['access_token']}"}
            usage = _checked(app.get("/api/v1/usage", headers=headers), "Synthetic test quota")
            original_token_limit = usage["tokens"]["limit"]
            original_gpu_limit = usage["gpu"]["limit"]
            limits_url = f"/api/v1/admin/users/{login['user']['id']}/limits"
            _checked(app.put(limits_url, headers=admin_headers,
                             json={"token_monthly_limit": original_token_limit + 200_000,
                                   "gpu_daily_minutes": original_gpu_limit}),
                     "Reserve synthetic tool qualification quota")
            deadline = time.monotonic() + 100
            while True:
                models = _checked(app.get("/api/v1/models", headers=headers), "Staging model discovery")
                if any(item["id"] == prefix for item in models):
                    break
                if time.monotonic() >= deadline:
                    raise SmokeFailure("Temporary tool route was not discovered")
                time.sleep(3)
            projects = _checked(app.get("/api/v1/g", headers=headers), "Synthetic projects")
            project = next(item for item in projects if item["name"] == "Staging research")
            path = f"/api/v1/g/{_project_key(project['id'])}/c"
            session = _checked(app.post(path, headers=headers, json={"title": prefix}), "Tool session", 201)
            run = _checked(app.post(f"{path}/{session['id']}/messages", headers=headers, json={
                "model": prefix,
                "content": "Create a downloadable Markdown file named tool-check.md in the artifacts directory "
                           "for this attempt. Use the real write_file tool, then read_file to verify its exact contents. "
                           "The entire UTF-8 content must be this JSON string decoded: " + json.dumps(content) +
                           ". Then reply briefly with a Markdown download link. Do not print code or fake tool tags.",
            }), "Real tool run")
            run_id = run["run_id"]
            events = []
            with app.stream("GET", f"/api/v1/runs/{run_id}/events", headers=headers,
                            params={"follow": "true"}, timeout=240) as response:
                if response.status_code != 200:
                    raise SmokeFailure(f"Tool SSE: HTTP {response.status_code}")
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        event = json.loads(line[6:])
                        events.append(event)
                        if event["type"] in {"run.completed", "run.failed", "run.cancelled"}:
                            break
            types = [e["type"] for e in events]
            completed_tools = [e["data"]["tool"] for e in events
                               if e["type"] == "tool.finished" and e["data"]["status"] == "completed"]
            if not types or types[-1] != "run.completed" or not {"write_file", "read_file"}.issubset(completed_tools):
                raise SmokeFailure(f"Real tool execution missing: run={run_id}, events={types}, tools={completed_tools}")
            artifacts = _checked(app.get(f"/api/v1/sessions/{session['id']}/artifacts", headers=headers), "Session artifacts")
            artifact = next((a for a in artifacts if a["name"] == "tool-check.md"), None)
            if artifact is None or "artifact.created" not in types:
                raise SmokeFailure("The completed tool run did not publish its artifact")
            history = _checked(app.get(f"{path}/{session['id']}/messages", headers=headers), "Persisted chat artifacts")
            if not any(part.get("type") == "artifact" and part.get("id") == artifact["id"]
                       for message in history for part in message.get("parts", [])):
                raise SmokeFailure("Chat history has no typed artifact part for the downloadable file")
            download = app.get(f"/api/v1/artifacts/{artifact['id']}/download", headers=headers)
            if download.status_code != 200 or download.content != content.encode():
                raise SmokeFailure("Downloaded bytes differ from the requested Markdown file")
            preview = _checked(app.get(f"/api/v1/artifacts/{artifact['id']}/preview", headers=headers), "Artifact preview")
            if preview["text"] != content:
                raise SmokeFailure("Artifact preview differs from downloaded bytes")
            if app.get(f"/api/v1/artifacts/{artifact['id']}/download").status_code != 401:
                raise SmokeFailure("Artifact download is accessible without authentication")
            spend = _checked(prod.get("/key/info", params={"key": key}), "Qualification metering")["info"].get("spend")
            result = {"model": model, "gateway_url": gateway_url, "session_id": session["id"], "run_id": run_id,
                      "tools": completed_tools, "events": types, "artifact_id": artifact["id"],
                      "chat_artifact_part": True,
                      "download_bytes": len(download.content), "download_sha256": hashlib.sha256(download.content).hexdigest(),
                      "key_max_budget_usd": 0.25, "key_spend_usd": spend}
        finally:
            if run_id and headers:
                try:
                    state = app.get(f"/api/v1/runs/{run_id}", headers=headers)
                    if state.status_code == 200 and state.json().get("status") not in {"completed", "failed", "cancelled"}:
                        app.delete(f"/api/v1/runs/{run_id}", headers=headers).raise_for_status()
                except httpx.HTTPError:
                    cleanup_errors.append("run")
            if route:
                try:
                    gateway.post("/model/delete", json={"id": route}).raise_for_status()
                except httpx.HTTPError:
                    cleanup_errors.append("route")
            if limits_url is not None and original_token_limit is not None:
                try:
                    app.put(limits_url, headers=admin_headers,
                            json={"token_monthly_limit": original_token_limit,
                                  "gpu_daily_minutes": original_gpu_limit}).raise_for_status()
                except httpx.HTTPError:
                    cleanup_errors.append("synthetic quota")
            if key:
                try:
                    prod.post("/key/delete", json={"keys": [key]}).raise_for_status()
                except httpx.HTTPError:
                    cleanup_errors.append("key")
            if cleanup_errors:
                raise SmokeFailure(f"Temporary tool qualification cleanup failed: {cleanup_errors}")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--production-env-file", type=Path, required=True)
    parser.add_argument("--gateway-url", default="http://10.9.8.1:4000")
    parser.add_argument("--model", default="anthropic/claude-sonnet-4-5-20250929")
    args = parser.parse_args()
    try:
        print(json.dumps(run_smoke(args.config_dir, args.production_env_file, args.gateway_url, args.model)))
    except SmokeFailure as error:
        raise SystemExit(str(error)) from None
    except (httpx.HTTPError, KeyError, ValueError, StopIteration) as error:
        raise SystemExit(f"Workspace qualification failed ({type(error).__name__}); inspect private service status") from None
