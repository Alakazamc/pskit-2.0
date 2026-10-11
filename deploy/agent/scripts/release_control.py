"""Application-only release controller. Never bootstrap, migrate, build, or stop a stack."""

from __future__ import annotations

import argparse
import copy as copy
import fcntl
import hashlib as hashlib
import ipaddress
import json
import math as math
import os
import re as re
import secrets as secrets
import stat
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from deploy.agent.scripts.production_config import (
    read_env,
)
from deploy.agent.scripts.production_config import (
    validate_production_auth as validate_production_auth,
)
from deploy.agent.scripts.release_api import (
    ADMISSION_PHASES as ADMISSION_PHASES,
)
from deploy.agent.scripts.release_api import (
    ADMISSION_SCOPES as ADMISSION_SCOPES,
)
from deploy.agent.scripts.release_api import (
    _ApiOperations,
)
from deploy.agent.scripts.release_config import (
    CONTROL_VERSION as CONTROL_VERSION,
)
from deploy.agent.scripts.release_config import (
    _ConfigOperations,
)
from deploy.agent.scripts.release_config import (
    fingerprint as fingerprint,
)
from deploy.agent.scripts.release_config import (
    normalized as normalized,
)
from deploy.agent.scripts.release_files import (
    ReleaseError,
    atomic_write,
    directory_fd,
    safe_read,
)
from deploy.agent.scripts.release_files import (
    atomic_at as atomic_at,
)
from deploy.agent.scripts.release_files import (
    publish_assets as publish_assets,
)
from deploy.agent.scripts.release_files import (
    read_at as read_at,
)
from deploy.agent.scripts.release_files import (
    relative_parent as relative_parent,
)
from deploy.agent.scripts.release_files import (
    snapshot_dist as snapshot_dist,
)
from deploy.agent.scripts.release_manifest import load_manifest as load_manifest
from deploy.agent.scripts.release_web import _WebOperations

