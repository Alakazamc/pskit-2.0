#!/usr/bin/env python3
"""Exercise real Staging workspace isolation from inside the backend container."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid

from app.adapters.live.opensandbox_workspace import OpenSandboxWorkspaceProvider
from app.config import Settings
from app.db.postgres import PostgresDatabase
from app.domain.workspace_access import WorkspaceAccessPolicy
from app.domain.workspace_sandboxes import WorkspaceSandboxStore
from app.ports.workspace_sandbox import (
    WorkspaceCommandRequest,
    WorkspaceInvalidPath,
    WorkspaceNotFound,
)


class SmokeFailure(RuntimeError):
    """One required isolation or persistence property was not observed."""


async def _completed(provider, sandbox, request, expected: bytes) -> tuple[int, bool]:
    handle = await provider.commands.start(sandbox, request)
    output = bytearray()
    truncated = False
    exit_code = None
    async for event in provider.commands.events(handle):
        output.extend(event.data)
        truncated = truncated or event.truncated
        if event.type == "exit":
            exit_code = event.exit_code
    if exit_code != 0 or expected not in output:
        raise SmokeFailure("A workspace command did not complete with expected output")
    metrics = await provider.commands.metrics(handle)
    if metrics.wall_ms < 0 or metrics.cpu_core_ms < 0:
        raise SmokeFailure("Workspace command metrics are invalid")
    return metrics.wall_ms, truncated


async def smoke() -> dict[str, object]:
    settings = Settings.from_env()
    settings.validate_workspace_config()
    if settings.workspace_provider != "opensandbox" or settings.database_url == "":
        raise SmokeFailure("Smoke requires the Staging OpenSandbox backend environment")
    database = PostgresDatabase(settings.database_url, schema=settings.database_schema)
    store = WorkspaceSandboxStore(database)
    provider = OpenSandboxWorkspaceProvider(settings, store)
    capabilities = await provider.probe()
    required = (
        capabilities.runtime == "runsc"
        and capabilities.file_access
        and capabilities.command_execution
        and capabilities.command_events
        and capabilities.cancellation
        and capabilities.persistent_volume
        and capabilities.session_mount_namespace
    )
    if not required:
        raise SmokeFailure("Capability probe did not prove the complete command boundary")

    user_a = "staging-workspace-smoke-a"
    user_b = "staging-workspace-smoke-b"
    first_a, second_a = await asyncio.gather(
        provider.ensure_user(user_a), provider.ensure_user(user_a)
    )
    if first_a.sandbox_id != second_a.sandbox_id:
        raise SmokeFailure("Concurrent ensure created two sandboxes for one user")
    sandbox_b = await provider.ensure_user(user_b)
    if sandbox_b.sandbox_id == first_a.sandbox_id:
        raise SmokeFailure("Two users share a sandbox instance")

    session_a = "staging-session-a"
    session_b = "staging-session-b"
    await provider.files.write(first_a, session_a, "files/input.txt", b"alpha")
    read_a = await provider.files.read(
        first_a, session_a, "files/input.txt", offset=0, limit=16
    )
    if read_a.content != b"alpha":
        raise SmokeFailure("Session upload round trip failed")
    try:
        await provider.files.read(first_a, session_b, "files/input.txt", offset=0, limit=16)
    except WorkspaceNotFound:
        pass
    else:
        raise SmokeFailure("A sibling Session could read another Session file")
    try:
        await provider.files.read(sandbox_b, session_a, "files/input.txt", offset=0, limit=16)
    except WorkspaceNotFound:
        pass
    else:
        raise SmokeFailure("A second user could read the first user's file")

    run_id = "smoke-run-" + uuid.uuid4().hex
    artifact_command = (
        "test ! -w /workspace/files && cat /workspace/files/input.txt "
        "&& printf artifact > /workspace/artifacts/result.txt"
    )
    request = WorkspaceCommandRequest(
        session_id=session_a,
        attempt_id="smoke-attempt-" + uuid.uuid4().hex,
        run_id=run_id,
        argv=("/bin/sh", "-c", artifact_command),
        cwd="work",
        env={"HOME": "/workspace/work", "TMPDIR": "/workspace/work/.tmp"},
        timeout_seconds=15,
        max_output_bytes=1024,
    )
    wall_ms, _ = await _completed(provider, first_a, request, b"alpha")
    artifact = await provider.files.read(
        first_a, session_a, "artifacts/result.txt", offset=0, limit=32
    )
    if artifact.content != b"artifact":
        raise SmokeFailure("Command artifact was not persisted")

    truncate = WorkspaceCommandRequest(
        session_id=session_b,
        attempt_id="smoke-attempt-" + uuid.uuid4().hex,
        run_id="smoke-run-" + uuid.uuid4().hex,
        argv=("/usr/bin/python3", "-c", "print('x' * 4096)"),
        cwd="work",
        env={},
        timeout_seconds=15,
        max_output_bytes=64,
    )
    _, truncated = await _completed(provider, first_a, truncate, b"x")
    if not truncated:
        raise SmokeFailure("Command output was not truncated at the declared limit")

    cancel_request = WorkspaceCommandRequest(
        session_id=session_b,
        attempt_id="smoke-attempt-" + uuid.uuid4().hex,
        run_id="smoke-run-" + uuid.uuid4().hex,
        argv=("/bin/sleep", "30"),
        cwd="work",
        env={},
        timeout_seconds=35,
        max_output_bytes=128,
    )
    cancel_handle = await provider.commands.start(first_a, cancel_request)
    cancelled = await provider.commands.cancel(cancel_handle)
    if cancelled.state != "cancelled" or not cancelled.termination_confirmed:
        raise SmokeFailure("Command cancellation lacked supervisor confirmation")

    rejected = WorkspaceCommandRequest(
        session_id=session_b,
        attempt_id="smoke-attempt-" + uuid.uuid4().hex,
        run_id="smoke-run-" + uuid.uuid4().hex,
        argv=("/bin/true",),
        cwd="work",
        env={"PSKIT_SECRET": "forbidden"},
        timeout_seconds=5,
        max_output_bytes=128,
    )
    try:
        await provider.commands.start(first_a, rejected)
    except WorkspaceInvalidPath:
        pass
    else:
        raise SmokeFailure("A reserved environment variable reached the sandbox")

    record = store.get(user_a)
    if record is None:
        raise SmokeFailure("Workspace ownership was not persisted")
    await provider.stop_user(user_a, record.provider_revision)
    stopped = store.get(user_a)
    if stopped is None or stopped.runtime_state != "stopped":
        raise SmokeFailure("Workspace stop was not persisted")
    replacement = await provider.replace_user(user_a, stopped.provider_revision)
    persisted = await provider.files.read(
        replacement, session_a, "artifacts/result.txt", offset=0, limit=32
    )
    if persisted.content != b"artifact":
        raise SmokeFailure("Workspace replacement did not preserve the user volume")

    capability_hash = WorkspaceAccessPolicy.capability_hash(capabilities)
    return {
        "status": "live-ready",
        "runtime": capabilities.runtime,
        "file_access": capabilities.file_access,
        "command_execution": capabilities.command_execution,
        "command_events": capabilities.command_events,
        "cancellation": capabilities.cancellation,
        "persistent_volume": capabilities.persistent_volume,
        "session_mount_namespace": capabilities.session_mount_namespace,
        "capability_hash": capability_hash,
        "session_isolation": True,
        "user_isolation": True,
        "artifact_persistence": True,
        "cancel_confirmed": True,
        "output_truncated": True,
        "replacement_preserved_volume": True,
        "command_wall_ms": wall_ms,
        "evidence_sha256": hashlib.sha256(
            f"{capability_hash}:{first_a.volume_id}".encode()
        ).hexdigest(),
    }


def main() -> int:
    try:
        print(json.dumps(asyncio.run(smoke()), indent=2, sort_keys=True))
        return 0
    except (SmokeFailure, OSError, ValueError) as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
