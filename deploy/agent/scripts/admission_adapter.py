"""Private durable admission CLI: verify the running backend, then acknowledge ownership.

No DDL, shell commands, arbitrary endpoints or credentials in command output.
The component must have been explicitly installed and bound by an enabled backend.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import math
import os
import re
import secrets
import stat
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

# A direct invocation needs repository imports; the interpreter is supplied by
# the approved host entrypoint, not discovered through an untrusted PATH.
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "new_backend"))

import httpx
from app.db.postgres import PostgresDatabase
from app.domain.release_admission import (
    COMPONENT_VERSION,
    ENFORCEMENT_VERSION,
    IDENTITY_FIELDS,
    SCOPES,
    DurableAdmissionGate,
    validate_identity,
)
from psycopg.conninfo import make_conninfo

from deploy.agent.scripts.release_files import directory_fd

CONFIG_FIELDS = {
    "schema_version", "database_url", "database_schema", "backend_url", "backend_token",
    "adapter_id", "qualification_sha256", "environment", "project",
}
OBSERVATION_FIELDS = {
    "schema_version", "challenge", "enabled", "enforcement_version", "component_version",
    "qualification_sha256", "environment", "project", "deployment_id", "state", "identity",
    "generation", "observed_at", "scopes",
}
MAX_BYTES = 64 * 1024


class AdapterError(RuntimeError):
    """A sanitized fail-closed adapter error."""


def strict_json(content):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise AdapterError("Duplicate JSON field")
            value[key] = item
        return value

    def invalid(_value):
        raise AdapterError("Non-finite JSON value")

    if len(content) > MAX_BYTES:
        raise AdapterError("JSON exceeds its private protocol bound")
    return json.loads(content, object_pairs_hook=pairs, parse_constant=invalid)


def read_config(path: Path) -> dict:
    """Pin every path component, require a private directory and regular 0600 file."""
    if not path.is_absolute():
        raise AdapterError("An absolute private configuration is required")
    with directory_fd(path.parent) as parent:
        if stat.S_IMODE(os.fstat(parent).st_mode) != 0o700:
            raise AdapterError("The configuration directory must be private")
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or stat.S_IMODE(before.st_mode) != 0o600
                    or before.st_uid != os.geteuid() or before.st_size > MAX_BYTES):
                raise AdapterError("A regular owned private configuration is required")
            content = stream.read(MAX_BYTES + 1)
            after = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns,
            ):
                raise AdapterError("The configuration changed while reading")
    config = strict_json(content)
    if not isinstance(config, dict) or set(config) != CONFIG_FIELDS:
        raise AdapterError("The private adapter configuration is invalid")
    if config["schema_version"] != "pskit.admission-adapter.v1":
        raise AdapterError("The private adapter configuration version is invalid")
    if (not isinstance(config["database_schema"], str)
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", config["database_schema"]) is None
            or not isinstance(config["database_url"], str)
            or not config["database_url"].startswith(("postgresql://", "postgres://"))):
        raise AdapterError("The private database configuration is invalid")
    if (not isinstance(config["backend_token"], str) or not 32 <= len(config["backend_token"]) <= 256
            or any(not 33 <= ord(char) <= 126 for char in config["backend_token"])):
        raise AdapterError("The private backend identity is invalid")
    parsed = urlsplit(config["backend_url"])
    address = ipaddress.ip_address(parsed.hostname or "")
    if (parsed.scheme not in {"http", "https"} or not address.is_loopback
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/", "/api/v1"} or parsed.port is None):
        raise AdapterError("The backend proof requires an explicit loopback endpoint")
    validate_identity({key: config[key] for key in ("adapter_id", "qualification_sha256", "environment", "project")}
                      | {"owner_nonce": "0" * 64, "previous_manifest_sha256": "0" * 64,
                         "target_manifest_sha256": "0" * 64, "scopes": SCOPES})
    return config


def read_command(content, operation: str, config: dict) -> dict:
    command = strict_json(content)
    if (not isinstance(command, dict) or set(command) != IDENTITY_FIELDS | {"schema_version", "operation"}
            or command["schema_version"] != "pskit.admission-command.v1" or command["operation"] != operation):
        raise AdapterError("The admission command is invalid")
    identity = validate_identity({key: command[key] for key in IDENTITY_FIELDS})
    if any(identity[key] != config[key] for key in ("adapter_id", "qualification_sha256", "environment", "project")):
        raise AdapterError("The command deployment differs from the private binding")
    return identity


def fetch_observation(config: dict, challenge: str) -> dict:
    url = config["backend_url"].rstrip("/") + "/internal/release-admission"
    # No ambient proxy, redirect, public name resolution or response buffering beyond the bound.
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=5) as client, client.stream(
        "GET", url, params={"challenge": challenge},
        headers={"Authorization": "Bearer " + config["backend_token"]},
    ) as response:
        if response.status_code != 200:
            raise AdapterError("The running backend enforcement is unavailable")
        content = bytearray()
        for chunk in response.iter_bytes():
            content.extend(chunk)
            if len(content) > MAX_BYTES:
                raise AdapterError("The backend observation exceeds its protocol bound")
    return strict_json(bytes(content))


def verify_backend(config: dict, gate: DurableAdmissionGate, *, fetch=fetch_observation) -> dict:
    challenge = secrets.token_hex(32)
    observation = fetch(config, challenge)
    if (not isinstance(observation, dict) or set(observation) != OBSERVATION_FIELDS
            or observation["schema_version"] != "pskit.admission-observation.v1"
            or observation["challenge"] != challenge or observation["enabled"] is not True
            or observation["enforcement_version"] != ENFORCEMENT_VERSION
            or type(observation["component_version"]) is not int
            or observation["component_version"] != COMPONENT_VERSION
            or observation["scopes"] != SCOPES
            or type(observation["generation"]) is not int or observation["generation"] < 0
            or type(observation["observed_at"]) not in {int, float}
            or not math.isfinite(observation["observed_at"])
            or not 0 <= time.time() - observation["observed_at"] <= 10):
        raise AdapterError("The running backend enforcement proof is invalid")
    expected = gate.observe(challenge)
    if any(observation[key] != expected[key] for key in OBSERVATION_FIELDS - {"observed_at"}):
        raise AdapterError("The running backend and durable database binding differ")
    return observation


def execute(operation, config, identity, gate, *, fetch=fetch_observation):
    """Only acknowledge proved state; uncertain release restores the owner's hold."""
    verify_backend(config, gate, fetch=fetch)
    try:
        receipt = getattr(gate, operation)(identity)
        proof = verify_backend(config, gate, fetch=fetch)
        if (proof["state"] != ("open" if operation == "release" else "closed")
                or proof["identity"] != identity):
            raise AdapterError("The resulting admission owner is unverified")
    except BaseException:
        if operation == "release":
            # The open transaction may already have committed. Preserve the
            # pending controller marker and restore closure on proof loss or
            # interruption; a second DB failure remains explicitly uncertain.
            try:
                gate.enter(identity)
            except BaseException:  # noqa: BLE001 - retain an uncertain marker even on a second interrupt.
                raise AdapterError("Release acknowledgement and durable hold are unverified") from None
        raise
    # Receipt freshness starts after both database and actual backend observations.
    now = time.time()
    return {**receipt, "observed_at": now, "valid_until": now + 20}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Private durable release admission adapter")
    parser.add_argument("operation", choices=("enter", "verify", "release"))
    parser.add_argument("--config", type=Path, required=True)
    arguments = parser.parse_args(argv)
    # Pool/network errors can contain operational details; the public diagnostic is fixed.
    previous_logging_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    database = None
    try:
        config = read_config(arguments.config)
        identity = read_command(sys.stdin.buffer.read(MAX_BYTES + 1), arguments.operation, config)
        database = PostgresDatabase(make_conninfo(config["database_url"], connect_timeout=5),
                                    schema=config["database_schema"], max_size=2)
        gate = DurableAdmissionGate(database, environment=config["environment"], project=config["project"],
                                    qualification_sha256=config["qualification_sha256"])
        receipt = execute(arguments.operation, config, identity, gate)
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except Exception:  # noqa: BLE001 - credential-redaction boundary for the private CLI.
        print("Admission could not be verified; retain the pending recovery marker and inspect private host state.",
              file=sys.stderr)
        return 1
    finally:
        if database is not None:
            database.close()
        logging.disable(previous_logging_disable)


if __name__ == "__main__":
    raise SystemExit(main())
