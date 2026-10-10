"""The retired release entry point cannot execute any historical deployment action."""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("arguments", [
    (), ("deploy",), ("up",), ("down",), ("rollback",), ("--rollback",),
    ("rollback", "/tmp/old-release"), ("help",), ("--help",), ("-h",),
    ("--dry-run",), ("--force",), ("unknown", "$(touch should-not-exist)"),
])
def test_every_historical_argument_exits_without_external_commands(tmp_path, arguments):
    directory = tmp_path / "pskit-agent-cloud-test"
    directory.mkdir()
    marker = directory / "operator-owned.env"
    marker.write_text("unchanged\n")
    binary = tmp_path / "bin"
    binary.mkdir()
    log = tmp_path / "external-commands.log"
    for command in (
        "docker", "ssh", "scp", "rsync", "git", "node", "npm", "sudo", "systemctl",
        "sleep", "whoami", "hostname", "date", "mkdir", "chmod", "cp", "mv", "rm",
        "curl", "sed", "grep", "cut", "tr", "find", "sha256sum", "dirname", "python",
        "python3", "psql", "bash",
    ):
        stub = binary / command
        stub.write_text(
            '#!/bin/bash\nprintf "%s\\n" "$0 $*" >> "$EXTERNAL_COMMAND_LOG"\nexit 97\n'
        )
        stub.chmod(0o755)
    result = subprocess.run(
        ["/bin/bash", str(ROOT / "deploy_to_cloud.sh"), *arguments],
        cwd=directory,
        env={"PATH": str(binary), "EXTERNAL_COMMAND_LOG": str(log)},
        capture_output=True, text=True, timeout=5, check=False,
    )

    assert result.returncode != 0
    assert "docs/runbooks/DEPLOYMENT.md" in result.stderr
    assert "deprecated" in result.stderr.lower()
    assert not log.exists(), log.read_text() if log.exists() else ""
    assert list(directory.iterdir()) == [marker]
    assert marker.read_text() == "unchanged\n"
