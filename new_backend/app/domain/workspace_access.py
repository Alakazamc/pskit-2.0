"""Live server-owned rollout policy for isolated workspace tools."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from app.ports.workspace_sandbox import WorkspaceCapabilities


class WorkspaceAccessConfigurationError(RuntimeError):
    """The live rollout file is missing or malformed, so access must fail closed."""


@dataclass(frozen=True, slots=True)
class WorkspaceRollout:
    enabled: bool
    commands_enabled: bool
    user_allowlist: frozenset[str]
    capability_hash: str

    def includes(self, user_id: str) -> bool:
        return self.enabled and ("*" in self.user_allowlist or user_id in self.user_allowlist)


class WorkspaceAccessPolicy:
    """Read rollout state for every admission decision without trusting the client."""

    def __init__(
        self,
        *,
        enabled: bool,
        commands_enabled: bool,
        user_allowlist_json: str,
        capability_hash: str,
        rollout_file: str = "",
    ) -> None:
        self._defaults = self._parse(
            {
                "enabled": enabled,
                "commands_enabled": commands_enabled,
                "user_allowlist": json.loads(user_allowlist_json),
                "capability_hash": capability_hash,
            }
        )
        self.rollout_file = Path(rollout_file) if rollout_file else None

    @staticmethod
    def capability_hash(capabilities: WorkspaceCapabilities) -> str:
        """Hash the verified command security contract for a release manifest."""
        payload = {
            "cancellation": capabilities.cancellation,
            "command_events": capabilities.command_events,
            "command_execution": capabilities.command_execution,
            "file_access": capabilities.file_access,
            "persistent_volume": capabilities.persistent_volume,
            "provider": capabilities.provider,
            "runtime": capabilities.runtime,
            "session_mount_namespace": capabilities.session_mount_namespace,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _parse(value: object) -> WorkspaceRollout:
        if not isinstance(value, dict):
            raise WorkspaceAccessConfigurationError("Workspace rollout must be a JSON object")
        enabled = value.get("enabled")
        commands_enabled = value.get("commands_enabled")
        users = value.get("user_allowlist")
        capability_hash = value.get("capability_hash", "")
        if not isinstance(enabled, bool) or not isinstance(commands_enabled, bool):
            raise WorkspaceAccessConfigurationError("Workspace rollout flags must be booleans")
        if (
            not isinstance(users, list)
            or any(not isinstance(item, str) or not item for item in users)
            or len(set(users)) != len(users)
        ):
            raise WorkspaceAccessConfigurationError(
                "Workspace rollout user_allowlist must contain unique user IDs"
            )
        if not isinstance(capability_hash, str):
            raise WorkspaceAccessConfigurationError(
                "Workspace rollout capability_hash must be a string"
            )
        if commands_enabled and (not enabled or len(capability_hash) != 64):
            raise WorkspaceAccessConfigurationError(
                "Workspace commands require enabled access and a capability hash"
            )
        return WorkspaceRollout(
            enabled=enabled,
            commands_enabled=commands_enabled,
            user_allowlist=frozenset(users),
            capability_hash=capability_hash,
        )

    def current(self) -> WorkspaceRollout:
        """Return current policy; an unreadable configured file denies all access."""
        if self.rollout_file is None:
            return self._defaults
        try:
            raw = self.rollout_file.read_text(encoding="utf-8")
            return self._parse(json.loads(raw))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkspaceAccessConfigurationError(
                "Workspace rollout file is unavailable"
            ) from exc

    def files_allowed(self, user_id: str) -> bool:
        try:
            return self.current().includes(user_id)
        except WorkspaceAccessConfigurationError:
            return False

    def commands_allowed(
        self, user_id: str, capabilities: WorkspaceCapabilities
    ) -> bool:
        try:
            rollout = self.current()
        except WorkspaceAccessConfigurationError:
            return False
        return (
            rollout.includes(user_id)
            and rollout.commands_enabled
            and rollout.capability_hash == self.capability_hash(capabilities)
        )


__all__ = [
    "WorkspaceAccessConfigurationError",
    "WorkspaceAccessPolicy",
    "WorkspaceRollout",
]
