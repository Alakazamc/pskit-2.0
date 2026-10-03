import asyncio
import inspect
import json
import os
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any


class PiRpcError(Exception):
    def __init__(
        self, message: str, *, code: str = "PI_RUN_FAILED", retryable: bool = False,
    ) -> None:
        """Attach a stable error code and retry hint to a Pi RPC failure."""
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _provider_error_code(message: str) -> str:
    """Classify a provider error message into a stable public failure code."""
    lowered = message.lower()
    if "pskit_token_quota_exceeded" in lowered:
        return "TOKEN_QUOTA_EXCEEDED"
    if any(part in lowered for part in (
        "insufficient_quota", "quota exceeded", "out of budget", "insufficient balance",
        "available balance", "余额不足", "额度不足", "billing",
    )):
        return "MODEL_GATEWAY_QUOTA_EXHAUSTED"
    if any(part in lowered for part in (
        "unauthorized", "forbidden", "invalid api key", "invalid_api_key", "401", "403",
    )):
        return "MODEL_GATEWAY_AUTH_FAILED"
    if any(part in lowered for part in ("rate limit", "rate_limit", "too many requests", "429")):
        return "MODEL_GATEWAY_RATE_LIMITED"
    return "PI_RUN_FAILED"


class PiRpcRunner:
    def __init__(
        self,
        *,
        executable: str = "pi",
        session_dir: str = "./data/pi-sessions",
        extension: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        new_api_base_url: str | None = None,
        new_api_model: str | None = None,
        model_gateway_base_url: str | None = None,
        model_gateway_model: str | None = None,
        system_prompt: str | None = None,
        timeout_seconds: float = 180,
    ) -> None:
        """Configure the Pi executable, session storage, model route, and timeout."""
        self.executable = executable
        self.session_dir = session_dir
        self.extension = extension
        self.provider = provider
        self.model = model
        self.new_api_base_url = new_api_base_url
        self.new_api_model = new_api_model
        self.model_gateway_base_url = model_gateway_base_url
        self.model_gateway_model = model_gateway_model
        self.system_prompt = system_prompt
        self.timeout_seconds = timeout_seconds

    async def prompt(
        self,
        session_id: str,
        message: str,
        on_event: Callable[[dict[str, Any]], Any],
        *,
        session_file: str | None = None,
        environment: dict[str, str] | None = None,
        allow_handled: bool = False,
        system_prompt_suffix: str = "",
        images: list[dict[str, str]] | None = None,
    ) -> dict[str, str]:
        """Run one Pi RPC prompt in an isolated session branch and relay events."""
        directory = (Path(self.session_dir) / session_id).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        branch_file: Path | None = None
        active_session_file = session_file
        if session_file is not None:
            committed = Path(session_file).resolve()
            if not committed.is_file():
                raise PiRpcError("Committed Pi session file is missing")
            branch_file = directory / f"attempt-{uuid.uuid4().hex}.jsonl"
            try:
                shutil.copy2(committed, branch_file)
                branch_file.chmod(0o600)
            except BaseException:
                branch_file.unlink(missing_ok=True)
                raise
            active_session_file = str(branch_file)
        child_env = {**os.environ, **(environment or {})}
        gateway_url = self.model_gateway_base_url or self.new_api_base_url
        gateway_model = (environment or {}).get("PSKIT_MODEL_ID") or self.model_gateway_model or self.new_api_model
        provider_name = "model-gateway" if self.model_gateway_base_url else "new-api"
        key_env = "MODEL_GATEWAY_API_KEY"
        config_dir = directory / ".pi-config"
        config_dir.mkdir(parents=True, exist_ok=True)
        retry_settings = {
            "retry": {
                "enabled": True, "maxRetries": 3, "baseDelayMs": 2000,
                "maxAgentDelayMs": 60000, "provider": {"maxRetries": 0},
            }
        }
        self._write_config(config_dir / "settings.json", retry_settings)
        child_env["PI_CODING_AGENT_DIR"] = str(config_dir.resolve())
        if gateway_url:
            if not gateway_model or not child_env.get(key_env):
                raise PiRpcError("Model gateway model or key is missing")
            config = {
                "providers": {
                    provider_name: {
                        "baseUrl": f"{gateway_url.rstrip('/').removesuffix('/v1')}/v1",
                        "api": "openai-completions",
                        "apiKey": f"${key_env}",
                        "models": [{"id": gateway_model, "input": ["text", "image"]
                                    if child_env.get("PSKIT_MODEL_SUPPORTS_IMAGES") == "1"
                                    else ["text"]}],
                    },
                },
            }
            self._write_config(config_dir / "models.json", config)
        args = [
            self.executable, "--mode", "rpc", "--no-builtin-tools", "--no-extensions",
            "--no-skills", "--no-context-files", "--no-prompt-templates", "--offline",
        ]
        args.extend(["--session", active_session_file] if active_session_file
                    else ["--session-dir", str(directory)])
        if self.extension:
            args.extend(["--extension", self.extension])
        if gateway_url:
            args.extend(["--provider", provider_name, "--model", gateway_model])
        else:
            if self.provider:
                args.extend(["--provider", self.provider])
            if self.model:
                args.extend(["--model", self.model])
        full_system_prompt = "\n\n".join(part for part in (self.system_prompt, system_prompt_suffix) if part)
        if full_system_prompt:
            args.extend(["--system-prompt", full_system_prompt])
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=child_env,
                cwd=directory,
            )
        except OSError as exc:
            if branch_file is not None:
                branch_file.unlink(missing_ok=True)
            raise PiRpcError(f"Pi could not start: {exc}") from exc
        assert process.stdin and process.stdout and process.stderr
        stderr_task = asyncio.create_task(process.stderr.read())

        async def write_command(kind: str, **fields: Any) -> str:
            """Write a JSONL RPC command with a unique request ID."""
            command_id = str(uuid.uuid4())
            record = {"id": command_id, "type": kind, **fields}
            process.stdin.write(json.dumps(record, ensure_ascii=False).encode("utf-8") + b"\n")
            await process.stdin.drain()
            return command_id

        async def next_record() -> dict[str, Any]:
            """Read and decode the next JSONL record from the Pi process."""
            line = await process.stdout.readline()
            if not line:
                raise PiRpcError(
                    f"Pi exited before settling (exit={await process.wait()})", retryable=True,
                )
            try:
                return json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise PiRpcError("Pi emitted invalid JSONL") from exc

        async def read_response(command_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
            """Collect events until Pi responds to the requested command."""
            buffered: list[dict[str, Any]] = []
            while True:
                record = await next_record()
                if record.get("type") == "response" and record.get("id") == command_id:
                    if not record.get("success"):
                        raise PiRpcError(str(record.get("error") or "Pi RPC command failed"))
                    return record.get("data") or {}, buffered
                buffered.append(record)

        async def run() -> dict[str, str]:
            """Submit the prompt, consume agent events, and return the session file."""
            state_id = await write_command("get_state")
            state, _ = await read_response(state_id)
            current_file = state.get("sessionFile") or active_session_file
            if branch_file is not None and Path(current_file).resolve() != branch_file:
                raise PiRpcError("Pi did not open the isolated session branch")
            prompt_id = await write_command("prompt", message=message, **(
                {"images": images} if images else {}
            ))
            accepted, early_events = await read_response(prompt_id)
            if accepted.get("disposition") == "handled" and not allow_handled:
                raise PiRpcError("Pi prompt was handled without an agent run")
            deltas: list[str] = []
            final_text: str | None = None
            failed = False
            failure_code = "PI_RUN_FAILED"

            async def consume(record: dict[str, Any]) -> bool:
                """Update the answer and error state, then forward one Pi event."""
                nonlocal final_text, failed, failure_code
                kind = record.get("type")
                if kind == "message_update":
                    update = record.get("assistantMessageEvent") or {}
                    if update.get("type") == "text_delta":
                        deltas.append(str(update.get("delta", "")))
                elif kind == "message_end":
                    message_data = record.get("message") or {}
                    if message_data.get("role") == "assistant":
                        failed = message_data.get("stopReason") in {"error", "aborted"}
                        failure_code = _provider_error_code(
                            str(message_data.get("errorMessage") or "")
                        ) if failed else "PI_RUN_FAILED"
                        content = message_data.get("content") or []
                        text = "".join(
                            block.get("text", "") for block in content if block.get("type") == "text"
                        )
                        if text:
                            final_text = text
                result = on_event(record)
                if inspect.isawaitable(result):
                    await result
                return kind == "agent_settled"

            settled = False
            for record in early_events:
                settled = await consume(record) or settled
            while not settled:
                record = await next_record()
                settled = await consume(record)
                if settled:
                    break
            if failed:
                raise PiRpcError("Pi provider error or abort", code=failure_code)
            if not current_file:
                state_id = await write_command("get_state")
                state, _ = await read_response(state_id)
                current_file = state.get("sessionFile")
            if not current_file:
                raise PiRpcError("Pi did not provide a session file")
            return {"session_file": current_file, "text": final_text or "".join(deltas)}

        try:
            return await asyncio.wait_for(run(), timeout=self.timeout_seconds)
        except TimeoutError as exc:
            if branch_file is not None:
                branch_file.unlink(missing_ok=True)
            raise PiRpcError("Pi RPC deadline exceeded", retryable=True) from exc
        except BaseException:
            if branch_file is not None:
                branch_file.unlink(missing_ok=True)
            raise
        finally:
            if process.returncode is None:
                process.stdin.close()
                try:
                    await asyncio.wait_for(process.wait(), timeout=2)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            await stderr_task

    @staticmethod
    def _write_config(path: Path, data: dict) -> None:
        """Atomically replace a Pi configuration file when its content changes."""
        serialized = json.dumps(data)
        if path.exists() and path.read_text() == serialized:
            return
        temporary = path.parent / f".{path.stem}-{uuid.uuid4().hex}.json"
        try:
            temporary.write_text(serialized)
            temporary.chmod(0o600)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
