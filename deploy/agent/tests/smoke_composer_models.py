"""Run one bounded real-model prompt through isolated Staging; revoke temporary routes."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path

import httpx
from app.services.model_catalog import ModelCatalog

from deploy.agent.scripts.seed_staging import _private_env, _project_key
from deploy.agent.tests.smoke_staging import SmokeFailure, _checked


def run_live_smoke(config_dir: Path, production_env_file: Path, models: list[str]) -> dict:
    if len(models) != 2 or len(set(models)) != 2:
        raise ValueError("Exactly two different real aliases are required")
    staging = _private_env(config_dir / "litellm.env")
    production = _private_env(production_env_file)
    if staging["LITELLM_MASTER_KEY"] == production["LITELLM_MASTER_KEY"]:
        raise ValueError("Staging and production gateway credentials must differ")
    identity = _private_env(config_dir / "seed.env")
    if (not identity.get("STAGING_USER_EMAIL", "").endswith("@example.invalid")
            or not identity.get("STAGING_USER_PASSWORD")):
        raise ValueError("Real-model smoke requires a synthetic Staging identity")
    prefix = f"release-live-{uuid.uuid4().hex[:12]}"
    temporary_key = None
    routes = []
    run_id = None
    headers = {}
    cleanup_errors = []
    with (
        httpx.Client(base_url="http://10.9.8.1:4000", timeout=30, trust_env=False,
                     headers={"Authorization": f"Bearer {production['LITELLM_MASTER_KEY']}"}) as prod,
        httpx.Client(base_url="http://10.9.8.1:4002", timeout=30, trust_env=False,
                     headers={"Authorization": f"Bearer {staging['LITELLM_MASTER_KEY']}"}) as gateway,
        httpx.Client(base_url="http://10.9.8.1:18132", timeout=30, trust_env=False) as app,
    ):
        try:
            key = _checked(prod.post("/key/generate", json={
                "key_alias": prefix, "models": models, "team_id": "pskit-lab",
                "max_budget": 0.25, "duration": "20m", "rpm_limit": 3,
                "tpm_limit": 20_000, "default_estimated_output_tokens": 256,
            }), "Create limited release key")
            temporary_key = key["key"]
            catalog = ModelCatalog(base_url="http://10.9.8.1:4000/v1",
                                   api_key=temporary_key, default_model=models[0])
            available = {model.id: model for model in asyncio.run(catalog.list_models())}
            if not all(model in available for model in models):
                raise SmokeFailure("Release key cannot discover the selected real aliases")
            if not available[models[0]].reasoning_levels:
                raise SmokeFailure("First real alias has no known reasoning capability")
            aliases = []
            for index, model in enumerate(models):
                metadata = available[model]
                alias = f"{prefix}-{index}"
                info = {"base_model": model, "mode": "chat",
                        "supports_vision": metadata.supports_images,
                        "supports_reasoning": bool(metadata.reasoning_levels)}
                for level, flag in {
                    "off": "none", "minimal": "minimal", "low": "low",
                    "xhigh": "xhigh", "max": "max",
                }.items():
                    info[f"supports_{flag}_reasoning_effort"] = level in metadata.reasoning_levels
                deployed = _checked(gateway.post("/model/new", json={
                    "model_name": alias,
                    "litellm_params": {"model": f"openai/{model}",
                                       "api_base": "http://10.9.8.1:4000/v1",
                                       "api_key": temporary_key},
                    "model_info": info,
                }), "Create temporary Staging route")
                routes.append(deployed["model_info"]["id"])
                aliases.append(alias)
            login = _checked(app.post("/api/v1/auth/login", json={
                "email": identity["STAGING_USER_EMAIL"],
                "password": identity["STAGING_USER_PASSWORD"],
            }), "Staging model login")
            headers = {"Authorization": f"Bearer {login['access_token']}"}
            deadline = time.monotonic() + 90
            while True:
                listed = _checked(app.get("/api/v1/models", headers=headers), "Staging models")
                by_id = {model["id"]: model for model in listed}
                if all(alias in by_id for alias in aliases):
                    break
                if time.monotonic() >= deadline:
                    raise SmokeFailure("Staging model discovery did not refresh")
                time.sleep(3)
            level = "medium"
            if level not in by_id[aliases[0]]["reasoning_levels"]:
                raise SmokeFailure("Staging route lost known reasoning metadata")
            projects = _checked(app.get("/api/v1/g", headers=headers), "Staging model projects")
            project = next(item for item in projects if item["name"] == "Staging research")
            path = f"/api/v1/g/{_project_key(project['id'])}/c"
            session = _checked(app.post(path, headers=headers, json={
                "title": "Real model release acceptance",
            }), "Staging model session", 201)
            run = _checked(app.post(f"{path}/{session['id']}/messages", headers=headers, json={
                "content": "Reply with only RELEASE_READY. Do not call any tools.",
                "model": aliases[0], "reasoning_effort": level,
            }), "Real model run")
            run_id = run["run_id"]
            terminal = None
            events = []
            with app.stream("GET", f"/api/v1/runs/{run_id}/events",
                            params={"follow": "true"}, headers=headers, timeout=180) as stream:
                if stream.status_code != 200:
                    raise SmokeFailure(f"Real model SSE: HTTP {stream.status_code}")
                for line in stream.iter_lines():
                    if line.startswith("data: "):
                        event = json.loads(line[6:]); events.append(event["type"])
                        if event["type"] in {"run.completed", "run.failed", "run.cancelled"}:
                            terminal = event["type"]
                            break
            if terminal != "run.completed" or "message.delta" not in events:
                raise SmokeFailure(f"Real model acceptance failed: {terminal}")
            info = _checked(prod.get("/key/info", params={"key": temporary_key}),
                            "Release key metering")["info"]
            result = {"real_models_discovered": models, "reasoning_effort": level,
                      "terminal": terminal, "events": events,
                      "key_max_budget_usd": 0.25, "key_spend_usd": info.get("spend")}
        finally:
            if run_id and headers:
                try:
                    state = app.get(f"/api/v1/runs/{run_id}", headers=headers)
                    if state.status_code == 200 and state.json().get("status") not in {
                        "completed", "failed", "cancelled",
                    }:
                        app.delete(f"/api/v1/runs/{run_id}", headers=headers).raise_for_status()
                except (httpx.HTTPError, ValueError, TypeError, AttributeError):
                    cleanup_errors.append("run")
            for route in routes:
                try:
                    gateway.post("/model/delete", json={"id": route}).raise_for_status()
                except httpx.HTTPError:
                    cleanup_errors.append("route")
            if temporary_key:
                try:
                    prod.post("/key/delete", json={"keys": [temporary_key]}).raise_for_status()
                except httpx.HTTPError:
                    cleanup_errors.append("key")
            if cleanup_errors:
                raise SmokeFailure(f"Temporary release resources need cleanup: {cleanup_errors}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", required=True, type=Path)
    parser.add_argument("--production-env-file", required=True, type=Path)
    parser.add_argument("--model", required=True, action="append")
    args = parser.parse_args()
    try:
        result = run_live_smoke(args.config_dir, args.production_env_file, args.model)
    except SmokeFailure as error:
        raise SystemExit(str(error)) from None
    except KeyError as error:
        raise SystemExit(f"Real-model smoke response lacks field: {error.args[0]}") from None
    except (httpx.HTTPError, ValueError, StopIteration):
        raise SystemExit("Real-model smoke failed; inspect private gateway/Agent status") from None
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
