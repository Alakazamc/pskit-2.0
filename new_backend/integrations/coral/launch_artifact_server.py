"""Restart the PSKit-owned MCP without losing the native model environment."""

import argparse
import json
import os
import shlex
import signal
import subprocess
import time
from pathlib import Path


def build_environment(root, python, inherited, *, foldseek=None):
    root = Path(root).resolve()
    environment = dict(inherited)
    for line in (root / ".pskit-mcp.runtime.env").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw = line.removeprefix("export ").split("=", 1)
        values = shlex.split(raw)
        environment[key] = values[0] if values else ""
    paths = [path for path in environment.get("PYTHONPATH", "").split(":") if path]
    if str(root) not in paths:
        paths.append(str(root))
    environment["PYTHONPATH"] = ":".join(paths)
    binaries = [str(Path(python).parent)]
    if foldseek:
        environment["FOLDSEEK_BINARY"] = str(foldseek)
        binaries.append(str(Path(foldseek).parent))
    environment["PATH"] = ":".join([*binaries, environment.get("PATH", os.defpath)])
    workspace = Path(environment.get("PSKIT_MCP_WORKSPACE_ROOT", "pskit_mcp_runs"))
    environment["PSKIT_MCP_WORKSPACE_ROOT"] = str(
        workspace if workspace.is_absolute() else root / workspace
    )
    environment["TMPDIR"] = str(root / ".pskit-mcp-tmp")
    return environment


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider-root", required=True, type=Path)
    parser.add_argument("--python", required=True, type=Path)
    parser.add_argument("--foldseek", type=Path)
    args = parser.parse_args()
    root = args.provider_root.resolve()
    environment = build_environment(root, args.python, os.environ, foldseek=args.foldseek)
    pid_file = root / ".pskit-mcp.pid"
    previous = int(pid_file.read_text())
    process_dir = Path("/proc") / str(previous)
    if b"pskit_mcp.server" not in (process_dir / "cmdline").read_bytes().split(b"\0"):
        raise RuntimeError("PROVIDER_PID_MISMATCH")
    os.kill(previous, signal.SIGTERM)
    for _ in range(100):
        if not process_dir.exists():
            break
        time.sleep(0.1)
    else:
        raise RuntimeError("PROVIDER_DID_NOT_STOP")
    with (root / ".pskit-mcp.log").open("ab") as output:
        process = subprocess.Popen(
            [str(args.python), "-m", "pskit_mcp.server"], cwd=root, env=environment,
            stdout=output, stderr=output, start_new_session=True,
        )
    pid_file.write_text(str(process.pid) + "\n")
    time.sleep(3)
    if process.poll() is not None:
        raise RuntimeError("PROVIDER_RESTART_FAILED")
    print(json.dumps({"provider_pid": process.pid, "native_environment_preserved": True}))


if __name__ == "__main__":
    main()
