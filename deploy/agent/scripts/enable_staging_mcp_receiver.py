#!/usr/bin/env python3
"""Add the generic MCP receiver to an existing private Staging configuration."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import tempfile
from pathlib import Path

from deploy.agent.scripts.pin_staging_release import _atomic_write
from deploy.agent.scripts.prepare_staging import _secret
from deploy.agent.scripts.staging_preflight import _private

SERVICE_ID = "coral-mcp"
WORKER_ID = "coral-mcp-staging-1"
VOLUME = "pskit-agent-staging_mcp_receiver_data"


def _env(content: bytes) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in content.decode().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in values:
            raise ValueError(f"Duplicate private setting: {key}")
        values[key] = value
    return values


def _set(content: bytes, additions: dict[str, str]) -> bytes:
    lines = content.decode().splitlines()
    found: set[str] = set()
    for index, line in enumerate(lines):
        key = line.split("=", 1)[0] if "=" in line and not line.startswith("#") else ""
        if key not in additions:
            continue
        if key in found:
            raise ValueError(f"Duplicate private setting: {key}")
        found.add(key)
        lines[index] = f"{key}={additions[key]}"
    lines.extend(f"{key}={value}" for key, value in additions.items() if key not in found)
    return ("\n".join(lines).rstrip("\n") + "\n").encode()


def _compute_key(receiver: bytes | None, backend_base: bytes) -> str:
    existing_mapping = _env(backend_base).get("RESEARCH_AGENT_COMPUTE_SERVICE_KEYS_JSON")
    mapped = None
    if existing_mapping:
        try:
            parsed = json.loads(existing_mapping)
        except json.JSONDecodeError as exc:
            raise ValueError("Existing compute service key mapping is invalid") from exc
        if not isinstance(parsed, dict) or set(parsed) != {SERVICE_ID}:
            raise ValueError("Existing compute service key mapping conflicts with Staging")
        mapped = parsed[SERVICE_ID]
    receiver_key = _env(receiver).get("PSKIT_COMPUTE_SERVICE_KEY") if receiver else None
    if mapped and receiver_key and mapped != receiver_key:
        raise ValueError("Existing MCP receiver key conflicts with backend admission")
    key = receiver_key or mapped or _secret(32)
    if not isinstance(key, str) or len(key) < 32:
        raise ValueError("Existing MCP receiver key is unsafe")
    return key


def enable_staging_mcp_receiver(config_dir: Path) -> Path | None:
    """Upgrade one stopped Staging config atomically; return its backup directory."""
    root = Path(config_dir).absolute()
    if root.is_symlink() or not root.is_dir() or root.stat().st_mode & 0o077:
        raise ValueError("Private staging directory is missing or unsafe")
    required = {name: root / name for name in ("cloud.env", "backend.env.base")}
    if (root / "backend.env").exists():
        required["backend.env"] = root / "backend.env"
    for path in required.values():
        _private(path)
    receiver_path = root / "mcp.receiver.env"
    receiver_content = None
    if receiver_path.exists() or receiver_path.is_symlink():
        _private(receiver_path)
        receiver_content = receiver_path.read_bytes()

    lock_fd = os.open(root / ".mcp-receiver.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, "wb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        originals = {name: path.read_bytes() for name, path in required.items()}
        key = _compute_key(receiver_content, originals["backend.env.base"])
        mapping = json.dumps({SERVICE_ID: key}, separators=(",", ":"))
        desired = {
            "cloud.env": _set(originals["cloud.env"], {
                "AGENT_MCP_RECEIVER_ENV_FILE": str(receiver_path),
                "AGENT_MCP_RECEIVER_DATA_VOLUME": VOLUME,
                "AGENT_MCP_SERVICE_ID": SERVICE_ID,
                "AGENT_MCP_WORKER_ID": WORKER_ID,
            }),
            "backend.env.base": _set(originals["backend.env.base"], {
                "RESEARCH_AGENT_COMPUTE_SERVICE_KEYS_JSON": mapping,
                "RESEARCH_AGENT_ADMIN_MCP_NETWORK_ZONES_JSON": (
                    '{"wireguard-private":["172.31.226.126/32"]}'
                ),
                "RESEARCH_AGENT_MCP_TIMEOUT_SECONDS": "900",
            }),
        }
        if "backend.env" in originals:
            backend_values = _env(originals["backend.env"])
            model_key = backend_values.pop("MODEL_GATEWAY_API_KEY", None)
            if not model_key or backend_values != _env(originals["backend.env.base"]):
                raise ValueError("Existing staging backend configuration differs")
            desired["backend.env"] = (
                desired["backend.env.base"].rstrip(b"\n")
                + f"\nMODEL_GATEWAY_API_KEY={model_key}\n".encode()
            )
        desired_receiver = _set(receiver_content or b"", {
            "PSKIT_COMPUTE_SERVICE_KEY": key,
            "PSKIT_MCP_ENDPOINT_OVERRIDES_JSON": "{}",
            "PSKIT_MCP_CREDENTIAL_REFS_JSON": "{}",
        })
        if (all(originals[name] == value for name, value in desired.items())
                and receiver_content == desired_receiver):
            return None

        backups = root / "release-backups"
        backups.mkdir(mode=0o700, exist_ok=True)
        if backups.is_symlink() or backups.stat().st_mode & 0o077:
            raise ValueError("Staging backup directory is unsafe")
        backup = Path(tempfile.mkdtemp(prefix="mcp-receiver-", dir=backups))
        backup.chmod(0o700)
        for name, content in originals.items():
            _atomic_write(backup / name, content)
        if receiver_content is not None:
            _atomic_write(backup / "mcp.receiver.env", receiver_content)
        written: list[Path] = []
        try:
            for name, content in desired.items():
                _atomic_write(required[name], content)
                written.append(required[name])
            _atomic_write(receiver_path, desired_receiver)
            receiver_path.chmod(0o600)
        except BaseException:
            for name, content in originals.items():
                _atomic_write(required[name], content)
            if receiver_content is None:
                receiver_path.unlink(missing_ok=True)
            else:
                _atomic_write(receiver_path, receiver_content)
            shutil.rmtree(backup, ignore_errors=True)
            raise
        return backup


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, required=True)
    backup = enable_staging_mcp_receiver(parser.parse_args().config_dir)
    print("Staging MCP receiver already configured" if backup is None
          else f"Staging MCP receiver configured; previous files: {backup}")


if __name__ == "__main__":
    main()
