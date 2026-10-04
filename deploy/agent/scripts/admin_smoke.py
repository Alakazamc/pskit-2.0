"""Run management acceptance with synthetic identities and disposable local schemas.

This exercises the real application HTTP boundary and PostgreSQL stores without
using production identities, changing a deployed release, or invoking inference.
"""

import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse


def acceptance_command(environ):
    """Return the acceptance command after requiring an explicit local database."""
    dsn = environ.get("TEST_POSTGRES_DSN", "")
    target = urlparse(dsn)
    if (
        target.scheme not in {"postgresql", "postgres"}
        or target.hostname
        not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }
        or not target.path.strip("/")
    ):
        raise ValueError("TEST_POSTGRES_DSN must point to an isolated local test database")
    directory = Path(__file__).resolve().parents[3] / "new_backend"
    paths = ["tests/test_management_wiring.py"]
    for folder in ("tests", "tests/postgres"):
        paths.extend(
            str(path.relative_to(directory))
            for path in sorted((directory / folder).glob("test_admin*.py"))
        )
    authorization = directory / "tests/test_model_authorization.py"
    if authorization.is_file():
        paths.append(str(authorization.relative_to(directory)))
    return [str(directory / ".venv/bin/python"), "-m", "pytest", *paths, "-q"], directory


def main():
    try:
        command, directory = acceptance_command(os.environ)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    raise SystemExit(subprocess.run(command, cwd=directory, check=False).returncode)


if __name__ == "__main__":
    main()
