#!/usr/bin/env python3
"""Fail-closed OpenSandbox/gVisor deployment preflight.

The report contains booleans and public identifiers only. Secret values are
never included in stdout, stderr, Docker command arguments, or exceptions.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Any

import tomllib

DIGEST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[a-f0-9]{64}$")


class Refusal(RuntimeError):
    """A deployment invariant is absent or contradicted."""


def _load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise Refusal(f"invalid env syntax at line {number}")
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def _run(command: list[str], *, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        command, check=False, capture_output=True, text=True, env=env
    )
    if completed.returncode:
        detail = command[1] if len(command) > 1 else ""
        raise Refusal(f"command failed: {command[0]} {detail}")
    return completed.stdout


def _require_digest(value: str, label: str) -> str:
    if DIGEST_RE.fullmatch(value) is None:
        raise Refusal(f"{label} must be an immutable sha256 image digest")
    return value


def _compose_config(files: list[Path], env_path: Path) -> dict[str, Any]:
    command = ["docker", "compose", "--env-file", str(env_path)]
    for file in files:
        command.extend(("-f", str(file)))
    command.extend(("config", "--format", "json"))
    return json.loads(_run(command))


def _mount_source(mount: Any) -> str:
    if isinstance(mount, str):
        return mount.split(":", 1)[0]
    if isinstance(mount, dict):
        return str(mount.get("source", ""))
    return ""


def _static_preflight(args: argparse.Namespace) -> dict[str, Any]:
    values = _load_env(args.env_file)
    required_env = (
        "OPENSANDBOX_SERVER_IMAGE",
        "OPENSANDBOX_SANDBOX_IMAGE",
        "OPENSANDBOX_RUNTIME_NETWORK",
        "OPENSANDBOX_STATE_VOLUME",
        "OPENSANDBOX_VOLUME_DRIVER",
        "OPENSANDBOX_API_KEY",
    )
    if any(not values.get(key) for key in required_env):
        raise Refusal("missing required OpenSandbox environment entries")
    if len(values["OPENSANDBOX_API_KEY"]) < 32:
        raise Refusal("OpenSandbox API key is shorter than 32 characters")
    _require_digest(values["OPENSANDBOX_SERVER_IMAGE"], "server image")
    _require_digest(values["OPENSANDBOX_SANDBOX_IMAGE"], "sandbox image")
    if values["OPENSANDBOX_VOLUME_DRIVER"] in {"", "local"}:
        raise Refusal("a quota-capable non-local volume driver is required")

    config = tomllib.loads(args.config.read_text(encoding="utf-8"))
    _require_digest(str(config["runtime"]["execd_image"]), "execd image")
    _require_digest(str(config["egress"]["image"]), "egress image")
    if config.get("secure_runtime") != {"type": "gvisor", "docker_runtime": "runsc"}:
        raise Refusal("secure_runtime must be exactly gvisor/runsc")
    if config["docker"].get("network_mode") != values["OPENSANDBOX_RUNTIME_NETWORK"]:
        raise Refusal("runtime network differs between config and manifest")
    if not config["storage"].get("allowed_host_paths"):
        raise Refusal("host bind allowlist must not be empty")

    runtimes = json.loads(
        _run(["docker", "info", "--format", "{{json .Runtimes}}"])[0:]
    )
    if "runsc" not in runtimes:
        raise Refusal("Docker has not registered the runsc runtime")
    probe_name = f"pskit-quota-preflight-{secrets.token_hex(6)}"
    try:
        _run(
            [
                "docker",
                "volume",
                "create",
                "--driver",
                values["OPENSANDBOX_VOLUME_DRIVER"],
                "--opt",
                "size=64MiB",
                "--opt",
                "inodes=1024",
                probe_name,
            ]
        )
        inspected = json.loads(_run(["docker", "volume", "inspect", probe_name]))
        if (
            not inspected
            or inspected[0].get("Driver") != values["OPENSANDBOX_VOLUME_DRIVER"]
            or inspected[0].get("Options") != {"size": "64MiB", "inodes": "1024"}
        ):
            raise Refusal("quota volume driver did not preserve hard-limit options")
        backend_image = values.get("AGENT_BACKEND_IMAGE", "")
        if not backend_image:
            raise Refusal("backend image is required for the quota volume probe")
        probe_script = (
            "import json,os,pathlib;"
            "v=os.statvfs('/probe');"
            "pathlib.Path('/probe/persist').write_text('bounded');"
            "print(json.dumps({'bytes':v.f_blocks*v.f_frsize,'inodes':v.f_files}))"
        )
        filesystem = json.loads(
            _run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--read-only",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    # A new ext4 root is owned by root until the trusted
                    # OpenSandbox bootstrap creates the per-user workspace.
                    # This no-network, cap-drop probe models only that volume
                    # initialization step; user commands are verified by the
                    # separate runsc/bwrap Staging smoke.
                    "--user",
                    "0:0",
                    "-v",
                    f"{probe_name}:/probe",
                    "--entrypoint",
                    "python",
                    backend_image,
                    "-c",
                    probe_script,
                ]
            )
        )
        if not 0 < int(filesystem.get("bytes", 0)) <= 64 * 1024 * 1024:
            raise Refusal("quota volume byte limit was not enforced")
        if not 0 < int(filesystem.get("inodes", 0)) <= 1024:
            raise Refusal("quota volume inode limit was not enforced")
        persisted = _run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--user",
                "0:0",
                "-v",
                f"{probe_name}:/probe:ro",
                "--entrypoint",
                "python",
                backend_image,
                "-c",
                "from pathlib import Path; print(Path('/probe/persist').read_text())",
            ]
        ).strip()
        if persisted != "bounded":
            raise Refusal("quota volume did not persist across mounts")
    finally:
        subprocess.run(
            ["docker", "volume", "rm", probe_name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    rendered = _compose_config(args.compose_file, args.env_file)
    services = rendered.get("services", {})
    server = services.get("opensandbox-server")
    backend = services.get("backend")
    if not isinstance(server, dict) or not isinstance(backend, dict):
        raise Refusal("rendered Compose is missing backend or OpenSandbox server")
    if server.get("ports") or server.get("expose"):
        raise Refusal("OpenSandbox server must not publish or expose host ports")
    mounts = server.get("volumes", [])
    if sum(_mount_source(item) == "/var/run/docker.sock" for item in mounts) != 1:
        raise Refusal("only the OpenSandbox server must mount the Docker socket")
    for service_name, service in services.items():
        if service_name != "opensandbox-server" and any(
            _mount_source(item) == "/var/run/docker.sock"
            for item in service.get("volumes", [])
        ):
            raise Refusal("another service mounts the Docker socket")
    runtime_network = rendered.get("networks", {}).get("opensandbox_runtime", {})
    if runtime_network.get("internal") is not True:
        raise Refusal("sandbox runtime network must be Docker-internal")
    if "opensandbox_runtime" in backend.get("networks", {}):
        raise Refusal("backend must not join the sandbox runtime network")

    return {
        "status": "static-ready",
        "server_image_pinned": True,
        "sandbox_image_pinned": True,
        "execd_image_pinned": True,
        "egress_image_pinned": True,
        "runsc_registered": True,
        "quota_driver_enabled": True,
        "private_control_plane": True,
        "internal_runtime_network": True,
        "docker_socket_scope": "opensandbox-server-only",
    }


async def _provider_probe() -> dict[str, Any]:
    from app.adapters.live.opensandbox_workspace import OpenSandboxWorkspaceProvider
    from app.config import Settings

    settings = Settings.from_env()
    settings.validate_workspace_config()
    provider = OpenSandboxWorkspaceProvider(settings, None)  # type: ignore[arg-type]
    capabilities = await provider.probe()
    if not capabilities.command_execution or capabilities.runtime != "runsc":
        raise Refusal("live provider probe did not prove command isolation")
    return {
        "status": "live-ready",
        "runtime": capabilities.runtime,
        "uid": capabilities.uid,
        "gid": capabilities.gid,
        "command_events": capabilities.command_events,
        "cancellation": capabilities.cancellation,
        "metrics": capabilities.metrics,
        "persistent_volume": capabilities.persistent_volume,
        "session_mount_namespace": capabilities.session_mount_namespace,
        "diagnostics": list(capabilities.diagnostics),
    }


def _live_probe(args: argparse.Namespace, static: dict[str, Any]) -> dict[str, Any]:
    values = _load_env(args.env_file)
    backend_image = values.get("AGENT_BACKEND_IMAGE", "")
    if not backend_image:
        raise Refusal("AGENT_BACKEND_IMAGE is required for the live probe")
    rendered = _compose_config(args.compose_file, args.env_file)
    control_network = rendered.get("networks", {}).get("app", {}).get("name")
    if not control_network:
        raise Refusal("rendered Compose has no named control network")
    backend_env_path = Path(values.get("AGENT_BACKEND_ENV_FILE", ""))
    if not backend_env_path.is_absolute():
        backend_env_path = (args.env_file.parent / backend_env_path).resolve()
    if not backend_env_path.is_file():
        raise Refusal("backend private environment file is unavailable")

    workspace_entries = {
        # The mounted probe script lives under /tmp, so Python otherwise drops
        # the image WORKDIR from sys.path and cannot import the packaged app.
        "PYTHONPATH": "/app",
        "RESEARCH_AGENT_WORKSPACE_PROVIDER": "opensandbox",
        "RESEARCH_AGENT_WORKSPACE_SERVER_URL": "http://opensandbox-server:8090",
        "RESEARCH_AGENT_WORKSPACE_API_KEY": values["OPENSANDBOX_API_KEY"],
        "RESEARCH_AGENT_WORKSPACE_IMAGE_DIGEST": values["OPENSANDBOX_SANDBOX_IMAGE"],
        "RESEARCH_AGENT_WORKSPACE_NAMESPACE": values.get(
            "OPENSANDBOX_NAMESPACE", "pskit"
        ),
        "RESEARCH_AGENT_WORKSPACE_CPU_MILLICORES": values.get(
            "OPENSANDBOX_CPU_MILLICORES", "1000"
        ),
        "RESEARCH_AGENT_WORKSPACE_MEMORY_BYTES": values.get(
            "OPENSANDBOX_MEMORY_BYTES", "1073741824"
        ),
        "RESEARCH_AGENT_WORKSPACE_PID_LIMIT": values.get(
            "OPENSANDBOX_PID_LIMIT", "256"
        ),
        "RESEARCH_AGENT_WORKSPACE_DISK_BYTES": values.get(
            "OPENSANDBOX_DISK_BYTES", "10737418240"
        ),
        "RESEARCH_AGENT_WORKSPACE_INODE_LIMIT": values.get(
            "OPENSANDBOX_INODE_LIMIT", "100000"
        ),
    }
    child_env = os.environ.copy()
    child_env.update(workspace_entries)
    command = [
        "docker",
        "run",
        "--rm",
        "--network",
        str(control_network),
        "--env-file",
        str(backend_env_path),
    ]
    for key in workspace_entries:
        command.extend(("-e", key))
    command.extend(
        (
            "-v",
            f"{Path(__file__).resolve()}:/tmp/opensandbox_preflight.py:ro",
            "--entrypoint",
            "python",
            backend_image,
            "/tmp/opensandbox_preflight.py",
            "--provider-only",
        )
    )
    live = json.loads(_run(command, env=child_env))
    return {**static, "status": "ready", "live_probe": live}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--compose-file", type=Path, action="append", default=[])
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--provider-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        if args.provider_only:
            print(json.dumps(asyncio.run(_provider_probe()), sort_keys=True))
            return 0
        if not args.env_file or not args.config or not args.compose_file:
            raise Refusal("env, config, and Compose files are required")
        report = _static_preflight(args)
        if args.live:
            report = _live_probe(args, report)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    except (Refusal, KeyError, ValueError, json.JSONDecodeError, OSError) as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
