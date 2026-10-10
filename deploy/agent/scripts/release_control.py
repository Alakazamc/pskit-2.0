"""Application-only release controller. Never bootstrap, migrate, build, or stop a stack."""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import math
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from deploy.agent.scripts.production_config import read_env, validate_production_auth
from deploy.agent.scripts.release_files import (
    ReleaseError,
    atomic_at,
    atomic_write,
    directory_fd,
    publish_assets,
    read_at,
    relative_parent,
    safe_read,
    snapshot_dist,
)
from deploy.agent.scripts.release_manifest import load_manifest

ROOT = Path(__file__).resolve().parents[3]
CONTROL_VERSION = "pskit.control.v1"
ADMISSION_SCOPES = [
    "agent_runs",
    "compute_jobs",
    "workspace_attempts",
    "scheduler_claims",
]
ADMISSION_PHASES = {
    "prepared",
    "closed",
    "switching",
    "committed",
    "releasing",
    "released",
}
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


def fingerprint(value: dict) -> str:
    """Hash complete resolved JSON with canonical keys; never print the unredacted model."""
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


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


def normalized(model: dict, image: str) -> dict:
    result = copy.deepcopy(model)
    backend_consumers = {
        "backend",
        "af3-callback-proxy",
        "mcp-receiver",
        "model-gateway-mock",
    }
    for name, service in result.get("services", {}).items():
        if service.get("image") == image:
            if name not in backend_consumers:
                raise ReleaseError(
                    "Unexpected infrastructure service uses the backend artifact pin"
                )
            service["image"] = "<unchanged-backend-artifact>"
    return result