ROOT = Path(__file__).resolve().parents[3]
# The same literal SQL dictionary is embedded in DATABASE_PROBE and tested on local PostgreSQL.
# Historical charged reservation entries are not unfinished holds. Use authoritative guard/accounting states.
ACTIVITY_QUERIES = {
    "run_leases": "SELECT count(*) FROM agent_runs WHERE lease_owner IS NOT NULL OR lease_expires_at IS NOT NULL",
    "job_leases": "SELECT count(*) FROM agent_jobs WHERE lease_expires_at IS NOT NULL AND lease_expires_at::timestamptz > now()",
    "jobs_unreconciled": "SELECT count(*) FROM agent_jobs WHERE gpu_accounting_status IS NULL OR gpu_accounting_status NOT IN ('settled','reconciled','released')",
    "model_calls_unresolved": "SELECT count(*) FROM agent_model_call_guards g LEFT JOIN agent_runs r ON r.id=g.run_id AND r.user_id=g.user_id WHERE g.status IS NULL OR g.status NOT IN ('reported','unreported','rejected') OR g.reserved_tokens IS NULL OR g.reserved_tokens<0 OR r.id IS NULL",
    "compute_reservations": "SELECT count(*) FROM compute_reservations WHERE active IS NULL OR active",
    "workspace_leases": "SELECT count(*) FROM workspace_sandbox_leases WHERE state IS NULL OR state<>'released' OR NOT exit_confirmed",
    "workspace_unknown": "SELECT count(*) FROM workspace_sandboxes WHERE runtime_state='unknown' OR lifecycle_state IN ('creating','draining','replacing','error')",
    "compute_outbox": "SELECT count(*) FROM compute_outbox WHERE consumed_at IS NULL",
    "device_leases": "SELECT count(*) FROM compute_device_leases",
    "title_calls": "SELECT count(*) FROM agent_session_titles WHERE status IS NULL OR status NOT IN ('waiting','pending','generated','failed','manual')",
    "mcp_leases": "SELECT count(*) FROM mcp_execution_leases",
    "pdf_leases": "SELECT count(*) FROM pdf_execution_leases",
    "mcp_calls_unresolved": "SELECT count(*) FROM mcp_tool_calls WHERE status IS NULL OR status NOT IN ('completed','failed')",
}
ACTIVITY_KEYS = (
    "runs_active",
    "runs_unknown",
    "jobs_active",
    "jobs_unknown",
    "attempts_active",
    "attempts_unknown",
    *ACTIVITY_QUERIES,
)
# Executed in the CURRENT backend only. No migration import or credential output.
# Missing tables/roles/permissions fail the command and therefore fail the gate.
DATABASE_PROBE = (
    "activity_queries = "
    + repr(ACTIVITY_QUERIES)
    + "\n"
    + '''
import json, os
import psycopg
from psycopg import sql
schema = os.environ.get("RESEARCH_AGENT_DATABASE_SCHEMA", "pskit")
with psycopg.connect(os.environ["RESEARCH_AGENT_DATABASE_URL"], connect_timeout=5,
                     options="-c default_transaction_read_only=on -c statement_timeout=5000") as db:
    db.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema)))
    versions = [row[0] for row in db.execute("SELECT version FROM schema_migrations ORDER BY version")]
    counts = {}
    terminal = "('completed','failed','cancelled')"
    known = {
      "agent_runs": "('queued','running','waiting','resume_queued','completed','failed','cancelled')",
      "agent_jobs": "('queued','running','cancelling','completed','failed','cancelled')",
      "workspace_attempts": "('queued','running','cancelling','completed','failed','cancelled','unknown')",
    }
    for table, prefix in (("agent_runs", "runs"), ("agent_jobs", "jobs"),
                          ("workspace_attempts", "attempts")):
        counts[prefix + "_active"] = db.execute(
          "SELECT count(*) FROM " + table + " WHERE status IS NULL OR status NOT IN " + terminal
        ).fetchone()[0]
        counts[prefix + "_unknown"] = db.execute(
          "SELECT count(*) FROM " + table + " WHERE status IS NULL OR status='unknown' OR status NOT IN "
          + known[table]).fetchone()[0]
    for key, query in activity_queries.items():
        counts[key] = db.execute(query).fetchone()[0]
    # Check every auth table, not just auth.users. No automatic GRANT/REVOKE.
    grants = db.execute("""SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='auth' AND c.relkind IN ('r','p','v','m','f') AND
      (has_table_privilege('pskit_app', c.oid, 'SELECT,INSERT,UPDATE,DELETE') OR
       has_table_privilege('litellm', c.oid, 'SELECT,INSERT,UPDATE,DELETE') OR
       has_any_column_privilege('pskit_app', c.oid, 'SELECT,INSERT,UPDATE') OR
       has_any_column_privilege('litellm', c.oid, 'SELECT,INSERT,UPDATE'))""").fetchone()[0]
    auth_tables = db.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='auth' AND c.relname='users'").fetchone()[0]
    print(json.dumps({"schema_versions": versions, "counts": counts,
                      "auth_grants_safe": grants == 0 and auth_tables == 1}))
'''
)


