"""One Pi attempt per session with queryable, exit-confirmed terminal states."""

import asyncio
import os
from pathlib import Path

from app.contracts.sandbox import SandboxAttemptStatus, SandboxPromptRequest
from app.domain.sandboxes import SandboxConflict

PI_ENVIRONMENT_KEYS = frozenset(
    {
        "PSKIT_USER_ID",
        "PSKIT_RUN_ID",
        "PSKIT_AGENT_TOOL_TOKEN",
        "PSKIT_MODEL_ID",
        "PSKIT_MODEL_SUPPORTS_IMAGES",
        "PSKIT_REASONING_EFFORT",
        "PSKIT_AF3_ENABLED",
        "PSKIT_MCP_TOOLS_JSON",
        "PSKIT_COMPUTE_CAPABILITIES_JSON",
        "MODEL_GATEWAY_API_KEY",
    }
)
PI_HOST_ENVIRONMENT_KEYS = frozenset({"PATH", "LANG", "LC_ALL", "TZ"})


def pi_environment(environment):
    """Accept only backend Run scoped credentials and inject the controlled gateway."""
    result = {key: value for key, value in environment.items() if key in PI_ENVIRONMENT_KEYS}
    key = result.get("MODEL_GATEWAY_API_KEY")
    run_id, tool_token = result.get("PSKIT_RUN_ID"), result.get("PSKIT_AGENT_TOOL_TOKEN")
    if key and (not run_id or not tool_token or key != f"{run_id}.{tool_token}"):
        raise ValueError("Sandbox model credentials must be Run scoped")
    gateway = os.environ.get("RESEARCH_AGENT_INTERNAL_API_URL", "http://sandbox-gateway:8080")
    result["PSKIT_INTERNAL_API_URL"] = gateway
    return result


class SandboxSessionCoordinator:
    def __init__(self, session_root: Path, runner_factory):
        self.session_root = session_root
        self.runner_factory = runner_factory
        self._locks = {}
        self._attempts = {}
        self._tasks = {}

    def status(self, attempt_id) -> SandboxAttemptStatus:
        if attempt_id not in self._attempts:
            raise SandboxConflict("Attempt is unavailable")
        return self._attempts[attempt_id].model_copy()

    def activity(self) -> list[SandboxAttemptStatus]:
        return [status.model_copy() for status in self._attempts.values()]

    def start_transfer(self, session_id, attempt_id):
        if attempt_id in self._attempts:
            raise SandboxConflict("Attempt ID was already used")
        self._attempts[attempt_id] = SandboxAttemptStatus(
            attempt_id=attempt_id, session_id=session_id, state="running"
        )
        self._tasks[attempt_id] = asyncio.current_task()

    def finish_transfer(self, attempt_id, state="completed"):
        self._attempts[attempt_id].state = state
        self._attempts[attempt_id].exited = True
        self._tasks.pop(attempt_id, None)

    async def cancel(self, attempt_id) -> SandboxAttemptStatus:
        status = self.status(attempt_id)
        if not status.exited and status.state != "cancelling":
            self._attempts[attempt_id] = status.model_copy(update={"state": "cancelling"})
            self._tasks[attempt_id].cancel()
        return self.status(attempt_id)

    async def prompt(self, request: SandboxPromptRequest, on_event):
        if request.attempt_id in self._attempts:
            raise SandboxConflict("Attempt ID was already used")
        self._attempts[request.attempt_id] = SandboxAttemptStatus(
            attempt_id=request.attempt_id, session_id=request.session_id, state="queued"
        )
        self._tasks[request.attempt_id] = asyncio.current_task()
        state = "failed"
        try:
            async with self._locks.setdefault(request.session_id, asyncio.Lock()):
                self._attempts[request.attempt_id].state = "running"
                directory = self.session_root / request.session_id
                from app.services.workspace_transfer import LocalWorkspace

                workspace = LocalWorkspace(self.session_root)
                for parts in (
                    [request.session_id, "attempts", request.attempt_id],
                    [request.session_id, "artifacts", request.attempt_id],
                    [request.session_id, ".pi"],
                ):
                    with workspace.directory(parts, create=True):
                        pass
                attempt_dir = directory / "attempts" / request.attempt_id
                environment = pi_environment(request.environment)
                environment["PSKIT_ATTEMPT_DIR"] = str(attempt_dir)
                environment["PSKIT_ARTIFACT_DIR"] = str(
                    directory / "artifacts" / request.attempt_id
                )
                result = await self.runner_factory(request.model).prompt(
                    request.session_id,
                    request.message,
                    on_event,
                    session_file=request.session_file,
                    environment=environment,
                    allow_handled=request.allow_handled,
                    system_prompt_suffix=request.system_prompt_suffix,
                    working_directory=str(directory),
                    transcript_directory=str(directory / ".pi"),
                    isolated_environment=True,
                    **({"images": request.images} if request.images else {}),
                )
                state = "completed"
                return result
        except asyncio.CancelledError:
            state = "cancelled"
            raise
        finally:
            # Runner returns/raises only after its subprocess wait has completed.
            self._attempts[request.attempt_id].state = state
            self._attempts[request.attempt_id].exited = True
            self._tasks.pop(request.attempt_id, None)
