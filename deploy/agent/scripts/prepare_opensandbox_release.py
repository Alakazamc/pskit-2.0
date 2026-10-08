#!/usr/bin/env python3
"""Prepare or qualify an immutable OpenSandbox Staging release manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import subprocess
import tempfile
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[3]
AGENT = ROOT / "deploy/agent"
DIGEST = re.compile(r"^(?:[A-Za-z0-9][A-Za-z0-9._:/-]*@)?sha256:[a-f0-9]{64}$")
OPEN_SANDBOX_VERSION = "1.1.0"
OPEN_SANDBOX_COMMIT = "b1a29cf93a823a95913f7943010febb3f29de05c"


class Refusal(RuntimeError):
    """A release does not satisfy an immutable deployment invariant."""


def _env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise Refusal(f"Invalid env syntax at line {number}")
        key, value = line.split("=", 1)
        if key in values:
            raise Refusal(f"Duplicate env key: {key}")
        values[key] = value
    return values


def _write_env(path: Path, values: dict[str, str]) -> None:
    content = "".join(f"{key}={value}\n" for key, value in values.items())
    _atomic(path, content.encode(), 0o600)


def _atomic(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _require_digest(value: str, label: str) -> str:
    if DIGEST.fullmatch(value) is None:
        raise Refusal(f"{label} must be an immutable sha256 image reference")
    return value


def _image_id(value: str) -> str:
    completed = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", value],
        capture_output=True,
        text=True,
        check=False,
    )
    image_id = completed.stdout.strip()
    if completed.returncode or re.fullmatch(r"sha256:[a-f0-9]{64}", image_id) is None:
        raise Refusal("A release image is unavailable locally")
    return image_id


def _tree_hash(root: Path) -> str:
    if not (root / "index.html").is_file():
        raise Refusal("Frontend dist lacks index.html")
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise Refusal("Frontend dist contains a symlink")
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode() + b"\0")
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _runsc_version(expected: str) -> str:
    info = subprocess.run(
        ["docker", "info", "--format", "{{json .Runtimes}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if info.returncode:
        raise Refusal("Docker runtime inventory is unavailable")
    runtimes = json.loads(info.stdout)
    runtime = runtimes.get("runsc") or {}
    executable = runtime.get("path") or runtime.get("Path")
    if not executable:
        raise Refusal("The registered runsc runtime is unavailable")
    completed = subprocess.run(
        [executable, "--version"], capture_output=True, text=True, check=False
    )
    report = (completed.stdout + completed.stderr).strip()
    if completed.returncode or expected not in report:
        raise Refusal("The registered runsc version does not match the release")
    return expected


def _render_toml(
    path: Path, *, execd: str, egress: str, runtime_network: str
) -> None:
    template = (AGENT / "opensandbox.toml.example").read_text(encoding="utf-8")
    content = template.replace(
        "opensandbox/execd@sha256:replace-with-64-lowercase-hex", execd
    ).replace(
        "opensandbox/egress@sha256:replace-with-64-lowercase-hex", egress
    ).replace("replace-with-private-runtime-network", runtime_network)
    if "replace-with" in content:
        raise Refusal("OpenSandbox TOML still contains a placeholder")
    _atomic(path, content.encode(), 0o600)


def _compose_hash(config_dir: Path, cloud: dict[str, str]) -> str:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG"}
    }
    environment.update(cloud)
    command = ["docker", "compose", "--env-file", str(config_dir / "cloud.env")]
    for file in (
        "compose.yaml",
        "compose.cloud.yaml",
        "compose.postgres.yaml",
        "compose.staging.yaml",
        "compose.opensandbox.yaml",
        "compose.opensandbox.staging.yaml",
    ):
        command.extend(("-f", str(AGENT / file)))
    command.extend(("config", "--format", "json"))
    completed = subprocess.run(
        command, capture_output=True, text=True, env=environment, check=False
    )
    if completed.returncode:
        raise Refusal("OpenSandbox Compose rendering failed")
    rendered = json.loads(completed.stdout)
    server = rendered.get("services", {}).get("opensandbox-server", {})
    if server.get("ports") or server.get("expose"):
        raise Refusal("OpenSandbox Server publishes a host port")
    canonical = json.dumps(rendered, sort_keys=True, separators=(",", ":")).encode()
    return _sha(canonical)


def _capability_hash(report_path: Path | None) -> tuple[str, str]:
    if report_path is None:
        return "prepared", ""
    report = json.loads(report_path.read_text(encoding="utf-8"))
    values = report.get("live_probe", report)
    required = {
        "runtime": "runsc",
        "command_events": True,
        "cancellation": True,
        "persistent_volume": True,
        "session_mount_namespace": True,
    }
    if any(values.get(key) != expected for key, expected in required.items()):
        raise Refusal("Capability report does not prove the required isolation")
    contract = {
        "cancellation": True,
        "command_events": True,
        "command_execution": True,
        "file_access": True,
        "persistent_volume": True,
        "provider": "opensandbox",
        "runtime": "runsc",
        "session_mount_namespace": True,
    }
    return "qualified", _sha(json.dumps(
        contract, sort_keys=True, separators=(",", ":")
    ).encode())


def prepare(args: argparse.Namespace) -> dict[str, object]:
    config_dir = args.config_dir.resolve(strict=True)
    if config_dir.is_symlink() or config_dir.stat().st_mode & 0o077:
        raise Refusal("Staging config directory must be private and not a symlink")
    cloud_path = config_dir / "cloud.env"
    cloud = _env(cloud_path)
    backend = _require_digest(args.backend_image, "backend image")
    server = _require_digest(args.server_image, "server image")
    sandbox = _require_digest(args.sandbox_image, "sandbox image")
    execd = _require_digest(args.execd_image, "execd image")
    egress = _require_digest(args.egress_image, "egress image")
    if not args.volume_driver or args.volume_driver == "local":
        raise Refusal("A non-local quota volume driver is required")

    namespace = "pskit-staging"
    runtime_network = "pskit-agent-staging-opensandbox-runtime"
    state_volume = "pskit-agent-staging_opensandbox_state"
    rollout_dir = config_dir / "opensandbox-rollout"
    rollout_dir.mkdir(mode=0o700, exist_ok=True)
    policy_path = rollout_dir / "policy.json"
    if not policy_path.exists():
        _atomic(policy_path, json.dumps({
            "enabled": True,
            "commands_enabled": False,
            "user_allowlist": ["*"],
            "capability_hash": "",
        }, indent=2).encode() + b"\n", 0o600)
    api_key = cloud.get("OPENSANDBOX_API_KEY") or secrets.token_urlsafe(48)
    cloud.update({
        "AGENT_BACKEND_IMAGE": backend,
        "OPENSANDBOX_SERVER_IMAGE": server,
        "OPENSANDBOX_SANDBOX_IMAGE": sandbox,
        "OPENSANDBOX_CONFIG_FILE": str(config_dir / "opensandbox.private.toml"),
        "OPENSANDBOX_RUNTIME_NETWORK": runtime_network,
        "OPENSANDBOX_STATE_VOLUME": state_volume,
        "OPENSANDBOX_VOLUME_DRIVER": args.volume_driver,
        "OPENSANDBOX_VOLUME_SIZE": args.volume_size,
        "OPENSANDBOX_CPU_MILLICORES": str(args.cpu_millicores),
        "OPENSANDBOX_MEMORY_BYTES": str(args.memory_bytes),
        "OPENSANDBOX_PID_LIMIT": str(args.pid_limit),
        "OPENSANDBOX_DISK_BYTES": str(args.disk_bytes),
        "OPENSANDBOX_INODE_LIMIT": str(args.inode_limit),
        "OPENSANDBOX_API_KEY": api_key,
        "OPENSANDBOX_NAMESPACE": namespace,
        "OPENSANDBOX_ROLLOUT_DIR": str(rollout_dir),
    })
    _write_env(cloud_path, cloud)
    base_manifest_path = config_dir / "manifest.json"
    base_manifest = json.loads(base_manifest_path.read_text(encoding="utf-8"))
    base_manifest.update({
        "backend_image": backend,
        "backend_image_id": _image_id(backend),
        "frontend_dist": str(args.frontend_dist.resolve(strict=True)),
        "frontend_dist_sha256": _tree_hash(args.frontend_dist.resolve(strict=True)),
    })
    _atomic(base_manifest_path, (
        json.dumps(base_manifest, sort_keys=True) + "\n"
    ).encode(), 0o600)
    _render_toml(
        config_dir / "opensandbox.private.toml",
        execd=execd,
        egress=egress,
        runtime_network=runtime_network,
    )
    rendered_toml = tomllib.loads(
        (config_dir / "opensandbox.private.toml").read_text(encoding="utf-8")
    )
    if rendered_toml["secure_runtime"] != {
        "type": "gvisor", "docker_runtime": "runsc"
    }:
        raise Refusal("Rendered OpenSandbox runtime is not gVisor/runsc")

    status, capability_hash = _capability_hash(args.capability_report)
    if status == "qualified":
        _atomic(policy_path, json.dumps({
            "enabled": True,
            "commands_enabled": False,
            "user_allowlist": ["*"],
            "capability_hash": capability_hash,
        }, indent=2).encode() + b"\n", 0o600)
    manifest: dict[str, object] = {
        "schema": 1,
        "status": status,
        "backend": {"reference": backend, "image_id": _image_id(backend)},
        "opensandbox": {
            "sdk_version": "1.1.0",
            "server_version": OPEN_SANDBOX_VERSION,
            "server_commit": OPEN_SANDBOX_COMMIT,
            "server": {"reference": server, "image_id": _image_id(server)},
            "sandbox": {"reference": sandbox, "image_id": _image_id(sandbox)},
            "execd": execd,
            "egress": egress,
            "runsc_version": _runsc_version(args.runsc_version),
            "namespace": namespace,
            "runtime_network": runtime_network,
            "state_volume": state_volume,
            "api_key_sha256": _sha(api_key.encode()),
            "capability_hash": capability_hash,
        },
        "frontend_dist_sha256": base_manifest["frontend_dist_sha256"],
        "compose_render_sha256": _compose_hash(config_dir, cloud),
    }
    _atomic(config_dir / "opensandbox-release.json", (
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    ).encode(), 0o600)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--backend-image", required=True)
    parser.add_argument("--server-image", required=True)
    parser.add_argument("--sandbox-image", required=True)
    parser.add_argument("--execd-image", required=True)
    parser.add_argument("--egress-image", required=True)
    parser.add_argument("--frontend-dist", type=Path, required=True)
    parser.add_argument("--runsc-version", required=True)
    parser.add_argument("--volume-driver", required=True)
    parser.add_argument("--volume-size", default="10Gi")
    parser.add_argument("--cpu-millicores", type=int, default=1000)
    parser.add_argument("--memory-bytes", type=int, default=1_073_741_824)
    parser.add_argument("--pid-limit", type=int, default=256)
    parser.add_argument("--disk-bytes", type=int, default=10_737_418_240)
    parser.add_argument("--inode-limit", type=int, default=100_000)
    parser.add_argument("--capability-report", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args), indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError, Refusal) as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
