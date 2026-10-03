"""The private site must never replace the public production virtual host."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from deploy.agent.scripts.prepare_staging import _dist_hash


ROOT = Path(__file__).resolve().parents[3]
AGENT = ROOT / "deploy/agent"
SOURCE = AGENT / "host-nginx-agent-staging.conf"
INSTALL = AGENT / "scripts/install_host_nginx_staging.sh"


def test_staging_vhost_binds_wireguard_and_blocks_private_routes():
    text = SOURCE.read_text()
    assert "listen 10.9.8.1:18132" in text
    assert "listen 0.0.0.0" not in text
    assert "127.0.0.1:18090" in text
    assert "location ^~ /internal/" in text
    assert "location ^~ /auth/v1/" in text
    assert "try_files $uri $uri/ /index.html" in text
    assert "proxy_buffering off" in text


def _fixture(tmp_path: Path):
    host = tmp_path / "host"
    conf = host / "etc/nginx/conf.d"
    conf.mkdir(parents=True)
    production = conf / "agent.bioailab.net.conf"
    production.write_text("production vhost\n")
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("staging release")
    manifest = private / "manifest.json"
    manifest.write_text(json.dumps({"frontend_dist": str(dist),
                                    "frontend_dist_sha256": _dist_hash(dist)}))
    manifest.chmod(0o600)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, script in {
        "ip": "#!/bin/sh\n[ \"${FAKE_WG_PRESENT:-0}\" = 1 ] && echo '10.9.8.1/32'\n",
        "nginx": "#!/bin/sh\n[ \"${FAKE_NGINX_FAIL:-0}\" != 1 ]\n",
        "systemctl": "#!/bin/sh\nexit 0\n",
    }.items():
        path = bin_dir / name
        path.write_text(script)
        path.chmod(0o755)
    env = dict(os.environ)
    env.update({"PATH": f"{bin_dir}:{env['PATH']}", "STAGING_TEST_ROOT": str(host),
                "STAGING_CONFIG_DIR": str(private)})
    return host, private, production, env


def _install(env: dict[str, str], *args: str):
    return subprocess.run(["bash", str(INSTALL), *args], env=env,
                          capture_output=True, text=True)


def test_installer_refuses_missing_wireguard_and_existing_vhost(tmp_path):
    host, _private, production, env = _fixture(tmp_path)
    missing = _install(env)
    assert missing.returncode != 0
    assert "WireGuard" in missing.stderr
    assert production.read_text() == "production vhost\n"
    assert not (host / "etc/nginx/conf.d/agent-staging-private.conf").exists()
    env["FAKE_WG_PRESENT"] = "1"
    stage_conf = host / "etc/nginx/conf.d/agent-staging-private.conf"
    stage_conf.write_text("old staging vhost\n")
    existing = _install(env)
    assert existing.returncode != 0
    assert "already exists" in existing.stderr
    assert stage_conf.read_text() == "old staging vhost\n"
    assert production.read_text() == "production vhost\n"


def test_nginx_failure_restores_existing_staging_and_leaves_production(tmp_path):
    host, _private, production, env = _fixture(tmp_path)
    env.update({"FAKE_WG_PRESENT": "1", "FAKE_NGINX_FAIL": "1"})
    stage_conf = host / "etc/nginx/conf.d/agent-staging-private.conf"
    stage_conf.write_text("old staging vhost\n")
    web_root = host / "var/www/agent-staging"
    web_root.mkdir(parents=True)
    (web_root / "index.html").write_text("old staging release")
    result = _install(env, "--replace-staging")
    assert result.returncode != 0
    assert "Nginx validation failed" in result.stderr
    assert stage_conf.read_text() == "old staging vhost\n"
    assert (web_root / "index.html").read_text() == "old staging release"
    assert production.read_text() == "production vhost\n"


def test_copy_failure_after_moving_old_site_restores_it(tmp_path):
    host, _private, production, env = _fixture(tmp_path)
    env["FAKE_WG_PRESENT"] = "1"
    stage_conf = host / "etc/nginx/conf.d/agent-staging-private.conf"
    stage_conf.write_text("old staging vhost\n")
    web_root = host / "var/www/agent-staging"
    web_root.mkdir(parents=True)
    (web_root / "index.html").write_text("old staging release")
    cp = Path(env["PATH"].split(":", 1)[0]) / "cp"
    cp.write_text("#!/bin/sh\ncase \"$2\" in */vhost.conf) case \"$3\" in *agent-staging-private.conf) exit 73;; esac;; esac\nexec /bin/cp \"$@\"\n")
    cp.chmod(0o755)
    result = _install(env, "--replace-staging")
    assert result.returncode != 0
    assert stage_conf.read_text() == "old staging vhost\n"
    assert (web_root / "index.html").read_text() == "old staging release"
    assert production.read_text() == "production vhost\n"
