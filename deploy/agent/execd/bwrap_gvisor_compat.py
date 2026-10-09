#!/usr/bin/python3
"""Run native bubblewrap with the one namespace gVisor already supplies optional."""

from __future__ import annotations

import os
import sys


def main() -> None:
    if os.environ.get("PSKIT_GVISOR_BWRAP_COMPAT") != "1":
        raise SystemExit("PSKit gVisor bwrap compatibility was not explicitly enabled")
    arguments = [
        "--unshare-cgroup-try" if value == "--unshare-cgroup" else value
        for value in sys.argv[1:]
    ]
    os.execv("/usr/bin/bwrap", ["/usr/bin/bwrap", *arguments])


if __name__ == "__main__":
    main()
