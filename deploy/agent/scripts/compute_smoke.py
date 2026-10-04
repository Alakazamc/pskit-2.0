"""Run isolated local compute protocol/CPU/Pi checks; no production data or paid model calls."""

import os
import subprocess
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[3]
    if not os.environ.get("TEST_POSTGRES_DSN"):
        raise SystemExit("TEST_POSTGRES_DSN is required; use the isolated test database, not production")
    tests = [str(path.relative_to(root/"new_backend")) for folder in ("tests/postgres", "tests")
             for path in sorted((root/"new_backend"/folder).glob("test_compute*.py"))]
    raise SystemExit(subprocess.run([str(root/"new_backend/.venv/bin/python"), "-m", "pytest", *tests,
                                    "-q"], cwd=root/"new_backend", check=False).returncode)


if __name__ == "__main__":
    main()
