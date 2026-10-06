"""Staging startup must fail closed before any production state can be touched."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from deploy.agent.scripts import prepare_staging
from deploy.agent.scripts import staging_preflight as staging

STAGE_NETWORK = "pskit-agent-supabase-staging_default"
IMAGE_ID = "sha256:" + "a" * 64


def _private(path: Path, content: str) -> Path:
    path.write_text(content)
    path.chmod(0o600)
    return path


@pytest.fixture
def stage(tmp_path):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    _private(root / "supabase.env", "POSTGRES_PASSWORD=stage-db\nJWT_SECRET=stage-jwt\n")
    _private(root / "litellm.env", "LITELLM_MASTER_KEY=stage-master\n"
             "SUPABASE_DOCKER_NETWORK=pskit-agent-supabase-staging_default\n")
    _private(root / "backend.env.base", "RESEARCH_AGENT_DATABASE_URL=postgresql://pskit_app:stage-pass@db:5432/postgres\n"
             "SUPABASE_PUBLISHABLE_KEY=sb_publishable_stage\n"
             "RESEARCH_AGENT_AUTH_ABUSE_MODE=observe\n"
             "RESEARCH_AGENT_AUTH_RATE_LIMIT_SECRET=stage-auth-secret-at-least-thirty-two-bytes\n"
             'RESEARCH_AGENT_AUTH_TRUSTED_PROXY_CIDRS_JSON=["127.0.0.1/32"]\n'
             "RESEARCH_AGENT_AUTH_CAPTCHA_REQUIRED=true\n"
             "TURNSTILE_SECRET_KEY=1x0000000000000000000000000000000AA\n"
             'TURNSTILE_HOSTNAMES_JSON=["dummy-key-pass"]\n'
             'RESEARCH_AGENT_COMPUTE_SERVICE_KEYS_JSON={"coral-mcp":"stage-mcp-key-at-least-thirty-two-bytes"}\n')
    _private(root / "admin.env", "SHARED_POSTGRES_ADMIN_DSN=postgresql://postgres:stage-db@db:5432/postgres\n")
    _private(root / "cloud.env", "AGENT_BACKEND_IMAGE=pskit-agent-backend:fixed\n"
             "AGENT_WEB_IMAGE=pskit-agent-web:unused-staging\n"
             "AGENT_BACKEND_ENV_FILE=" + str(root / "backend.env") + "\n"
             "AGENT_AF3_PROXY_KEY_FILE=" + str(root / "proxy.env") + "\n"
             "AGENT_PUBLIC_URL=http://10.9.8.1:18132\n"
             "TURNSTILE_SITE_KEY=1x00000000000000000000AA\n"
             "AGENT_PG_DATA_VOLUME=pskit-agent-staging_agent_data\n"
             "AGENT_MCP_RECEIVER_ENV_FILE=" + str(root / "mcp.receiver.env") + "\n"
             "AGENT_MCP_RECEIVER_DATA_VOLUME=pskit-agent-staging_mcp_receiver_data\n"
             "AGENT_MCP_SERVICE_ID=coral-mcp\n"
             "AGENT_MCP_WORKER_ID=coral-mcp-staging-1\n"
             "SUPABASE_DOCKER_NETWORK=pskit-agent-supabase-staging_default\n")
    _private(root / "mcp.receiver.env", "PSKIT_COMPUTE_SERVICE_KEY=stage-mcp-key-at-least-thirty-two-bytes\n"
             "PSKIT_MCP_ENDPOINT_OVERRIDES_JSON={}\n"
             "PSKIT_MCP_CREDENTIAL_REFS_JSON={}\n")
    _private(root / "proxy.env", "RESEARCH_AGENT_COMPUTE_CALLBACK_KEY=stage-callback\n")
    _private(root / "seed.env", "STAGING_USER_EMAIL=staging-user@example.invalid\n")
    _private(root / "manifest.json", json.dumps({
        "backend_image": "pskit-agent-backend:fixed", "backend_image_id": IMAGE_ID,
        "frontend_dist": str(tmp_path / "dist"), "frontend_dist_sha256": "0" * 64,
        "projects": ["pskit-agent-supabase-staging", "pskit-agent-litellm-staging",
                     "pskit-agent-staging"],
    }))
    return root


@pytest.fixture
def rendered():
    names = ["studio", "api-gw", "auth", "rest", "realtime", "storage",
             "imgproxy", "meta", "functions", "db", "supavisor"]
    supabase = {
        "name": "pskit-agent-supabase-staging",
        "services": {name: {"container_name": f"{name}-staging", "networks": {"default": None}}
                     for name in names},
        "networks": {"default": {"name": STAGE_NETWORK}},
        "volumes": {
            "agent-db-data": {"name": "pskit-agent-db-data-staging"},
            "agent-storage": {"name": "pskit-agent-storage-staging"},
        },
    }
    supabase["services"]["api-gw"]["ports"] = [
        {"host_ip": "127.0.0.1", "published": "18131", "target": 8000},
    ]
    litellm = {
        "name": "pskit-agent-litellm-staging",
        "services": {"gateway": {"ports": [{"host_ip": "10.9.8.1", "published": "4002"}],
                                 "networks": {"supabase": None},
                                 "environment": {"DATABASE_URL": "postgresql://litellm:stage-pass@db:5432/litellm"}},
                     "model-stub": {"image": "pskit-agent-backend:fixed", "networks": {"supabase": None}}},
        "networks": {"supabase": {"name": STAGE_NETWORK}}, "volumes": {},
    }
    agent = {
        "name": "pskit-agent-staging",
        "services": {"backend": {
            "image": "pskit-agent-backend:fixed",
            "networks": {"app": None, "supabase": None},
            "ports": [{"host_ip": "127.0.0.1", "published": "18090"}],
            "environment": {"RESEARCH_AGENT_AF3_EXECUTOR": "mock",
                            "RESEARCH_AGENT_DATABASE_URL": "postgresql://pskit_app:stage-pass@db:5432/postgres",
                            "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_stage"},
        }, "mcp-receiver": {
            "image": "pskit-agent-backend:fixed",
            "networks": {"app": None, "mcp_egress": None},
            "environment": {"PSKIT_COMPUTE_SERVICE_KEY": "stage-mcp-key-at-least-thirty-two-bytes",
                            "PSKIT_MCP_SERVICE_ID": "coral-mcp"},
            "volumes": [{"type": "volume", "source": "mcp_receiver_data",
                         "target": "/var/lib/pskit-mcp"}],
        }},
        "networks": {"app": {"name": "pskit-agent-staging_app"},
                     "supabase": {"name": STAGE_NETWORK},
                     "mcp_egress": {"name": "pskit-agent-staging_mcp_egress"}},
        "volumes": {"agent_data": {"name": "pskit-agent-staging_agent_data"},
                    "mcp_receiver_data": {"name": "pskit-agent-staging_mcp_receiver_data"}},
    }
    return {"supabase": supabase, "litellm": litellm, "agent": agent}


def _production():
    values = ["prod-db", "prod-jwt", "prod-master", "prod-app", "sb_publishable_prod"]
    return {str(index): hashlib.sha256(value.encode()).hexdigest()
            for index, value in enumerate(values)}


def test_preflight_accepts_isolated_config(stage, rendered):
    staging.validate_staging(stage / "manifest.json", rendered, _production())


def test_preflight_requires_production_fingerprints(stage, rendered):
    with pytest.raises(ValueError):
        staging.validate_staging(stage / "manifest.json", rendered, {})


@pytest.mark.parametrize("part,path,value", [
    ("supabase", ("volumes", "agent-db-data", "name"), "pskit-agent-db-data"),
    ("supabase", ("services", "api-gw", "ports", 0, "host_ip"), "0.0.0.0"),
    ("litellm", ("networks", "supabase", "name"), "pskit-agent-supabase_default"),
    ("agent", ("services", "backend", "environment", "RESEARCH_AGENT_DATABASE_URL"),
     "postgresql://pskit_app:prod-app@db:5432/postgres"),
])
def test_preflight_rejects_production_state_before_start(stage, rendered, part, path, value):
    entry = rendered[part]
    for piece in path[:-1]:
        entry = entry[piece]
    entry[path[-1]] = value
    with pytest.raises(ValueError):
        staging.validate_staging(stage / "manifest.json", rendered, _production())


def test_preflight_rejects_same_production_secret_without_printing_it(stage, rendered, capsys):
    _private(stage / "supabase.env", "POSTGRES_PASSWORD=prod-db\nJWT_SECRET=stage-jwt\n")
    with pytest.raises(ValueError):
        staging.validate_staging(stage / "manifest.json", rendered, _production())
    assert "prod-db" not in "".join(capsys.readouterr())


@pytest.mark.parametrize("change", [
    "extra_network", "effective_secret", "remote_backend_db", "remote_litellm_db",
    "production_bind", "unlocked_image",
])
def test_preflight_rejects_effective_production_state(stage, rendered, change):
    if change == "extra_network":
        rendered["agent"]["networks"]["production"] = {"name": "pskit-agent-supabase_default"}
        rendered["agent"]["services"]["backend"]["networks"] = {"supabase": None, "production": None}
    elif change == "effective_secret":
        rendered["supabase"]["services"]["auth"]["environment"] = {"GOTRUE_JWT_SECRET": "prod-jwt"}
    elif change == "remote_backend_db":
        rendered["agent"]["services"]["backend"]["environment"]["RESEARCH_AGENT_DATABASE_URL"] = (
            "postgresql://pskit_app:stage-pass@production-db:5432/postgres")
    elif change == "remote_litellm_db":
        rendered["litellm"]["services"]["gateway"]["environment"]["DATABASE_URL"] = (
            "postgresql://litellm:stage-pass@production-db:5432/litellm")
    elif change == "production_bind":
        rendered["agent"]["services"]["backend"]["volumes"] = [{
            "type": "bind", "source": "/home/prod/data", "target": "/data", "read_only": False}]
    else:
        rendered["agent"]["services"]["backend"]["image"] = "pskit-agent-backend:other"
    with pytest.raises(ValueError):
        staging.validate_staging(stage / "manifest.json", rendered, _production())


def test_compose_rejects_cloud_env_production_override(stage):
    with (stage / "cloud.env").open("a") as stream:
        stream.write("POSTGRES_PASSWORD=prod-db\n")
    with pytest.raises(ValueError):
        staging._compose_commands(stage, stage / "backend.env.base")


def test_backend_env_interrupted_publish_can_retry(stage, monkeypatch):
    original_link = staging.os.link

    def interrupted(*args, **kwargs):
        raise OSError("interrupted")

    monkeypatch.setattr(staging.os, "link", interrupted)
    with pytest.raises(OSError):
        staging._backend_env(stage, "sk-test")
    assert not (stage / "backend.env").exists()
    monkeypatch.setattr(staging.os, "link", original_link)
    staging._backend_env(stage, "sk-test")
    assert (stage / "backend.env").read_text().endswith("MODEL_GATEWAY_API_KEY=sk-test\n")


def test_compose_never_inherits_production_secrets_from_shell(stage, monkeypatch):
    monkeypatch.setenv("POSTGRES_PASSWORD", "prod-db")
    monkeypatch.setenv("LITELLM_MASTER_KEY", "prod-master")
    commands, env = staging._compose_commands(stage, stage / "backend.env.base")
    assert commands["supabase"][-1] == "pskit-agent-supabase-staging"
    assert "POSTGRES_PASSWORD" not in env
    assert "LITELLM_MASTER_KEY" not in env


def test_real_compose_render_passes_preflight(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("staging")
    root = tmp_path / "private"
    monkeypatch.setenv("STAGING_AUTH_KEYS_NODE", "node")
    monkeypatch.setattr(prepare_staging, "_image_id", lambda image: IMAGE_ID)
    prepare_staging.prepare_staging(root, backend_image="pskit-agent-backend:fixed",
                                    frontend_dist=dist)
    commands, env = staging._compose_commands(root, root / "backend.env.base")
    rendered = staging._render_configs(commands, env)
    staging.validate_staging(root / "manifest.json", rendered, _production())


def test_up_orders_services_and_never_starts_callback(stage, rendered, monkeypatch):
    calls = []
    monkeypatch.setattr(staging, "_render_configs", lambda *args, **kwargs: rendered)
    monkeypatch.setattr(staging, "_check_resources", lambda *args, **kwargs: None)
    monkeypatch.setattr(staging, "_check_ports", lambda *args, **kwargs: None)
    monkeypatch.setattr(staging, "_image_id", lambda image: IMAGE_ID)
    monkeypatch.setattr(staging, "_bootstrap_key", lambda path: _private(path / "virtual-key", "sk-stage-key\n"))
    monkeypatch.setattr(staging, "_run", lambda command, **kwargs: calls.append(command) or "")
    staging.run_staging("up", stage, production_fingerprints=_production())
    joined = [" ".join(command) for command in calls]
    assert next(i for i, line in enumerate(joined) if "supabase-staging" in line and " up " in line) < next(
        i for i, line in enumerate(joined) if "provision_shared_postgres.py" in line
    ) < next(i for i, line in enumerate(joined) if "migrate_postgres" in line) < next(
        i for i, line in enumerate(joined) if "litellm-staging" in line and " up " in line
    ) < next(i for i, line in enumerate(joined) if "pskit-agent-staging" in line and " up " in line)
    agent_up = next(line for line in joined if "pskit-agent-staging" in line and " up " in line)
    assert agent_up.endswith("up -d --wait backend mcp-receiver")
    assert all("af3-callback-proxy" not in line for line in joined)
    assert (stage / "backend.env").stat().st_mode & 0o777 == 0o600
    assert "MODEL_GATEWAY_API_KEY=sk-stage-key" in (stage / "backend.env").read_text()


def test_failed_gateway_does_not_start_backend_or_stop_production(stage, rendered, monkeypatch):
    calls = []
    monkeypatch.setattr(staging, "_render_configs", lambda *args, **kwargs: rendered)
    monkeypatch.setattr(staging, "_check_resources", lambda *args, **kwargs: None)
    monkeypatch.setattr(staging, "_check_ports", lambda *args, **kwargs: None)
    monkeypatch.setattr(staging, "_image_id", lambda image: IMAGE_ID)

    def fake_run(command, **kwargs):
        calls.append(" ".join(command))
        if "pskit-agent-litellm-staging" in calls[-1] and " up " in calls[-1]:
            raise RuntimeError("gateway failed")
        return ""

    monkeypatch.setattr(staging, "_run", fake_run)
    with pytest.raises(RuntimeError):
        staging.run_staging("up", stage, production_fingerprints=_production())
    assert not any("pskit-agent-staging" in line and " up " in line for line in calls)
    assert not any(" down -v" in line or "pskit-agent-cloud" in line for line in calls)


def test_down_keeps_all_staging_volumes(stage, rendered, monkeypatch):
    calls = []
    monkeypatch.setattr(staging, "_run", lambda command, **kwargs: calls.append(command) or "")
    staging.run_staging("down", stage, production_fingerprints=_production())
    assert len(calls) == 3
    assert all(command[-1] == "down" and "-v" not in command for command in calls)
    assert [next(x for x in command if x.startswith("pskit-agent-") and x.endswith("-staging"))
            for command in calls] == ["pskit-agent-staging", "pskit-agent-litellm-staging",
                                      "pskit-agent-supabase-staging"]
