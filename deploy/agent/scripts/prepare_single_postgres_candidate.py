"""Create private Aliyun candidate configuration from the existing Supabase DB.

Run once on the target host. Refuses to overwrite any existing candidate file.
No credential is printed or included in command-line arguments.
"""

from __future__ import annotations

import argparse
import os
import secrets
from pathlib import Path
from urllib.parse import quote


def _private_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Supabase environment file is missing or unsafe")
    if path.stat().st_mode & 0o077:
        raise ValueError("Supabase environment file has unsafe permissions")


def _supabase_password(path: Path) -> str:
    _private_file(path)
    for line in path.read_text().splitlines():
        if line.startswith("POSTGRES_PASSWORD="):
            value = line.partition("=")[2].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if not value or "\n" in value or "\r" in value:
                break
            return value
    raise ValueError("Supabase POSTGRES_PASSWORD is missing")


def _create_private(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as file:
        file.write(content)


def prepare_candidate(root: Path) -> None:
    root = Path(root)
    password = _supabase_password(root / "infra/supabase/.env")
    admin = root / "deploy/agent/.env.stack-admin"
    gateway = root / "infra/litellm/.env.shared"
    if admin.exists() or admin.is_symlink() or gateway.exists() or gateway.is_symlink():
        raise FileExistsError("Candidate configuration already exists; inspect it before retrying")

    db_password = secrets.token_hex(24)
    app_password = secrets.token_hex(24)
    admin_dsn = f"postgresql://postgres:{quote(password, safe='')}@db:5432/postgres"
    admin_content = (
        f"SHARED_POSTGRES_ADMIN_DSN={admin_dsn}\n"
        f"RESEARCH_AGENT_DATABASE_URL={admin_dsn}\n"
        f"LITELLM_DB_PASSWORD={db_password}\n"
        f"PSKIT_DB_PASSWORD={app_password}\n"
    )
    gateway_content = (
        "LITELLM_BIND_IP=10.9.8.1\n"
        "LITELLM_PUBLIC_PORT=4001\n"
        "SUPABASE_DOCKER_NETWORK=pskit-agent-supabase_default\n"
        f"LITELLM_DB_PASSWORD={db_password}\n"
        f"LITELLM_MASTER_KEY=sk-{secrets.token_hex(24)}\n"
        f"LITELLM_SALT_KEY=sk-{secrets.token_hex(24)}\n"
    )
    created = []
    try:
        _create_private(admin, admin_content)
        created.append(admin)
        _create_private(gateway, gateway_content)
        created.append(gateway)
    except OSError:
        for path in created:
            path.unlink()
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    arguments = parser.parse_args()
    prepare_candidate(arguments.root)
    print("Private candidate configuration created")


if __name__ == "__main__":
    main()
