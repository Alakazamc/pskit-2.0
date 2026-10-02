"""Cloud Compose and ingress contracts; no remote host is required."""

import json
import os
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[3]


def compose_config(files: list[Path], env_example: Path,
                   profiles: tuple[str, ...] = ()) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        env_file = Path(directory) / "cloud.env"
        env_file.write_text(env_example.read_text())
        env = os.environ.copy()
        env.pop("COMPOSE_FILE", None)
        result = subprocess.run(
            ["docker", "compose", *[argument for profile in profiles
                                   for argument in ("--profile", profile)],
             "--env-file", str(env_file),
             *[argument for path in files for argument in ("-f", str(path))],
             "config", "--format", "json"],
            cwd=files[0].parent, env=env, text=True, capture_output=True,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)


def published_host_ips(config: dict) -> list[str]:
    return [port.get("host_ip", "") for service in config["services"].values()
            for port in service.get("ports", [])]


def test_cloud_supabase_is_private_and_uses_durable_volumes():
    base = ROOT / "infra/supabase"
    config = compose_config([base / "docker-compose.yml", base / "compose.cloud.yaml"],
                            base / ".env.example")
    assert config["name"] == "pskit-agent-supabase"
    assert all(ip == "127.0.0.1" for ip in published_host_ips(config))
    assert "mailpit" not in config["services"]
    assert "templates-server" in config["services"]
    auth = config["services"]["auth"]["environment"]
    assert auth["GOTRUE_DISABLE_SIGNUP"] == "true"
    assert auth["GOTRUE_MAILER_AUTOCONFIRM"] == "false"
    assert auth["GOTRUE_MAILER_TEMPLATES_CONFIRMATION"].endswith("/confirmation.html")
    assert auth["GOTRUE_SMTP_HOST"]
    for service, target in (("db", "/var/lib/postgresql/data"),
                            ("storage", "/var/lib/storage"),
                            ("imgproxy", "/var/lib/storage")):
        mount = next(volume for volume in config["services"][service]["volumes"]
                     if volume["target"] == target)
        assert mount["type"] == "volume"
        assert config["volumes"][mount["source"]]["name"].startswith("pskit-agent-")


def test_cloud_app_has_only_loopback_published_ports():
    base = ROOT / "deploy/agent"
    config = compose_config([base / "compose.yaml", base / "compose.local.yaml",
                             base / "compose.cloud.yaml"],
                            base / "cloud.env.example", profiles=("private-test",))
    assert config["name"] == "pskit-agent-cloud"
    assert all(ip == "127.0.0.1" for ip in published_host_ips(config))
    assert config["services"]["backend"]["environment"]["RESEARCH_AGENT_MEMBER_DAILY_GPU_MINUTES"] == "0"
    assert config["services"]["backend"]["environment"]["RESEARCH_AGENT_AUTH_COOKIE_SECURE"] == "true"
    assert config["networks"]["supabase"]["name"] == "pskit-agent-supabase_default"
    mock = config["services"]["model-gateway-mock"]
    assert mock["image"] == config["services"]["backend"]["image"]
    assert mock["profiles"] == ["private-test"]


def test_private_nginx_is_wireguard_only():
    config = (ROOT / "deploy/agent/host-nginx-af3.conf").read_text()
    assert "listen 10.9.8.1:18184" in config
    assert "allow 10.9.8.2;" in config
    assert "deny all;" in config
    assert "proxy_pass http://127.0.0.1:18185" in config
    assert "pskit.bioailab.net" not in config
