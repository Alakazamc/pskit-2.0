"""Run new-stack checks in a disposable source tree with no live configuration."""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCOPES = ("apps/api", "apps/web", "deploy/agent", "infra", "contracts", "scripts",
          ".github", "deploy_to_cloud.sh", "skills/pskit-cloud-deploy")
SAFE_ENV = ("PATH", "LANG", "LC_ALL", "TZ", "SYSTEMROOT", "WINDIR", "CI")


def isolated_env(directory: Path, source: Path) -> dict[str, str]:
    """Keep process tooling, supply deterministic mock config, isolate all writes."""
    home = directory / "home"
    home.mkdir(parents=True, exist_ok=True)
    return {
        **{key: os.environ[key] for key in SAFE_ENV if key in os.environ},
        "HOME": str(home), "TMPDIR": str(directory),
        "XDG_CACHE_HOME": str(directory / "cache"),
        "PYTHONPYCACHEPREFIX": str(directory / "pycache"),
        "PYTHONPATH": os.pathsep.join(map(str, (source, source / "apps/api",
                                               source / "scripts"))),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "RESEARCH_AGENT_MODE": "mock", "RESEARCH_AGENT_RUNTIME": "mock",
        "RESEARCH_AGENT_AF3_EXECUTOR": "mock", "RESEARCH_AGENT_MCP_EXECUTOR": "mock",
        "RESEARCH_AGENT_DB_PATH": str(directory / "agent.sqlite3"),
        "RESEARCH_AGENT_PI_SESSION_DIR": str(directory / "pi-sessions"),
        "VITE_AUTH_MODE": "demo", "VITE_API_BASE_URL": "/api/v1",
        "VITE_TURNSTILE_SITE_KEY": "", "DEV_API_PROXY_TARGET": "http://127.0.0.1:18080",
        "NPM_CONFIG_USERCONFIG": str(home / ".npmrc"),
    }


