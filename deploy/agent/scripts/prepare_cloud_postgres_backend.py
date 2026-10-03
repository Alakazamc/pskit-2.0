"""Stage private PostgreSQL backend configuration without changing live files.

Run once on Aliyun after the candidate virtual key has been generated. The
new files are inert until stack.sh is invoked for the final cutover.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from urllib.parse import quote

BACKEND_IMAGE = "pskit-agent-backend:pg17-20261003-r1"
AGENT_VOLUME = "pskit-agent-cloud-pg17-20261003_agent_data"


def _private(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Required private source file is missing or unsafe")
    if path.stat().st_mode & 0o077:
        raise ValueError("Private source file has unsafe permissions")


def _env(path: Path) -> dict[str, str]:
    _private(path)
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line or line.lstrip().startswith("#"):
            continue
        if "=" not in line:
            raise ValueError("Private source file contains an invalid line")
        key, _, value = line.partition("=")
        if not key or key in values:
            raise ValueError("Private source file has a duplicate or empty key")
        values[key] = value
    return values


def _required(values: dict[str, str], key: str) -> str:
    value = values.get(key, "")
    if not value or "\n" in value or "\r" in value:
        raise ValueError(f"Required private setting is missing: {key}")
    return value


def _write_private(path: Path, values: dict[str, str]) -> None:
    if any("\n" in value or "\r" in value for value in values.values()):
        raise ValueError("Private setting contains a newline")
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600,
    )
    with os.fdopen(descriptor, "w") as output:
        for key, value in values.items():
            output.write(f"{key}={value}\n")


def prepare_backend(root: Path) -> None:
    root = Path(root).resolve()
    agent = root / "deploy/agent"
    llm = root / "infra/litellm"
    targets = (
        agent / "cloud.backend.pg17.env",
        agent / "cloud.env",
        agent / ".env.stack",
    )
    if any(path.exists() or path.is_symlink() for path in targets):
        raise FileExistsError("Staged backend configuration already exists")

    admin = _env(agent / ".env.stack-admin")
    existing_backend = _env(agent / "cloud.backend.env")
    proxy = _env(agent / "cloud.proxy.env")
    existing_cloud = _env(agent / ".env")
    key_file = llm / ".pskit-candidate-virtual-key"
    _private(key_file)
    new_key = key_file.read_text().strip()
    if not new_key.startswith("sk-"):
        raise ValueError("Candidate virtual key is missing or invalid")
    if _required(existing_backend, "RESEARCH_AGENT_COMPUTE_CALLBACK_KEY") != _required(
        proxy, "RESEARCH_AGENT_COMPUTE_CALLBACK_KEY"
    ):
        raise ValueError("Backend and proxy callback keys do not match")

    backend = existing_backend | {
        "RESEARCH_AGENT_DATABASE_URL": (
            "postgresql://pskit_app:"
            f"{quote(_required(admin, 'PSKIT_DB_PASSWORD'), safe='')}@db:5432/postgres"
        ),
        "MODEL_GATEWAY_BASE_URL": "http://gateway:4000/v1",
        "MODEL_GATEWAY_KIND": "litellm",
        "MODEL_GATEWAY_MODEL": "claude-opus-4-8",
        "MODEL_GATEWAY_API_KEY": new_key,
    }
    cloud = existing_cloud | {
        "AGENT_BACKEND_IMAGE": BACKEND_IMAGE,
        "AGENT_BACKEND_ENV_FILE": str(targets[0]),
        "AGENT_AF3_PROXY_KEY_FILE": str(agent / "cloud.proxy.env"),
        "AGENT_PG_DATA_VOLUME": AGENT_VOLUME,
    }
    stack = {
        "STACK_CLOUD_ENV_FILE": str(targets[1]),
        "STACK_BACKEND_ENV_FILE": str(targets[0]),
        "STACK_PROXY_ENV_FILE": str(agent / "cloud.proxy.env"),
    }
    created: list[Path] = []
    try:
        for path, values in zip(targets, (backend, cloud, stack), strict=True):
            _write_private(path, values)
            created.append(path)
    except (OSError, ValueError):
        for path in created:
            path.unlink()
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    prepare_backend(args.root)
    print("Private PostgreSQL backend configuration staged")


if __name__ == "__main__":
    main()
