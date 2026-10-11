"""Private configuration validation for the application-only release controller."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re

from deploy.agent.scripts.production_config import read_env, validate_production_auth
from deploy.agent.scripts.release_files import ReleaseError, safe_read
from deploy.agent.scripts.release_manifest import load_manifest

CONTROL_VERSION = "pskit.control.v1"


def fingerprint(value: dict) -> str:
    """Hash complete resolved JSON with canonical keys; never print the unredacted model."""
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()


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


class _ConfigOperations:
    """Resolve the registered configuration without mutating host infrastructure."""

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