def snapshot(destination: Path, root: Path = ROOT) -> None:
    """Copy only versioned/nonignored source paths, never datasets or private files."""
    listing = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--",
         *SCOPES], cwd=root, check=True, capture_output=True, timeout=30,
    ).stdout.decode().split("\0")
    for name in set(listing) - {""}:
        relative = Path(name)
        if any(part in {"node_modules", ".venv", "__pycache__", "data", "dist"}
               for part in relative.parts):
            continue
        if ((relative.name.startswith(".env") or relative.name.endswith(".env"))
                and not relative.name.endswith(".example")):
            continue
        source = root / relative
        if not source.is_file():
            continue
        if source.is_symlink() or source.stat().st_size > 10 * 1024 * 1024:
            raise RuntimeError(f"Refusing unexpected source path: {name}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        shutil.copymode(source, target)


def link_dependencies(source: Path, relative: str) -> None:
    dependencies = ROOT / relative / "node_modules"
    if not dependencies.is_dir():
        raise RuntimeError(f"Run npm ci in {relative} before these checks")
    # Reuse packages, but keep Vite/Vitest cache and bundled config writes in /tmp.
    target = source / relative / "node_modules"
    target.mkdir(parents=True)
    for dependency in dependencies.iterdir():
        if dependency.name.startswith(".") and dependency.name != ".bin":
            continue
        (target / dependency.name).symlink_to(dependency, target_is_directory=dependency.is_dir())


def run(command: list[str], cwd: Path, env: dict[str, str], timeout: float,
        log: Path | None = None) -> None:
    """Bound the complete subprocess group, including Node/Pi worker children."""
    if timeout <= 0:
        raise RuntimeError("New-stack check exceeded its overall timeout")
    print(f"Checking: {' '.join(command)}", flush=True)
    process = subprocess.Popen(command, cwd=cwd, env=env, start_new_session=True,
                               stdout=subprocess.PIPE if log else None,
                               stderr=subprocess.STDOUT if log else None, text=True)
    reader = None
    if log:
        def collect_output() -> None:
            with log.open("a", encoding="utf-8") as output:
                output.write(f"Checking: {' '.join(command)}\n")
                for line in process.stdout:
                    print(line, end="", flush=True)
                    output.write(line)
                    output.flush()
        reader = threading.Thread(target=collect_output, daemon=True)
        reader.start()
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        if reader:
            reader.join(timeout=5)
        raise RuntimeError(f"Check timed out after {timeout:.0f}s: {command[0]}") from None
    except BaseException:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        if reader:
            reader.join(timeout=5)
        raise
    if reader:
        reader.join(timeout=5)
    if code:
        raise subprocess.CalledProcessError(code, command)


def require_postgres(dsn: str) -> None:
    """Reject missing/nonlocal DSNs before importing tests or touching a database."""
    if not dsn:
        raise RuntimeError("TEST_POSTGRES_DSN is required; PostgreSQL tests may not skip in CI")
    import psycopg
    from psycopg.conninfo import conninfo_to_dict

    values = conninfo_to_dict(dsn)
    if (values.get("host") not in {"127.0.0.1", "localhost", "::1"}
            or values.get("hostaddr", "") not in {"", "127.0.0.1", "::1"}
            or "service" in values):
        raise RuntimeError("CI PostgreSQL DSN must target a disposable loopback service")
    # Pin hostaddr as well: inherited PGHOSTADDR cannot redirect the supplied host.
    address = values.get("hostaddr") or ("::1" if values["host"] == "::1" else "127.0.0.1")
    with psycopg.connect(dsn, hostaddr=address, connect_timeout=5) as connection:
        version = int(connection.execute("SHOW server_version_num").fetchone()[0])
    if not 170000 <= version < 180000:
        raise RuntimeError("CI requires PostgreSQL major version 17")
    print("PostgreSQL 17 service verified (DSN suppressed)", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("backend", "frontend", "pi", "deployment"))
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--per-file", action="store_true",
                        help="Diagnose backend files separately with trace and a file timeout")
    parser.add_argument("--file-timeout", type=int, default=120)
    parser.add_argument("--tests", nargs="+", help="Explicit backend test paths")
    parser.add_argument("--report-dir", type=Path,
                        help="Preserve logs and JUnit XML outside the source tree")
    options = parser.parse_args()
    if options.timeout <= 0 or options.file_timeout <= 0:
        parser.error("Timeouts must be positive")
    reports = options.report_dir.resolve() if options.report_dir else None
    if reports:
        if reports.is_relative_to(ROOT):
            parser.error("Reports must be outside the source tree")
        reports.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + options.timeout
    dsn = os.environ.get("TEST_POSTGRES_DSN", "")
    if options.stage in {"backend", "deployment"}:
        require_postgres(dsn)
    with tempfile.TemporaryDirectory(prefix="pskit-new-stack-ci-") as temporary:
        directory = Path(temporary)
        source = directory / "source"
        snapshot(source)
        env = isolated_env(directory, source)
        work = directory / "work"
        work.mkdir()

        def check(command: list[str], cwd: Path = work, maximum: int | None = None) -> None:
            remaining = deadline - time.monotonic()
            run(command, cwd, env, min(remaining, maximum) if maximum else remaining,
                log=reports / f"{options.stage}.log" if reports else None)

        if options.stage == "backend":
            link_dependencies(source, "apps/api/pi")
            env.update(TEST_POSTGRES_DSN=dsn, PSKIT_CI_REQUIRE_POSTGRES="1")
            backend = source / "apps/api"
            check([sys.executable, "-m", "ruff", "check", "app", "tests"], backend)
            check([sys.executable, "-m", "compileall", "-q", "app", "pskit_compute", "tests"],
                  backend)
            targets = options.tests or ["tests"]
            command = [sys.executable, "-m", "pytest", "-q", "-p", "pytest_asyncio.plugin",
                       "-p", "ci_pg_guard", "-o", "faulthandler_timeout=60",
                       "-o", f"cache_dir={directory / 'pytest-cache'}"]
            if options.per_file:
                files = sorted({path for target in targets
                                for path in (list((backend / target).rglob("test_*.py"))
                                             if (backend / target).is_dir()
                                             else [backend / target])})
                for path in files:
                    env["PSKIT_CI_EXPECT_POSTGRES"] = str(int("postgres" in path.parts))
                    xml = ([f"--junitxml={reports / (path.parent.name + '-' + path.stem + '.xml')}"]
                           if reports else [])
                    check([*command, *xml, str(path)], maximum=options.file_timeout)
            else:
                env["PSKIT_CI_EXPECT_POSTGRES"] = str(int(
                    not options.tests or any("postgres" in target for target in targets)))
                xml = [f"--junitxml={reports / 'backend.xml'}"] if reports else []
                check([*command, *xml, *(str(backend / target) for target in targets)])
        elif options.stage == "frontend":
            link_dependencies(source, "apps/web")
            for task in ("typecheck", "lint", "test", "build"):
                check(["npm", "run", task], source / "apps/web")
        elif options.stage == "pi":
            link_dependencies(source, "apps/api/pi")
            check(["node", "--test", "extension.test.js"], source / "apps/api/pi")
        else:
            env.update(TEST_POSTGRES_DSN=dsn, PSKIT_CI_REQUIRE_POSTGRES="1")
            check([sys.executable, "-m", "pytest", "-q", "-p", "pytest_asyncio.plugin",
                   "-p", "ci_pg_guard",
                   str(source / "deploy/agent/tests"), str(source / "scripts/test_ci_new_stack.py"),
                   str(source / "scripts/test_benchmark_new_stack.py"),
                   "--ignore=" + str(source / "deploy/agent/tests/test_backend_image.py"),
                   "--ignore=" + str(source / "deploy/agent/tests/test_web_routes.py"),
                   "--ignore=" + str(source / "deploy/agent/tests/test_split_web_routes.py"),
                   "-o", "faulthandler_timeout=60",
                   "-o", f"cache_dir={directory / 'pytest-cache'}"])


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
