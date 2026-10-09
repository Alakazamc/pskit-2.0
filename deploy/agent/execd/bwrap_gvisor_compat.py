#!/usr/bin/python3
"""Run native bubblewrap without the cgroup namespace gVisor already supplies."""

from __future__ import annotations

import os
import sys


def compatible_arguments(arguments: list[str]) -> list[str]:
    """Expand bwrap's aggregate namespace flag without requesting cgroupns."""
    unshare_all = "--unshare-all" in arguments
    share_net = "--share-net" in arguments
    compatible: list[str] = []
    for value in arguments:
        if value in {"--unshare-cgroup", "--unshare-cgroup-try"}:
            continue
        if value == "--unshare-all":
            compatible.extend(("--unshare-ipc", "--unshare-pid", "--unshare-uts"))
            if not share_net:
                compatible.append("--unshare-net")
            continue
        if value == "--share-net" and unshare_all:
            continue
        compatible.append(value)
    return compatible


def main() -> None:
    if os.environ.get("PSKIT_GVISOR_BWRAP_COMPAT") != "1":
        raise SystemExit("PSKit gVisor bwrap compatibility was not explicitly enabled")
    arguments = compatible_arguments(sys.argv[1:])
    os.execv("/usr/bin/bwrap", ["/usr/bin/bwrap", *arguments])


if __name__ == "__main__":
    main()
