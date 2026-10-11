"""Private API release transaction: admission ownership, fencing, commit and recovery."""

from __future__ import annotations

import json
import math
import os
import re
import secrets
import time
from pathlib import Path

from deploy.agent.scripts.production_config import read_env
from deploy.agent.scripts.release_files import ReleaseError, atomic_write, safe_read

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


class _ApiOperations:
    """Keep the durable hold and API mutation in one owner-controlled transaction."""

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

    def _qualified_admission(self, manifest: dict) -> None:
        """Never restart or roll back into an image without the same proven guard."""
        adapter = self._admission_config()
        expected = {"component_version": 1, "enforcement_version": "pskit.db-admission.v1",
                    "qualification_sha256": adapter["qualification_sha256"]}
        if manifest.get("admission") != expected:
            raise ReleaseError("The backend Manifest lacks qualified admission enforcement")
        backend = read_env(self.backend_path)
        expected_environment = {
            "RESEARCH_AGENT_RUNTIME": "pi",
            "RESEARCH_AGENT_RELEASE_ADMISSION_ENABLED": "true",
            "RESEARCH_AGENT_RELEASE_ADMISSION_ENVIRONMENT": self.environment,
            "RESEARCH_AGENT_RELEASE_ADMISSION_PROJECT": manifest["compose"]["project"],
            "RESEARCH_AGENT_RELEASE_ADMISSION_QUALIFICATION_SHA256": adapter["qualification_sha256"],
        }
        if any(backend.get(key) != value for key, value in expected_environment.items()) or not (
            32 <= len(backend.get("RESEARCH_AGENT_RELEASE_ADMISSION_KEY", "")) <= 256
        ):
            raise ReleaseError("The backend configuration lacks qualified admission enforcement")
        resolved = self.render(manifest)["services"]["backend"].get("environment", {})
        if any(str(resolved.get(key, "")) != value for key, value in expected_environment.items()) or (
            resolved.get("RESEARCH_AGENT_RELEASE_ADMISSION_KEY")
            != backend["RESEARCH_AGENT_RELEASE_ADMISSION_KEY"]
        ):
            raise ReleaseError("The resolved backend lacks qualified admission enforcement")

    def _admission_command(self, operation: str, pending: dict, manifest: dict) -> None:
        self._qualified_admission(manifest)
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

    def _release_api(
        self,
        pending: dict,
        current_api: dict,
        candidate: dict,
        state: dict,
        cloud_before: bytes,
        image: str,
    ) -> None:
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

    def _recover_api(self, pending: dict, old: dict) -> None:
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
