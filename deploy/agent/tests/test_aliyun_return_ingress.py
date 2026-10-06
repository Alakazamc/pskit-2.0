"""Check the Aliyun Nginx routes and rollback when reload fails."""

import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "deploy/agent"
PUBLIC = DEPLOY / "scripts/install_host_nginx_agent_cloud.sh"
PRIVATE = DEPLOY / "scripts/enable_private_af3_ingress.sh"


def test_return_public_config_keeps_static_auth_and_private_api():
    config = (DEPLOY / "host-nginx-agent-aliyun.conf").read_text()
    assert "root /var/www/agent.bioailab.net;" in config
    assert "try_files $uri $uri/ /index.html;" in config
    assert "ssl_certificate /etc/letsencrypt/live/agent.bioailab.net/fullchain.pem;" in config
    assert "location ^~ /api/v1/ {" in config
    assert "proxy_pass http://127.0.0.1:18088;" in config
    assert "10.9.8.2:18088" not in config
    assert "proxy_buffering off;" in config
    assert "proxy_request_buffering off;" in config
    assert "location = /internal { return 404; }" in config
    assert "location ^~ /internal/ { return 404; }" in config
    assert "location = /auth/v1/authorize {" in config
    assert "location = /auth/v1/callback {" in config
    assert "location = /auth/v1/verify {" in config
    assert "location ^~ /auth/v1/ { return 404; }" in config


def test_admin_page_and_api_are_restricted_to_wireguard():
    config = (DEPLOY / "host-nginx-agent-aliyun.conf").read_text()
    for location in (r"location ~\* \^/admin\(\?:/\|\$\)",
                     r"location \^~ /api/v1/admin"):
        match = re.search(rf"{location}\s*\{{([^}}]+)\}}", config)
        assert match, f"Missing protected Nginx location: {location}"
        assert "allow 10.9.8.0/24;" in match.group(1)
        assert "deny all;" in match.group(1)
    assert "try_files /index.html =404;" in config


def _fake_commands(tmp_path: Path, *, curl_status: str) -> dict[str, str]:
    binaries = tmp_path / "bin"
    binaries.mkdir()
    scripts = {
        "id": "printf '0\\n'",
        "nginx": "exit 0",
        "curl": f"printf '{curl_status}'",
        "systemctl": (
            'count=0; test -f "$FAKE_RELOAD_COUNT" && count=$(cat "$FAKE_RELOAD_COUNT"); '
            'count=$((count+1)); printf "%s" "$count" > "$FAKE_RELOAD_COUNT"; '
            'test "$count" -ne 1'
        ),
    }
    for name, body in scripts.items():
        path = binaries / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)
    environment = os.environ.copy()
    environment["PATH"] = str(binaries) + os.pathsep + environment["PATH"]
    environment["FAKE_RELOAD_COUNT"] = str(tmp_path / "reload-count")
    environment["AGENT_NGINX_CONF_DIR"] = str(tmp_path / "conf.d")
    return environment


def test_public_installer_restores_previous_config_if_reload_fails(tmp_path: Path):
    config_dir = tmp_path / "conf.d"
    config_dir.mkdir()
    target = config_dir / "agent.bioailab.net.conf"
    target.write_text("old upstream\n")
    result = subprocess.run(
        ["bash", str(PUBLIC)], check=False, text=True, capture_output=True,
        env=_fake_commands(tmp_path, curl_status="401"),
    )
    assert result.returncode != 0
    assert target.read_text() == "old upstream\n"
    backups = list(config_dir.glob("agent.bioailab.net.conf.pre-update-*"))
    assert len(backups) == 1
    assert backups[0].read_text() == "old upstream\n"
    assert (tmp_path / "reload-count").read_text() == "2"


def test_public_installer_waits_for_new_upstream_after_reload(tmp_path: Path):
    config_dir = tmp_path / "conf.d"
    config_dir.mkdir()
    target = config_dir / "agent.bioailab.net.conf"
    target.write_text("old upstream\n")
    backup = config_dir / "agent.bioailab.net.conf.pre-aliyun-20261003"
    backup.write_text("original migration baseline\n")
    environment = _fake_commands(tmp_path, curl_status="401")
    (tmp_path / "bin/systemctl").write_text("#!/bin/sh\nexit 0\n")
    (tmp_path / "bin/curl").write_text(
        "#!/bin/sh\n"
        'count=0; test -f "$FAKE_CURL_COUNT" && count=$(cat "$FAKE_CURL_COUNT"); '
        'count=$((count+1)); printf "%s" "$count" > "$FAKE_CURL_COUNT"\n'
        'case "$count" in 1) printf 401;; 2) exit 0;; '
        '3) printf 502;; *) printf 401;; esac\n'
    )
    environment["FAKE_CURL_COUNT"] = str(tmp_path / "curl-count")
    result = subprocess.run(
        ["bash", str(PUBLIC)], check=False, text=True, capture_output=True, env=environment,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "curl-count").read_text() == "4"
    assert "proxy_pass http://127.0.0.1:18088" in target.read_text()
    assert backup.read_text() == "original migration baseline\n"
    update_backups = list(config_dir.glob("agent.bioailab.net.conf.pre-update-*"))
    assert len(update_backups) == 1
    assert update_backups[0].read_text() == "old upstream\n"


def test_private_installer_restores_disabled_config_if_reload_fails(tmp_path: Path):
    config_dir = tmp_path / "conf.d"
    config_dir.mkdir()
    disabled = config_dir / "agent-af3-private.conf.disabled-20261002"
    disabled.write_text("listen 10.9.8.1:18184;\n")
    result = subprocess.run(
        ["bash", str(PRIVATE)], check=False, text=True, capture_output=True,
        env=_fake_commands(tmp_path, curl_status="404"),
    )
    assert result.returncode != 0
    assert disabled.read_text() == "listen 10.9.8.1:18184;\n"
    assert not (config_dir / "agent-af3-private.conf").exists()
    assert (tmp_path / "reload-count").read_text() == "2"


def test_private_installer_waits_for_nginx_listener_after_reload(tmp_path: Path):
    config_dir = tmp_path / "conf.d"
    config_dir.mkdir()
    disabled = config_dir / "agent-af3-private.conf.disabled-20261002"
    disabled.write_text("listen 10.9.8.1:18184;\n")
    environment = _fake_commands(tmp_path, curl_status="404")
    (tmp_path / "bin/systemctl").write_text("#!/bin/sh\nexit 0\n")
    (tmp_path / "bin/curl").write_text(
        "#!/bin/sh\n"
        'count=0; test -f "$FAKE_CURL_COUNT" && count=$(cat "$FAKE_CURL_COUNT"); '
        'count=$((count+1)); printf "%s" "$count" > "$FAKE_CURL_COUNT"\n'
        'if [ "$count" -eq 1 ]; then printf 404; '
        'elif [ "$count" -eq 2 ]; then printf 000; exit 7; '
        'else printf 403; fi\n'
    )
    environment["FAKE_CURL_COUNT"] = str(tmp_path / "curl-count")
    result = subprocess.run(
        ["bash", str(PRIVATE)], check=False, text=True, capture_output=True, env=environment,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "curl-count").read_text() == "3"
    assert (config_dir / "agent-af3-private.conf").exists()
    assert not disabled.exists()


def test_private_ingress_allows_only_a6000():
    config = (DEPLOY / "host-nginx-af3.conf").read_text()
    assert "listen 10.9.8.1:18184;" in config
    assert "allow 10.9.8.2;" in config
    assert "deny all;" in config
    assert "proxy_pass http://127.0.0.1:18185;" in config
