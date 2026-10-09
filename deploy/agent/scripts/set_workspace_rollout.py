#!/usr/bin/env python3
"""Atomically update the server-owned workspace rollout policy."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path


def write_policy(
    directory: Path,
    *,
    enabled: bool,
    commands_enabled: bool,
    users: list[str],
    capability_hash: str,
) -> None:
    root = directory.resolve(strict=True)
    mode = root.stat().st_mode & 0o777
    if root.is_symlink() or not root.is_dir() or mode != 0o755:
        raise ValueError("Rollout directory must be mode 0755 and not a symlink")
    if commands_enabled and (
        not enabled or re.fullmatch(r"[a-f0-9]{64}", capability_hash) is None
    ):
        raise ValueError("Commands require enabled access and a capability hash")
    if len(set(users)) != len(users) or any(not item for item in users):
        raise ValueError("Rollout users must be unique non-empty IDs")
    payload = {
        "enabled": enabled,
        "commands_enabled": commands_enabled,
        "user_allowlist": users,
        "capability_hash": capability_hash,
    }
    descriptor, temporary = tempfile.mkstemp(prefix=".policy.", dir=root)
    try:
        # The backend receives this directory as a read-only bind mount and
        # runs as UID 10001.  The policy contains no credential, so make it
        # readable while keeping host writes restricted to the owner.
        os.fchmod(descriptor, 0o644)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, root / "policy.json")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--enabled", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument(
        "--commands-enabled", action=argparse.BooleanOptionalAction, default=False
    )
    parser.add_argument("--user", action="append", default=[])
    parser.add_argument("--capability-hash", default="")
    args = parser.parse_args()
    try:
        write_policy(
            args.directory,
            enabled=args.enabled,
            commands_enabled=args.commands_enabled,
            users=args.user,
            capability_hash=args.capability_hash,
        )
        print("Workspace rollout policy updated")
        return 0
    except (OSError, ValueError) as exc:
        print(f"Workspace rollout policy refused: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