def run_command(command: list[str], *, env: dict, input_text: str | None = None) -> str:
    try:
        result = subprocess.run(
            command,
            env=env,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReleaseError(
            "Release command could not complete; inspect private host logs"
        ) from error
    if result.returncode:
        raise ReleaseError("Release command failed; inspect private host logs")
    return result.stdout


def get_http(url: str) -> tuple[int, bytes]:
    # Operator probes must not accidentally traverse a browser/system public proxy.
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(Request(url, method="GET"), timeout=5) as response:
            return response.status, response.read(64 * 1024 + 1)
    except HTTPError as error:
        return error.code, b""
    except (OSError, URLError) as error:
        raise ReleaseError("HTTP release probe is unavailable") from error


def _receiver_url(url):
    try:
        parsed = urlsplit(url)
        address = ipaddress.ip_address(parsed.hostname or "")
        if (parsed.scheme not in {"http", "https"} or parsed.username or parsed.password
                or parsed.query or parsed.fragment or not parsed.path or len(url) > 2048
                or parsed.port is not None and not 1 <= parsed.port <= 65535
                or not address.is_loopback and (not address.is_private or address.is_reserved
                    or address.is_unspecified or address.is_multicast or address.is_link_local)):
            raise ValueError
    except (TypeError, ValueError):
        raise ReleaseError("Authenticated Receiver URL must be an explicit private IP") from None


def _receiver_token(path):
    """Pin every path component and read one bounded mode-0600 token."""
    try:
        path = Path(path)
        if not path.is_absolute():
            raise ValueError
        with directory_fd(path.parent) as parent:
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            with os.fdopen(descriptor, "rb") as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or stat.S_IMODE(before.st_mode) != 0o600 or before.st_size > 257:
                    raise ValueError
                value = stream.read(258)
                after = os.fstat(stream.fileno())
                if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise ValueError
        token = value.removesuffix(b"\n").decode("ascii")
        if not 32 <= len(token) <= 256 or any(not 33 <= ord(char) <= 126 for char in token):
            raise ValueError
        return token
    except (OSError, TypeError, ValueError, UnicodeError):
        raise ReleaseError("Receiver activity authentication file is unavailable or unsafe") from None


class _ReceiverNoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def _receiver_object(pairs):
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ValueError("Duplicate receiver evidence member")
    return value


def get_receiver_http(url: str, token: str) -> tuple[int, bytes]:
    _receiver_url(url)
    if not 32 <= len(token) <= 256 or any(not 33 <= ord(char) <= 126 for char in token):
        raise ReleaseError("Receiver activity authentication is invalid")
    opener = build_opener(ProxyHandler({}), _ReceiverNoRedirect())
    try:
        with opener.open(Request(url, method="GET", headers={"Authorization": "Bearer " + token}), timeout=5) as response:
            return response.status, response.read(64 * 1024 + 1)
    except HTTPError as error:
        error.close()
        return error.code, b""
    except (OSError, URLError):
        raise ReleaseError("Receiver activity probe is unavailable") from None


class Controller(_ConfigOperations, _ApiOperations, _WebOperations):
    def __init__(
        self, path: Path, environment: str, *, runner=run_command, http=get_http,
        receiver_http=get_receiver_http,
    ):
        self.config = json.loads(safe_read(path, private=True, limit=64 * 1024))
        required = {
            "schema_version",
            "environment",
            "repository",
            "cloud_env_file",
            "backend_env_file",
            "release_directory",
            "state_directory",
            "static_directory",
            "backend_url",
            "public_url",
            "critical_smoke_command",
            "receiver_activity_urls",
            "receiver_activity_required",
        }
        if (
            not required <= set(self.config)
            or set(self.config) - required - {"admission_barrier", "receiver_activity_auth"}
            or self.config["schema_version"] != CONTROL_VERSION
            or environment not in {"prod", "staging"}
            or self.config["environment"] != environment
        ):
            raise ReleaseError(
                "Explicit environment and private controller configuration disagree"
            )
        self.environment, self.runner, self.http = environment, runner, http
        self.receiver_http = receiver_http
        self.repository = Path(self.config["repository"])
        self.cloud_path = Path(self.config["cloud_env_file"])
        self.backend_path = Path(self.config["backend_env_file"])
        self.releases = Path(self.config["release_directory"])
        self.state = Path(self.config["state_directory"])
        self.static = Path(self.config["static_directory"])
        if any(
            not path.is_absolute()
            for path in (
                self.repository,
                self.cloud_path,
                self.backend_path,
                self.releases,
                self.state,
                self.static,
            )
        ):
            raise ReleaseError("Operational paths must be explicit absolute paths")
        for directory in (self.repository, self.releases, self.state, self.static):
            with directory_fd(directory) as fd:
                if directory == self.state and os.fstat(fd).st_mode & 0o077:
                    raise ReleaseError("Controller state directory must be private")
        for key in ("backend_url", "public_url"):
            url = urlsplit(self.config[key])
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username
                or url.password
            ):
                raise ReleaseError("Probe URL is invalid")
        if urlsplit(self.config["backend_url"]).hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ReleaseError("Backend probes must address the local loopback service")
        command = self.config["critical_smoke_command"]
        if (
            not isinstance(command, list)
            or not command
            or any(
                not isinstance(item, str) or not item or "\0" in item
                for item in command
            )
        ):
            raise ReleaseError(
                "An explicit structured critical smoke command is required"
            )
        urls = self.config["receiver_activity_urls"]
        if not isinstance(urls, list) or any(not isinstance(url, str) for url in urls):
            raise ReleaseError("Receiver activity endpoints must be an explicit list")
        authentication = self.config.get("receiver_activity_auth", {})
        if not isinstance(authentication, dict) or set(authentication) - set(urls):
            raise ReleaseError("Receiver activity authentication must match configured endpoints")
        for url, identity in authentication.items():
            _receiver_url(url)
            if (not isinstance(identity, dict) or set(identity) != {"token_file"}
                    or not isinstance(identity["token_file"], str) or not Path(identity["token_file"]).is_absolute()):
                raise ReleaseError("Receiver activity authentication requires an absolute private token file")
        if not isinstance(self.config["receiver_activity_required"], bool):
            raise ReleaseError("Receiver activity requirement must be explicit")
        if environment == "prod" and not self.config["receiver_activity_required"]:
            # API releases on a production host with remote executors require journal evidence.
            # Web-only does not restart execution and can use the central activity gate alone.
            self.receiver_unverified = True
        else:
            self.receiver_unverified = False
        if self.config.get("admission_barrier") is not None:
            self._admission_config()

    def _activity(self, manifest: dict) -> dict:
        report = json.loads(
            self.runner(
                self._compose(manifest) + ["exec", "-T", "backend", "python", "-"],
                env=self._env(manifest["backend"]["reference"]),
                input_text=DATABASE_PROBE,
            )
        )
        counts = report.get("counts", {})
        if set(counts) != set(ACTIVITY_KEYS) or any(
            type(value) is not int or value != 0 for value in counts.values()
        ):
            raise ReleaseError(
                "Active or unknown application activity prevents release"
            )
        if report.get("schema_versions") != list(
            range(1, manifest["database_schema_version"] + 1)
        ):
            raise ReleaseError(
                "Database schema is incompatible; run an approved migration separately"
            )
        if report.get("auth_grants_safe") is not True:
            raise ReleaseError(
                "Unsafe or unverified auth schema grants prevent release"
            )
        return report

    def _receiver_activity(self, *, required: bool) -> None:
        urls = self.config["receiver_activity_urls"]
        if required and not urls:
            raise ReleaseError(
                "Receiver journal/outbox evidence is unavailable; API release refused"
            )
        for url in urls:
            identity = self.config.get("receiver_activity_auth", {}).get(url)
            code, body = (self.receiver_http(url, _receiver_token(identity["token_file"]))
                          if identity is not None else self.http(url))
            if not isinstance(body, bytes) or len(body) > 64 * 1024:
                raise ReleaseError("Receiver activity evidence is invalid")
            try:
                data = json.loads(body, object_pairs_hook=_receiver_object)
            except (ValueError, UnicodeError, RecursionError):
                raise ReleaseError("Receiver activity evidence is invalid") from None
            if (
                code != 200
                or not isinstance(data, dict)
                or set(data) != {"schema_version", "observed_at", "counts"}
                or data["schema_version"] != "pskit.receiver-activity.v1"
                or type(data["observed_at"]) not in {int, float}
                or not 0 <= time.time() - data["observed_at"] <= 30
                or not isinstance(data["counts"], dict)
                or set(data["counts"])
                != {"active", "unknown", "journal_pending", "outbox_pending"}
                or any(
                    type(value) is not int or value != 0
                    for value in data["counts"].values()
                )
            ):
                raise ReleaseError(
                    "Active, stale, or unknown Receiver evidence prevents API release"
                )

    def _health(self) -> None:
        for path in ("/health/live", "/health/ready"):
            if self.http(self.config["backend_url"].rstrip("/") + path)[0] != 200:
                raise ReleaseError("Backend live/readiness probe failed")

    def _postflight(self) -> None:
        self._health()
        base = self.config["public_url"].rstrip("/")
        probes = [
            ("/login", 200),
            ("/api/v1/me/usage", 401),
            ("/api/v1/internal/release-probe", 404),
        ]
        if self.environment == "prod":
            probes.append(("/admin", 403))
        for path, expected in probes:
            if self.http(base + path)[0] != expected:
                raise ReleaseError("Critical public boundary smoke failed")
        self.runner(
            self.config["critical_smoke_command"],
            env={
                key: value
                for key, value in os.environ.items()
                if key in {"PATH", "HOME"}
            },
        )

    def doctor(self) -> dict:
        self._check_config()
        current = self._current_manifest("api")
        rendered = self.render(current)
        if fingerprint(rendered) != current["compose"]["resolved_sha256"]:
            raise ReleaseError("Current resolved Compose fingerprint differs")
        self._live_backend(current)
        activity = self._activity(current)
        self._health()
        pending = (self.state / "pending.json").exists()
        if pending:
            raise ReleaseError(
                "Interrupted release requires an explicit rollback before new releases"
            )
        admission = "unavailable"
        if self.config.get("admission_barrier") is not None:
            self._qualified_admission(current)
            # This read-only check validates artifact/configuration pins. Only
            # the actual adapter challenge verifies a server-enforced hold.
            admission = "qualified_configuration; runtime_observation_required"
        return {
            "status": "passed",
            "environment": self.environment,
            "schema_version": activity["schema_versions"][-1],
            "receiver_journal": "unverified"
            if not self.config["receiver_activity_urls"]
            else "configured",
            "api_admission": admission,
        }

    def status(self) -> dict:
        current = self.current()
        manifest = self._current_manifest("api")
        # JSON output includes only artifact pins and counts; never container Env/Compose secrets.
        ids = self.runner(
            self._compose(manifest) + ["ps", "-q", "backend"],
            env=self._env(manifest["backend"]["reference"]),
        ).split()
        return {
            "environment": self.environment,
            "current": current,
            "backend_count": len(ids),
            "pending_release": (self.state / "pending.json").exists(),
            "api_admission": "configured"
            if self.config.get("admission_barrier") is not None
            else "unavailable",
        }

    def logs(self, service: str) -> str:
        if service != "backend":
            raise ReleaseError("Daily controller logs are scoped to backend")
        manifest = self._current_manifest("api")
        output = self.runner(
            self._compose(manifest) + ["logs", "--tail=100", "backend"],
            env=self._env(manifest["backend"]["reference"]),
        )
        secrets = set()
        for path in (self.cloud_path, self.backend_path):
            safe_read(path, private=True)
            for key, value in read_env(path).items():
                if value and (
                    any(term in key for term in ("SECRET", "PASSWORD", "TOKEN", "KEY"))
                    or key.endswith("DATABASE_URL")
                ):
                    secrets.add(value)
        for value in sorted(secrets, key=len, reverse=True):
            output = output.replace(value, "[REDACTED]")
        return output

    def _write_json(self, name: str, data: dict) -> None:
        atomic_write(
            self.state / name, (json.dumps(data, sort_keys=True) + "\n").encode()
        )

    def _remove_pending(self) -> None:
        with directory_fd(self.state) as fd:
            os.unlink("pending.json", dir_fd=fd)
            os.fsync(fd)

    def release(self, version: str, component: str, *, rollback: bool = False) -> None:
        if component not in {"api", "web"}:
            raise ReleaseError(
                "Executor release requires its separate reviewed procedure"
            )
        with directory_fd(self.state) as fd:
            lock_fd = os.open(
                "release.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=fd
            )
            with os.fdopen(lock_fd, "wb") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._release_locked(version, component, rollback=rollback)

    def _release_locked(self, version: str, component: str, *, rollback: bool) -> None:
        self._check_config()
        state = self.current()
        old = self._current_manifest(component)
        current_api = self._current_manifest("api")
        candidate, digest = self.artifact(version)
        pending_path = self.state / "pending.json"
        if pending_path.exists():
            pending = json.loads(safe_read(pending_path, private=True))
            allowed = {
                "component",
                "previous",
                "target",
                "target_reference",
                "cloud_before",
                "created_at",
            }
            if component == "api":
                allowed |= {"admission", "admission_phase"}
            if (
                set(pending) != allowed
                or not rollback
                or pending.get("component") != component
                or state[component]
                not in (pending["previous"], pending["target_reference"])
            ):
                raise ReleaseError(
                    "Interrupted release requires rollback to the previous registered artifact"
                )
            if version != pending["previous"]["release_id"]:
                raise ReleaseError(
                    "Interrupted release must first roll back its previous artifact"
                )
            previous, _ = self.artifact(
                version, expected_hash=pending["previous"]["manifest_sha256"]
            )
            self._recover(pending, previous, component)
            return
        if component == "api":
            self._admission_config()
            self._qualified_admission(current_api)
            self._qualified_admission(candidate)
        self._compatible(current_api, candidate)
        self._live_backend(current_api)
        self._activity(current_api)
        self._health()
        if component == "api":
            if self.receiver_unverified:
                raise ReleaseError(
                    "Production API receiver journal evidence is unverified"
                )
            self._receiver_activity(required=self.config["receiver_activity_required"])
        image = candidate["backend"]["reference"]
        self.runner(["docker", "image", "inspect", image], env=self._env(image))
        cloud_before = safe_read(self.cloud_path, private=True)
        pending = {
            "component": component,
            "previous": state[component],
            "target": version,
            "target_reference": {"release_id": version, "manifest_sha256": digest},
            "cloud_before": cloud_before.decode(),
            "created_at": time.time(),
        }
        if component == "web":
            self._release_web(
                pending, candidate, old, state,
                version=version, digest=digest, component=component,
            )
        else:
            self._release_api(
                pending, current_api, candidate, state, cloud_before, image
            )

    def _recover(self, pending: dict, old: dict, component: str) -> None:
        # SIGKILL/power-loss recovery is explicit; never claim an interrupted transaction succeeded.
        self._compatible(self._current_manifest("api"), old)
        if component == "api":
            self._recover_api(pending, old)
        else:
            self._recover_web(pending, old, component)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "doctor",
            "status",
            "render",
            "logs",
            "release",
            "rollback",
            "bootstrap",
            "migrate",
        ],
    )
    parser.add_argument("--env", required=True, choices=["prod", "staging"])
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--component", choices=["api", "web", "executor"])
    parser.add_argument("--version")
    parser.add_argument("--to")
    parser.add_argument("--service", default="backend")
    args = parser.parse_args(argv)
    try:
        if args.command in {"bootstrap", "migrate"}:
            raise ReleaseError(
                "Bootstrap and schema migration require separate reviewed procedures"
            )
        ctl = Controller(args.config, args.env)
        if args.command in {"release", "rollback"}:
            version = args.version if args.command == "release" else args.to
            if not version or not args.component:
                raise ReleaseError(
                    "Release/rollback require an explicit component and artifact version"
                )
            ctl.release(version, args.component, rollback=args.command == "rollback")
            print(
                json.dumps(
                    {
                        "status": "passed",
                        "environment": args.env,
                        "component": args.component,
                        "release_id": version,
                    }
                )
            )
        elif args.command == "render":
            manifest = ctl._current_manifest("api")
            rendered = ctl.render(manifest)
            print(
                json.dumps(
                    {
                        "project": rendered["name"],
                        "files": manifest["compose"]["files"],
                        "resolved_sha256": fingerprint(rendered),
                    }
                )
            )
        elif args.command == "logs":
            print(ctl.logs(args.service))
        else:
            print(json.dumps(getattr(ctl, args.command)(), sort_keys=True))
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        IndexError,
        ReleaseError,
    ) as error:
        # Never echo underlying Docker output, decoded config, DSNs, or HTTP bodies.
        message = (
            str(error)
            if isinstance(error, (ReleaseError, ValueError))
            and not isinstance(error, (json.JSONDecodeError, UnicodeError))
            else "Release controller input or command failed"
        )
        print(message, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(
            "Release interrupted; inspect status and use explicit rollback",
            file=sys.stderr,
        )
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
