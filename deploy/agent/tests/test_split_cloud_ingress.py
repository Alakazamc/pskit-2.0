"""Contract checks for the two Aliyun host Nginx entrances."""

from pathlib import Path
import json
import subprocess


ROOT = Path(__file__).resolve().parents[3]


def test_split_public_routes_and_private_denials():
    path = ROOT / "deploy/agent/host-nginx-agent-split.conf"
    assert path.exists(), "split public site must have its own template"
    config = path.read_text()
    assert "server_name agent.bioailab.net;" in config
    assert "ssl_certificate /etc/letsencrypt/live/agent.bioailab.net/fullchain.pem;" in config
    assert "location ^~ /api/v1/ {" in config
    assert "proxy_pass http://10.9.8.2:18088;" in config
    assert "proxy_set_header Host $host;" in config
    assert "proxy_set_header X-Forwarded-Proto https;" in config
    assert "proxy_buffering off;" in config
    assert "proxy_request_buffering off;" in config
    assert "proxy_read_timeout 3600s;" in config
    assert "client_max_body_size 25m;" in config
    assert "location ^~ /internal/ { return 404; }" in config
    assert "root /var/www/agent.bioailab.net;" in config
    assert "try_files $uri $uri/ /index.html;" in config
    assert "proxy_pass http://127.0.0.1:18130;" in config
    assert "location ^~ /auth/v1/ { return 404; }" in config
    assert "18085" not in config
    assert "pskit.bioailab.net" not in config
    assert "18185" not in config


def test_supabase_relay_is_wireguard_only():
    path = ROOT / "deploy/agent/host-nginx-supabase-private.conf"
    assert path.exists(), "Supabase needs an A6000-only private relay"
    config = path.read_text()
    assert "listen 10.9.8.1:18130;" in config
    assert "allow 10.9.8.2;" in config
    assert "deny all;" in config
    assert "proxy_pass http://127.0.0.1:18130;" in config
    assert "listen 0.0.0.0" not in config
    assert "pskit.bioailab.net" not in config


def test_supabase_relay_can_run_in_isolated_docker_project():
    deploy = ROOT / "deploy/agent"
    compose = deploy / "compose.supabase-relay.yaml"
    assert compose.exists()
    rendered = subprocess.run(
        ["docker", "compose", "--env-file", str(deploy / "cloud.env.example"),
         "-f", str(compose), "config", "--format", "json"],
        cwd=ROOT, text=True, capture_output=True, check=True,
    )
    stack = json.loads(rendered.stdout)
    assert stack["name"] == "pskit-agent-supabase-relay"
    assert set(stack["services"]) == {"relay"}
    relay = stack["services"]["relay"]
    assert relay["network_mode"] == "host"
    assert relay["user"] == "101:101"
    assert relay["read_only"] is True
    assert relay["cap_drop"] == ["ALL"]
    assert not relay.get("ports")
    assert relay["volumes"][0]["source"].endswith("host-nginx-supabase-private.conf")


def test_host_nginx_install_requires_root_and_keeps_rollback_copy():
    script = ROOT / "deploy/agent/scripts/install_host_nginx_agent.sh"
    assert script.exists()
    content = script.read_text()
    assert "$(id -u)" in content
    assert "nginx -t" in content
    assert "systemctl reload nginx" in content
    assert "agent.bioailab.net.conf.pre-a6000" in content
    assert "frontend-dist" in content
    assert "10.9.8.2:18088" in content
    subprocess.run(["bash", "-n", str(script)], check=True)
