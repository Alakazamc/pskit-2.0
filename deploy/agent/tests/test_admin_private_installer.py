"""A failed private-admin cutover restores the public site configuration."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
INSTALLER = ROOT / "deploy/agent/scripts/install_private_admin_ingress.sh"
SOURCE = ROOT / "deploy/agent/host-nginx-agent-aliyun.conf"


def _environment(tmp_path: Path, *, fail_private_probe: bool = False) -> dict[str, str]:
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    for name, body in {
        "id": "printf '0\\n'",
        "nginx": "exit 0",
        "systemctl": 'printf "reload\\n" >> "$FAKE_RELOAD_LOG"',
    }.items():
        binary = binary_dir / name
        binary.write_text(f"#!/bin/sh\n{body}\n")
        binary.chmod(0o755)
    curl = binary_dir / "curl"
    curl.write_text("""#!/usr/bin/env python3
import os
import sys
arguments = ' '.join(sys.argv[1:]).lower()
wireguard = 'agent.bioailab.net:443:10.9.8.1' in arguments
admin_api = '/api/v1/admin/me' in arguments
admin_page = '/admin' in arguments and not admin_api
if wireguard and admin_page and os.getenv('FAKE_PRIVATE_PROBE_FAIL') == '1':
    print('503', end='')
elif admin_page or admin_api:
    print('401' if wireguard and admin_api else '200' if wireguard else '403', end='')
elif '/api/v1/usage' in arguments:
    print('401', end='')
else:
    print('200', end='')
""")
    curl.chmod(0o755)
    environment = os.environ.copy()
    environment.update({
        "PATH": f"{binary_dir}{os.pathsep}{os.environ['PATH']}",
        "AGENT_NGINX_CONF_DIR": str(tmp_path / "conf.d"),
        "AGENT_ADMIN_PROBE_ATTEMPTS": "1",
        "FAKE_RELOAD_LOG": str(tmp_path / "reload.log"),
        "FAKE_PRIVATE_PROBE_FAIL": "1" if fail_private_probe else "0",
    })
    return environment


def test_installer_keeps_public_site_and_opens_private_admin(tmp_path: Path):
    config_dir = tmp_path / "conf.d"
    config_dir.mkdir()
    target = config_dir / "agent.bioailab.net.conf"
    target.write_text("old public config\n")

    result = subprocess.run(["bash", str(INSTALLER)], check=False, capture_output=True, text=True,
                            env=_environment(tmp_path))

    assert result.returncode == 0, result.stderr
    assert target.read_text() == SOURCE.read_text()
    assert (tmp_path / "reload.log").read_text().splitlines() == ["reload"]
    assert len(list(config_dir.glob("agent.bioailab.net.conf.pre-admin-private.*"))) == 1


def test_installer_restores_previous_config_when_private_admin_probe_fails(tmp_path: Path):
    config_dir = tmp_path / "conf.d"
    config_dir.mkdir()
    target = config_dir / "agent.bioailab.net.conf"
    target.write_text("old public config\n")

    result = subprocess.run(["bash", str(INSTALLER)], check=False, capture_output=True, text=True,
                            env=_environment(tmp_path, fail_private_probe=True))

    assert result.returncode != 0
    assert target.read_text() == "old public config\n"
    assert (tmp_path / "reload.log").read_text().splitlines() == ["reload", "reload"]