class Controller:
    def __init__(
        self, path: Path, environment: str, *, runner=run_command, http=get_http
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
            or set(self.config) - required - {"admission_barrier"}
            or self.config["schema_version"] != CONTROL_VERSION
            or environment not in {"prod", "staging"}
            or self.config["environment"] != environment
        ):
            raise ReleaseError(
                "Explicit environment and private controller configuration disagree"
            )
        self.environment, self.runner, self.http = environment, runner, http
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

    def _admission_config(self) -> dict:
        adapter = self.config.get("admission_barrier")
        required = {
            "adapter_id",
            "qualification_sha256",
            "enter_command",
            "verify_command",
            "release_command",
        }
        if not isinstance(adapter, dict) or set(adapter) != required:
            raise ReleaseError(
                "A qualified admission adapter is required for API changes"
            )
        if (
            not isinstance(adapter["adapter_id"], str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", adapter["adapter_id"])
            is None
            or not isinstance(adapter["qualification_sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", adapter["qualification_sha256"]) is None
        ):
            raise ReleaseError("The admission adapter identity is invalid")
        for operation in ("enter", "verify", "release"):
            command = adapter[operation + "_command"]
            if (
                not isinstance(command, list)
                or not command
                or any(
                    not isinstance(item, str) or not item or "\0" in item
                    for item in command
                )
                or not Path(command[0]).is_absolute()
            ):
                raise ReleaseError(
                    "Admission adapter commands must be explicit absolute argv arrays"
                )
        return adapter

    def _admission_identity(
        self, pending: dict, manifest: dict, *, new: bool = False
    ) -> dict:
        adapter = self._admission_config()
        expected = {
            "adapter_id": adapter["adapter_id"],
            "qualification_sha256": adapter["qualification_sha256"],
            "environment": self.environment,
            "project": manifest["compose"]["project"],
            "previous_manifest_sha256": pending["previous"]["manifest_sha256"],
            "target_manifest_sha256": pending["target_reference"]["manifest_sha256"],
        }
        if new:
            return {**expected, "owner_nonce": secrets.token_hex(32)}
        identity = pending.get("admission")
        if (
            not isinstance(identity, dict)
            or set(identity) != set(expected) | {"owner_nonce"}
            or any(identity.get(key) != value for key, value in expected.items())
            or not isinstance(identity["owner_nonce"], str)
            or re.fullmatch(r"[0-9a-f]{64}", identity["owner_nonce"]) is None
            or pending.get("admission_phase") not in ADMISSION_PHASES
        ):
            raise ReleaseError(
                "Pending admission owner differs from the configured adapter or artifacts"
            )
        return identity

    def _admission_command(self, operation: str, pending: dict, manifest: dict) -> None:
        adapter = self._admission_config()
        identity = self._admission_identity(pending, manifest)
        request = {
            **identity,
            "schema_version": "pskit.admission-command.v1",
            "operation": operation,
            "scopes": ADMISSION_SCOPES,
        }
        output = self.runner(
            adapter[operation + "_command"],
            env={
                key: value
                for key, value in os.environ.items()
                if key in {"PATH", "HOME"}
            },
            input_text=json.dumps(request, sort_keys=True) + "\n",
        )
        try:
            if len(output.encode()) > 64 * 1024:
                raise ValueError
            receipt = json.loads(output)
        except (ValueError, UnicodeError, AttributeError):
            raise ReleaseError(
                "The admission adapter receipt is unavailable or invalid"
            ) from None
        expected = {
            **identity,
            "schema_version": "pskit.admission-receipt.v1",
            "scopes": ADMISSION_SCOPES,
            "state": "released" if operation == "release" else "closed",
            # Observation expiry NEVER reopens the durable hold. Only this owner's explicit release does.
            "hold_mode": "until_explicit_release",
        }
        now = time.time()
        if (
            not isinstance(receipt, dict)
            or set(receipt) != set(expected) | {"observed_at", "valid_until"}
            or any(receipt.get(key) != value for key, value in expected.items())
            or any(
                type(receipt[key]) not in {int, float}
                or not math.isfinite(receipt[key])
                for key in ("observed_at", "valid_until")
            )
            or not 0 <= now - receipt["observed_at"] <= 30
            or not now < receipt["valid_until"] <= receipt["observed_at"] + 30
        ):
            raise ReleaseError(
                "Fresh admission ownership and closed execution scope are unverified"
            )

    def _admission_gate(self, pending: dict, manifest: dict) -> None:
        self._admission_command("verify", pending, manifest)
        self._receiver_activity(
            required=self.environment == "prod"
            or self.config["receiver_activity_required"]
        )
        self._activity(manifest)
        # Activity/receiver probes may block. Re-observe the server-enforced hold immediately before mutation.
        self._admission_command("verify", pending, manifest)

    def _admission_phase(self, pending: dict, phase: str) -> None:
        pending["admission_phase"] = phase
        self._write_json("pending.json", pending)

    def _finish_api(
        self, pending: dict, manifest: dict, state: dict, reference: dict
    ) -> None:
        self._admission_command("verify", pending, manifest)
        self._postflight()
        self._admission_gate(pending, manifest)
        state["api"] = reference
        self._write_json("current.json", state)
        self._admission_phase(pending, "committed")
        self._admission_gate(pending, manifest)
        # Persist uncertainty before a command which can reopen admission. Never auto-rollback after this point.
        self._admission_phase(pending, "releasing")
        self._admission_command("release", pending, manifest)
        self._admission_phase(pending, "released")
        self._remove_pending()

    def _env(self, image: str) -> dict[str, str]:
        safe_read(self.cloud_path, private=True)
        env = {
            key: value
            for key, value in os.environ.items()
            if key
            in {
                "PATH",
                "HOME",
                "DOCKER_HOST",
                "DOCKER_CONTEXT",
                "DOCKER_CONFIG",
                "XDG_RUNTIME_DIR",
            }
        }
        # Literal parser shared with production preflight; no shell sourcing or expansion.
        env.update(read_env(self.cloud_path))
        env["AGENT_BACKEND_IMAGE"] = image
        return env

    def _compose(self, manifest: dict) -> list[str]:
        files = manifest["compose"]["files"]
        return [
            "docker",
            "compose",
            "--env-file",
            str(self.cloud_path),
            *(item for path in files for item in ("-f", str(self.repository / path))),
            "-p",
            manifest["compose"]["project"],
        ]

    def render(self, manifest: dict) -> dict:
        return json.loads(
            self.runner(
                self._compose(manifest) + ["config", "--format", "json"],
                env=self._env(manifest["backend"]["reference"]),
            )
        )

    def current(self) -> dict:
        result = json.loads(
            safe_read(self.state / "current.json", private=True, limit=64 * 1024)
        )
        if (
            set(result) != {"schema_version", "api", "web"}
            or result["schema_version"] != CONTROL_VERSION
        ):
            raise ReleaseError("Current artifact registration is missing or invalid")
        for component in ("api", "web"):
            reference = result[component]
            if (
                set(reference) != {"release_id", "manifest_sha256"}
                or re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", reference["release_id"]
                )
                is None
                or re.fullmatch(r"[0-9a-f]{64}", reference["manifest_sha256"]) is None
            ):
                raise ReleaseError("Current artifact registration is invalid")
        return result

    def artifact(
        self, version: str, *, expected_hash: str | None = None
    ) -> tuple[dict, str]:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", version) is None:
            raise ReleaseError("Release version is invalid")
        path = self.releases / version / "manifest.json"
        content = safe_read(path, limit=64 * 1024)
        digest = hashlib.sha256(content).hexdigest()
        if expected_hash and digest != expected_hash:
            raise ReleaseError("Registered Manifest has changed")
        manifest = load_manifest(path, require_qualified=True)
        if safe_read(path, limit=64 * 1024) != content:
            raise ReleaseError("Manifest changed during validation")
        project = (
            "pskit-agent-cloud" if self.environment == "prod" else "pskit-agent-staging"
        )
        if (
            manifest["release_id"] != version
            or manifest["compose"]["project"] != project
        ):
            raise ReleaseError("Manifest version or target environment differs")
        return manifest, digest

    def _current_manifest(self, component: str) -> dict:
        ref = self.current()[component]
        return self.artifact(ref["release_id"], expected_hash=ref["manifest_sha256"])[0]

    def _check_config(self) -> None:
        safe_read(self.backend_path, private=True)
        safe_read(self.cloud_path, private=True)
        backend, cloud = read_env(self.backend_path), read_env(self.cloud_path)
        if self.environment == "prod":
            validate_production_auth(backend, cloud)
        for key in ("RESEARCH_AGENT_DATABASE_URL", "MODEL_GATEWAY_API_KEY"):
            if not backend.get(key) or "replace-with" in backend[key]:
                raise ReleaseError("Required backend configuration is unset: " + key)
        if cloud.get("AGENT_BACKEND_ENV_FILE") != str(self.backend_path):
            raise ReleaseError(
                "Backend source environment path differs from controller configuration"
            )

    def _live_backend(
        self, manifest: dict, *, allowed_reference: str | None = None
    ) -> None:
        env = self._env(manifest["backend"]["reference"])
        ids = self.runner(
            self._compose(manifest) + ["ps", "-q", "backend"], env=env
        ).split()
        if len(ids) != 1:
            raise ReleaseError("Exactly one current backend is required")
        info = json.loads(self.runner(["docker", "inspect", ids[0]], env=env))
        image = json.loads(
            self.runner(
                [
                    "docker",
                    "image",
                    "inspect",
                    allowed_reference or manifest["backend"]["reference"],
                ],
                env=env,
            )
        )
        labels = info[0]["Config"].get("Labels", {})
        if (
            info[0]["Image"] != image[0]["Id"]
            or labels.get("com.docker.compose.project")
            != manifest["compose"]["project"]
            or labels.get("com.docker.compose.service") != "backend"
        ):
            raise ReleaseError(
                "Running backend differs from registered immutable artifact"
            )
        expected_files = [
            str(self.repository / name) for name in manifest["compose"]["files"]
        ]
        if (
            labels.get("com.docker.compose.project.config_files", "").split(",")
            != expected_files
        ):
            raise ReleaseError(
                "Running backend lost or reordered required Compose overlays"
            )
        rendered = self.render(manifest)
        service = rendered["services"]["backend"]
        actual_env = dict(
            item.split("=", 1)
            for item in info[0]["Config"].get("Env", [])
            if "=" in item
        )
        if any(
            actual_env.get(key) != str(value)
            for key, value in service.get("environment", {}).items()
        ):
            raise ReleaseError(
                "Running backend environment differs from resolved configuration"
            )
        actual_mounts = {
            item["Destination"]: item for item in info[0].get("Mounts", [])
        }
        mounts = service.get("volumes", [])
        if set(actual_mounts) != {item["target"] for item in mounts}:
            raise ReleaseError("Running backend required mounts differ")
        for mount in mounts:
            actual = actual_mounts[mount["target"]]
            source_key = "Source" if mount["type"] == "bind" else "Name"
            expected_source = (
                mount["source"]
                if mount["type"] == "bind"
                else rendered["volumes"][mount["source"]]["name"]
            )
            if (
                actual.get("Type") != mount["type"]
                or actual.get(source_key) != expected_source
                or actual.get("RW") != (not mount.get("read_only", False))
            ):
                raise ReleaseError(
                    "Running backend source mount differs from registered configuration"
                )
        networks = {
            rendered["networks"][name].get(
                "name", manifest["compose"]["project"] + "_" + name
            )
            for name in service.get("networks", {})
        }
        if networks != set(info[0].get("NetworkSettings", {}).get("Networks", {})):
            raise ReleaseError(
                "Running backend networks differ from registered configuration"
            )

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
            code, body = self.http(url)
            try:
                data = json.loads(body)
            except (ValueError, UnicodeError):
                raise ReleaseError("Receiver activity evidence is invalid") from None
            if (
                code != 200
                or set(data) != {"schema_version", "observed_at", "counts"}
                or data["schema_version"] != "pskit.receiver-activity.v1"
                or type(data["observed_at"]) not in {int, float}
                or not 0 <= time.time() - data["observed_at"] <= 30
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
        return {
            "status": "passed",
            "environment": self.environment,
            "schema_version": activity["schema_versions"][-1],
            "receiver_journal": "unverified"
            if not self.config["receiver_activity_urls"]
            else "configured",
            "api_admission": "configured"
            if self.config.get("admission_barrier") is not None
            else "unavailable",
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

    def _up(self, manifest: dict) -> None:
        self.runner(
            self._compose(manifest)
            + ["up", "-d", "--no-deps", "--wait", "--wait-timeout", "60", "backend"],
            env=self._env(manifest["backend"]["reference"]),
        )
        self._live_backend(manifest)

    def _pin_cloud(self, before: bytes, image: str) -> bytes:
        text = before.decode()
        pattern = re.compile(r"(?m)^(?:export\s+)?AGENT_BACKEND_IMAGE\s*=.*$")
        if len(pattern.findall(text)) != 1:
            raise ReleaseError("Backend image pin is missing or duplicated")
        result = pattern.sub("AGENT_BACKEND_IMAGE=" + image, text).encode()
        # Admission/database probes can block after preflight. Recheck immediately before replacement.
        if safe_read(self.cloud_path, private=True) != before:
            raise ReleaseError("Operational configuration changed before the pin update")
        atomic_write(self.cloud_path, result)
        return result

    def _compatible(self, current_api: dict, candidate: dict) -> None:
        if (
            candidate["database_schema_version"]
            != current_api["database_schema_version"]
        ):
            raise ReleaseError(
                "Incompatible schema; application rollback never downgrades the database"
            )
        for key in (
            "pi_version",
            "litellm_version",
            "compatibility_sha256",
            "opensandbox",
        ):
            if candidate[key] != current_api[key] and key != "pi_version":
                raise ReleaseError(
                    "Infrastructure or provider compatibility changed; use separate qualification"
                )
        if candidate["compose"]["files"] != current_api["compose"]["files"]:
            raise ReleaseError(
                "Application overlays changed; executor/configuration procedure is separate"
            )
        rendered_old, rendered_new = self.render(current_api), self.render(candidate)
        for manifest, rendered in (
            (current_api, rendered_old),
            (candidate, rendered_new),
        ):
            if fingerprint(rendered) != manifest["compose"]["resolved_sha256"]:
                raise ReleaseError(
                    "Resolved Compose fingerprint differs from immutable Manifest"
                )
        if normalized(rendered_old, current_api["backend"]["reference"]) != normalized(
            rendered_new, candidate["backend"]["reference"]
        ):
            raise ReleaseError(
                "Required source binds, environments, networks, or volumes changed"
            )

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
            files = snapshot_dist(
                self.releases / version / "dist", candidate["frontend"]["sha256"]
            )
            old_files = snapshot_dist(
                self.releases / old["release_id"] / "dist", old["frontend"]["sha256"]
            )
            with directory_fd(self.static) as fd:
                for relative, content in old_files.items():
                    with relative_parent(fd, relative) as (parent, name):
                        if read_at(parent, name) != content:
                            raise ReleaseError(
                                "Live web asset differs from registered rollback artifact"
                            )
                publish_assets(fd, files)
                self._write_json("pending.json", pending)
                try:
                    atomic_at(fd, "index.html", files["index.html"])
                    self._postflight()
                    state[component] = {
                        "release_id": version,
                        "manifest_sha256": digest,
                    }
                    self._write_json("current.json", state)
                    self._remove_pending()
                except BaseException:
                    publish_assets(fd, old_files)
                    atomic_at(fd, "index.html", old_files["index.html"])
                    state[component] = pending["previous"]
                    self._write_json("current.json", state)
                    self._remove_pending()
                    raise
        else:
            if (
                read_env(self.cloud_path).get("AGENT_BACKEND_IMAGE")
                != current_api["backend"]["reference"]
            ):
                raise ReleaseError(
                    "Operational backend pin differs from current Manifest"
                )
            pending["admission"] = self._admission_identity(
                pending, current_api, new=True
            )
            pending["admission_phase"] = "prepared"
            # Write BEFORE enter: a killed/failed command might have closed admission without returning a receipt.
            self._write_json("pending.json", pending)
            up_attempted = False
            try:
                self._admission_command("enter", pending, current_api)
                self._admission_phase(pending, "closed")
                # Recheck exact source bytes before mutation; other secret lines are preserved.
                if safe_read(self.cloud_path, private=True) != cloud_before:
                    raise ReleaseError(
                        "Operational configuration changed during preflight"
                    )
                self._admission_gate(pending, current_api)
                self._pin_cloud(cloud_before, image)
                self._admission_gate(pending, current_api)
                self._admission_phase(pending, "switching")
                up_attempted = True
                self._up(candidate)
                self._finish_api(pending, candidate, state, pending["target_reference"])
            except BaseException as error:
                if not up_attempted or pending["admission_phase"] in {
                    "releasing",
                    "released",
                }:
                    raise ReleaseError(
                        "API admission or release state is uncertain; pending recovery marker retained"
                    ) from None
                try:
                    self._admission_gate(pending, current_api)
                    expected = re.sub(
                        r"(?m)^(?:export\s+)?AGENT_BACKEND_IMAGE\s*=.*$",
                        "AGENT_BACKEND_IMAGE=" + image,
                        cloud_before.decode(),
                    ).encode()
                    if safe_read(self.cloud_path, private=True) not in (
                        cloud_before,
                        expected,
                    ):
                        raise ReleaseError(
                            "Operational configuration changed; review privately"
                        )
                    atomic_write(self.cloud_path, cloud_before)
                    self._admission_gate(pending, current_api)
                    self._up(current_api)
                    self._finish_api(pending, current_api, state, pending["previous"])
                except BaseException:  # noqa: BLE001 - retain recovery marker even on a second interrupt
                    raise ReleaseError(
                        "Release and rollback failed; pending recovery marker retained"
                    ) from None
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    raise
                raise ReleaseError(
                    "Application release failed and was rolled back"
                ) from None

    def _recover(self, pending: dict, old: dict, component: str) -> None:
        # SIGKILL/power-loss recovery is explicit; never claim an interrupted transaction succeeded.
        self._compatible(self._current_manifest("api"), old)
        if component == "api":
            self._admission_identity(pending, old)
            # Same-owner enter is idempotent and never steals another owner's hold.
            # It also closes admission again if a release acknowledgement was lost.
            self._admission_command("enter", pending, old)
            self._admission_phase(pending, "closed")
            self._admission_gate(pending, old)
            target, _ = self.artifact(
                pending["target"],
                expected_hash=pending["target_reference"]["manifest_sha256"],
            )
            before = pending["cloud_before"].encode()
            expected = re.sub(
                r"(?m)^(?:export\s+)?AGENT_BACKEND_IMAGE\s*=.*$",
                "AGENT_BACKEND_IMAGE=" + target["backend"]["reference"],
                pending["cloud_before"],
            ).encode()
            if safe_read(self.cloud_path, private=True) not in (before, expected):
                raise ReleaseError(
                    "Operational configuration changed after interruption; review privately"
                )
            atomic_write(self.cloud_path, before)
            self._admission_gate(pending, old)
            self._up(old)
            self._finish_api(pending, old, self.current(), pending["previous"])
        else:
            self._activity(self._current_manifest("api"))
            files = snapshot_dist(
                self.releases / old["release_id"] / "dist", old["frontend"]["sha256"]
            )
            with directory_fd(self.static) as fd:
                # Recovery must restore every verified immutable asset before switching the index.
                # Same-name mismatches remain fail closed; never overwrite a collision.
                publish_assets(fd, files)
                atomic_at(fd, "index.html", files["index.html"])
            self._postflight()
            state = self.current()
            state[component] = pending["previous"]
            self._write_json("current.json", state)
            self._remove_pending()


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
