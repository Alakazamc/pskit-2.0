import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import uuid

from app.adapters.live.pi_rpc import PiRpcError
from app.contracts.conversation import (
    MessageDeltaData,
    MessageDeltaEvent,
    MessageEndEvent,
    MessageStartEvent,
    RunFailedData,
    RunFailedEvent,
    RunRetryingData,
    RunRetryingEvent,
    ToolFinishedData,
    ToolFinishedEvent,
    ToolResultPart,
    ToolStartedData,
    ToolStartedEvent,
    ToolUpdatedEvent,
)
from app.domain.catalog import image_mime_type
from app.domain.mcp_tool_calls import McpToolCallStore
from app.domain.persistent_conversation import PersistentConversationStore
from app.domain.quota import TokenQuotaExceeded


class AgentService:
    """Schedule durable Pi Runs, project events, and resume completed jobs."""

    @staticmethod
    def _reported_tokens(event: dict) -> int:
        """Extract positive assistant Token usage from a Pi message-end event."""
        if event.get("type") != "message_end":
            return 0
        message = event.get("message") or {}
        usage = message.get("usage") or {}
        count = usage.get("totalTokens")
        return count if message.get("role") == "assistant" and type(count) is int and count > 0 else 0

    def __init__(
        self, store: PersistentConversationStore, runner, internal_api_url: str,
        mock_af3_seconds: float,
        resume_retry_seconds: float,
        model_gateway_api_key: str | None = None,
        requires_gateway_key: bool = False,
        model_gateway_proxy_enabled: bool = False,
        mcp_tools: list | None = None,
        af3_executor: str = "mock",
        af3_queue_timeout_seconds: int = 3600,
        af3_execution_timeout_seconds: int = 21600,
        max_active_runs: int = 4,
        max_active_runs_per_user: int = 2,
        tool_token_secret: bytes | None = None,
        mcp_tool_calls: McpToolCallStore | None = None,
    ) -> None:
        """Bind the durable store, Pi runner, tool gateway, and scheduler limits.

        Args:
            store: Persistent conversation and quota store.
            runner: Pi RPC adapter used for turns and resumes.
            internal_api_url: Python URL available to Pi extensions.
            mock_af3_seconds: Simulated AF3 job duration.
            resume_retry_seconds: Base retry delay for safe resumes.
            model_gateway_api_key: Server-owned model gateway credential.
            requires_gateway_key: Whether execution requires that credential.
            model_gateway_proxy_enabled: Route Pi model calls through Python.
            mcp_tools: Tools currently discoverable by the Agent.
            af3_executor: Selected mock, callback, or disabled AF3 mode.
            af3_queue_timeout_seconds: Deadline for a worker to claim a job.
            af3_execution_timeout_seconds: Maximum running AF3 duration.
            max_active_runs: Global Pi Run concurrency limit.
            max_active_runs_per_user: Per-user Pi Run concurrency limit.
            tool_token_secret: Shared secret for internal tool request tokens.
            mcp_tool_calls: Durable MCP tool result ledger.
        """
        self.store = store
        self.runner = runner
        self.internal_api_url = internal_api_url
        self.mock_af3_seconds = mock_af3_seconds
        self.resume_retry_seconds = resume_retry_seconds
        self.model_gateway_api_key = model_gateway_api_key
        self.requires_gateway_key = requires_gateway_key
        self.model_gateway_proxy_enabled = model_gateway_proxy_enabled
        self.mcp_tools = mcp_tools or []
        self.af3_executor = af3_executor
        self.af3_queue_timeout_seconds = af3_queue_timeout_seconds
        self.af3_execution_timeout_seconds = af3_execution_timeout_seconds
        self.max_active_runs = max_active_runs
        self.max_active_runs_per_user = max_active_runs_per_user
        self._tool_secret = tool_token_secret or secrets.token_bytes(32)
        self.mcp_tool_calls = mcp_tool_calls
        self.catalog = None
        self._instance_id = uuid.uuid4().hex
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task] = set()
        self._run_tasks: dict[str, asyncio.Task] = {}
        self._scheduler: asyncio.Task | None = None

    async def start(self) -> None:
        """Recover expired Run leases and start the background scheduler once."""
        if self._scheduler is None:
            self.store.recover_wakeups(self.resume_retry_seconds)
            self._scheduler = asyncio.create_task(self._scheduler_loop())

    async def stop(self) -> None:
        """Cancel local tasks and release this instance's durable Run leases."""
        if self._scheduler is not None:
            self._scheduler.cancel()
            try:
                await self._scheduler
            except asyncio.CancelledError:
                pass
            self._scheduler = None
        if self._tasks:
            for task in tuple(self._tasks):
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self.store.release_owned_runs(self._instance_id, self.resume_retry_seconds)

    async def _scheduler_loop(self) -> None:
        """Renew leases, advance jobs, and claim queued or resumable Runs."""
        next_heartbeat = 0.0
        while True:
            now = asyncio.get_running_loop().time()
            if now >= next_heartbeat:
                self.store.recover_wakeups(self.resume_retry_seconds)
                renewed = self.store.renew_leases(self._instance_id, list(self._run_tasks))
                for run_id, task in tuple(self._run_tasks.items()):
                    if run_id not in renewed:
                        task.cancel()
                next_heartbeat = now + 1
            if self.af3_executor == "mock":
                self.store.advance_mock_jobs(self.mock_af3_seconds)
            elif self.af3_executor == "callback":
                self.store.expire_queued_compute_jobs(self.af3_queue_timeout_seconds)
                self.store.expire_running_compute_jobs(self.af3_execution_timeout_seconds)
            for run_id, user_id, session_id, job_id in self.store.claim_wakeups(
                self._instance_id, max_active=self.max_active_runs,
                max_user_active=self.max_active_runs_per_user,
            ):
                self._track(run_id, self._resume(user_id, session_id, run_id, job_id))
            for run_id, user_id, session_id, content in self.store.claim_queued_runs(
                self._instance_id, max_active=self.max_active_runs,
                max_user_active=self.max_active_runs_per_user,
            ):
                self._track(run_id, self._execute(user_id, session_id, run_id, content))
            await asyncio.sleep(0.05)

    def _track(self, run_id: str, coro) -> None:
        """Start a Run task and remove it from local tracking on completion."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        self._run_tasks[run_id] = task
        task.add_done_callback(lambda completed: self._run_tasks.pop(run_id, None)
                               if self._run_tasks.get(run_id) is completed else None)

    def cancel_run(self, run_id: str) -> None:
        """Cancel a locally running Pi task after the durable Run is cancelled."""
        task = self._run_tasks.get(run_id)
        if task is not None:
            task.cancel()

    def schedule(self, user_id: str, session_id: str, run_id: str, content: str) -> None:
        """Claim a newly admitted Run immediately when capacity is free.

        The scheduler will pick it up later if this claim cannot proceed.
        """
        if self.store.claim_initial_run(
            run_id, self._instance_id, max_active=self.max_active_runs,
            max_user_active=self.max_active_runs_per_user,
        ):
            self._track(run_id, self._execute(user_id, session_id, run_id, content))

    def has_model_token(self, user_id: str) -> bool:
        """Check service-level gateway credentials when live Pi requires them."""
        return not self.requires_gateway_key or bool(self.model_gateway_api_key)

    def _environment(self, user_id: str, run_id: str) -> dict[str, str]:
        """Build Pi extension variables with tier-filtered tools and scoped auth.

        Args:
            user_id: Run owner whose capability policy applies.
            run_id: Run used to scope internal tool and model proxy tokens.

        Returns:
            Server-only environment variables for the Pi subprocess.
        """
        allowed = set(self.store.run_context(run_id).get("allowed_tools", []))
        capabilities = getattr(self, "guest_capabilities", None)
        if capabilities is not None:
            allowed = {name for name in allowed if capabilities.mcp_allowed_for(user_id, name)}
        environment = {
            "PSKIT_USER_ID": user_id,
            "PSKIT_RUN_ID": run_id,
            "PSKIT_AGENT_TOOL_TOKEN": self.tool_token(run_id),
            "PSKIT_INTERNAL_API_URL": self.internal_api_url,
            "PSKIT_AF3_ENABLED": "1" if self.af3_executor != "disabled"
            and (capabilities is None or capabilities.identities.tier_for(user_id) == "member")
            else "0",
            "PSKIT_MCP_TOOLS_JSON": json.dumps([
                tool.model_dump() for tool in self.mcp_tools if tool.name in allowed
            ]),
        }
        model_id = self.store.run_context(run_id).get("model_id")
        if model_id:
            environment["PSKIT_MODEL_ID"] = model_id
        if self.store.run_context(run_id).get("model_supports_images") is True:
            environment["PSKIT_MODEL_SUPPORTS_IMAGES"] = "1"
        if self.model_gateway_api_key is not None:
            environment["MODEL_GATEWAY_API_KEY"] = (
                f"{run_id}.{self.tool_token(run_id)}" if self.model_gateway_proxy_enabled
                else self.model_gateway_api_key
            )
        return environment

    def tool_token(self, run_id: str) -> str:
        """Derive a Run-scoped HMAC token for Python internal tool routes."""
        return hmac.new(self._tool_secret, run_id.encode(), hashlib.sha256).hexdigest()

    def verify_tool_token(self, run_id: str, token: str) -> bool:
        """Compare an internal tool token without leaking timing differences."""
        return secrets.compare_digest(self.tool_token(run_id), token)

    def _mcp_tool_result_parts(self, run_id: str) -> list[ToolResultPart]:
        """Project durable completed MCP calls into typed answer parts."""
        if self.mcp_tool_calls is None:
            return []
        return [ToolResultPart(tool_call_id=call_id, tool=name, result=record.result)
                for call_id, name, record in self.mcp_tool_calls.completed_for_run(run_id)]

    def _record_progress_event(self, user_id: str, run_id: str, event: dict) -> None:
        """Translate Pi stream updates into the public typed Run event log."""
        if event.get("type") in {"message_start", "message_end"}:
            message = event.get("message")
            if isinstance(message, dict) and message.get("role") == "assistant":
                projected = (MessageStartEvent(run_id=run_id) if event["type"] == "message_start"
                             else MessageEndEvent(run_id=run_id))
                self.store.append_event(user_id, run_id, projected)
        elif event.get("type") == "message_update":
            update = event.get("assistantMessageEvent", {})
            if update.get("type") == "text_delta":
                self.store.append_event(
                    user_id, run_id,
                    MessageDeltaEvent(run_id=run_id, data=MessageDeltaData(delta=update["delta"])),
                )
        elif event.get("type") == "auto_retry_start":
            attempt, maximum, delay = (event.get("attempt"), event.get("maxAttempts"),
                                       event.get("delayMs"))
            if all(type(value) is int for value in (attempt, maximum, delay)):
                self.store.append_event(
                    user_id, run_id,
                    RunRetryingEvent(run_id=run_id, data=RunRetryingData(
                        attempt=attempt, max_attempts=maximum, delay_ms=delay,
                    )),
                )
        elif event.get("type") in {"tool_execution_start", "tool_execution_update", "tool_execution_end"}:
            tool_call_id, tool = event.get("toolCallId"), event.get("toolName")
            if not isinstance(tool_call_id, str) or not isinstance(tool, str):
                return
            common = ToolStartedData(tool_call_id=tool_call_id, tool=tool)
            if event["type"] == "tool_execution_start":
                projected = ToolStartedEvent(run_id=run_id, data=common)
            elif event["type"] == "tool_execution_update":
                projected = ToolUpdatedEvent(run_id=run_id, data=common)
            else:
                details = (event.get("result") or {}).get("details") or {}
                state = details.get("status") if isinstance(details, dict) else None
                status = ("failed" if event.get("isError") else
                          state if state in {"pending", "approval_required"} else "completed")
                summary = {
                    "failed": "Tool failed", "pending": "Background task submitted",
                    "approval_required": "Waiting for approval", "completed": "Tool completed",
                }[status]
                projected = ToolFinishedEvent(run_id=run_id, data=ToolFinishedData(
                    tool_call_id=tool_call_id, tool=tool, status=status, summary=summary,
                ))
            self.store.append_event(user_id, run_id, projected)

    async def _execute(self, user_id: str, session_id: str, run_id: str, content: str) -> None:
        """Run an initial Pi turn under its session lock and durable lease.

        Token usage, progress, terminal failures, and the committed Pi branch
        are persisted before the lease is released.
        """
        async with self._locks.setdefault(session_id, asyncio.Lock()):
            if not self.store.owns_lease(run_id, self._instance_id):
                return
            if not self.store.start_pi_turn(run_id, self._instance_id):
                return
            instructions = self.store.run_context(run_id).get("instructions", "")

            async def on_event(event: dict) -> None:
                """Record provider usage and public progress for a live lease."""
                if not self.store.owns_lease(run_id, self._instance_id):
                    return
                tokens = self._reported_tokens(event)
                if tokens:
                    message = event["message"]
                    self.store.record_model_attempt(
                        user_id, run_id, tokens,
                        "error" if message.get("stopReason") in {"error", "aborted"} else "completed",
                    )
                elif event.get("type") == "message_end" and (event.get("message") or {}).get("role") == "assistant":
                    self.store.record_unreported_model_call(user_id, run_id)
                self._record_progress_event(user_id, run_id, event)

            try:
                image_ids = self.store.run_context(run_id).get("image_ids", [])
                images = []
                for file_id in image_ids:
                    if self.catalog is None:
                        raise RuntimeError("Image catalog is unavailable")
                    file = next((item for item in self.catalog.files_for(user_id)
                                 if item.id == file_id), None)
                    raw = self.catalog.file_bytes_for(user_id, file_id) if file else None
                    mime = image_mime_type(file.name, raw) if raw is not None and file else None
                    if mime is None:
                        raise RuntimeError("Run image is unavailable")
                    images.append({"type": "image", "data": base64.b64encode(raw).decode(),
                                   "mimeType": mime})
                result = await self.runner.prompt(
                    session_id, content, on_event,
                    session_file=self.store.session_file_for(user_id, session_id),
                    environment=self._environment(user_id, run_id),
                    system_prompt_suffix=instructions,
                    **({"images": images} if images else {}),
                )
                if not self.store.owns_lease(run_id, self._instance_id):
                    return
                self.store.settle_current_tokens(user_id, run_id)
                waiting = (self.store.has_any_job_for_run(run_id)
                           or self.store.has_pending_approval_for_run(run_id))
                if not result["text"] and not waiting:
                    raise RuntimeError("Pi settled without an answer")
                self.store.finish_pi_turn(
                    user_id, session_id, run_id, self._instance_id,
                    result["session_file"], result["text"], waiting=waiting,
                    tool_results=[] if waiting else self._mcp_tool_result_parts(run_id),
                )
            except Exception as exc:  # noqa: BLE001 - Persist external Pi failures.
                if not self.store.owns_lease(run_id, self._instance_id):
                    return
                self.store.settle_current_tokens(
                    user_id, run_id,
                    refund_if_unreported=isinstance(exc, PiRpcError) and exc.code != "PI_RUN_FAILED",
                )
                if isinstance(exc, PiRpcError) and exc.retryable and exc.code == "PI_RUN_FAILED":
                    self.store.recover_transient_initial_failure(
                        run_id, self._instance_id, self.resume_retry_seconds,
                    )
                    return
                self.store.set_run_status(run_id, "failed")
                self.store.append_event(
                    user_id, run_id,
                    RunFailedEvent(
                        run_id=run_id,
                        data=RunFailedData(
                            code=exc.code if isinstance(exc, PiRpcError) else "PI_RUN_FAILED",
                            message="Pi Agent 执行失败",
                        ),
                    ),
                )

    async def _resume(self, user_id: str, session_id: str, run_id: str, job_id: str) -> None:
        """Wake a waiting Pi Run after an AF3 job completes.

        The resumed turn reserves Tokens and stops unsafe retries once a tool
        has started. Its result is committed to the original session.
        """
        async with self._locks.setdefault(session_id, asyncio.Lock()):
            if not self.store.owns_lease(run_id, self._instance_id):
                return
            capabilities = getattr(self, "guest_capabilities", None)
            if capabilities is not None and capabilities.identities.tier_for(user_id) != "member":
                self.store.set_run_status(run_id, "failed")
                self.store.append_event(
                    user_id, run_id,
                    RunFailedEvent(run_id=run_id, data=RunFailedData(
                        code="LOGIN_REQUIRED", message="登录后才能继续 AF3 分析",
                    )),
                )
                return
            if not self.store.start_pi_turn(run_id, self._instance_id):
                return
            instructions = self.store.run_context(run_id).get("instructions", "")
            estimated_tokens = max(1, (len(instructions) + len(job_id)) // 4)
            try:
                reservation_period = self.store.reserve_resume_tokens(
                    user_id, run_id, estimated_tokens,
                )
            except TokenQuotaExceeded:
                self.store.set_run_status(run_id, "failed")
                self.store.append_event(
                    user_id, run_id,
                    RunFailedEvent(
                        run_id=run_id,
                        data=RunFailedData(
                            code="TOKEN_QUOTA_EXCEEDED", message="本月 Token 额度不足，无法继续分析",
                        ),
                    ),
                )
                return

            async def on_event(event: dict) -> None:
                """Charge observed resumed-turn usage and project Pi events."""
                if not self.store.owns_lease(run_id, self._instance_id):
                    return
                tokens = self._reported_tokens(event)
                if tokens:
                    message = event["message"]
                    self.store.record_model_attempt(
                        user_id, run_id, tokens,
                        "error" if message.get("stopReason") in {"error", "aborted"} else "completed",
                        period_override=reservation_period,
                    )
                elif event.get("type") == "message_end" and (event.get("message") or {}).get("role") == "assistant":
                    self.store.record_unreported_model_call(user_id, run_id)
                self._record_progress_event(user_id, run_id, event)

            try:
                result = await self.runner.prompt(
                    session_id, f"/pskit_resume {job_id}", on_event,
                    session_file=self.store.session_file_for(user_id, session_id),
                    environment=self._environment(user_id, run_id),
                    system_prompt_suffix=instructions,
                    allow_handled=True,
                )
                if not self.store.owns_lease(run_id, self._instance_id):
                    return
                self.store.settle_current_tokens(user_id, run_id)
                waiting = (self.store.has_new_job_for_run(run_id, job_id)
                           or self.store.has_pending_approval_for_run(run_id))
                if not result["text"] and not waiting:
                    raise RuntimeError("Pi settled without a resumed answer")
                self.store.finish_pi_turn(
                    user_id, session_id, run_id, self._instance_id,
                    result["session_file"], result["text"], waiting=waiting,
                    resumed_job_id=job_id,
                    tool_results=[] if waiting else self._mcp_tool_result_parts(run_id),
                )
            except Exception as exc:  # noqa: BLE001 - Retry recoverable Pi failures only.
                if not self.store.owns_lease(run_id, self._instance_id):
                    return
                self.store.settle_current_tokens(
                    user_id, run_id,
                    refund_if_unreported=isinstance(exc, PiRpcError) and exc.code != "PI_RUN_FAILED",
                )
                if self.store.has_started_tool_this_turn(run_id):
                    self.store.set_run_status(run_id, "failed")
                    self.store.append_event(
                        user_id, run_id,
                        RunFailedEvent(run_id=run_id, data=RunFailedData(
                            code="PI_RESUME_UNSAFE_RETRY",
                            message="工具执行结果不确定，已停止自动重试",
                        )),
                    )
                    return
                if isinstance(exc, PiRpcError) and exc.code != "PI_RUN_FAILED":
                    self.store.set_run_status(run_id, "failed")
                    self.store.append_event(
                        user_id, run_id,
                        RunFailedEvent(
                            run_id=run_id,
                            data=RunFailedData(code=exc.code, message="Pi Agent 恢复失败"),
                        ),
                    )
                    return
                exhausted = self.store.record_resume_failure(
                    user_id, run_id, self.resume_retry_seconds
                )
                if exhausted:
                    self.store.append_event(
                        user_id, run_id,
                        RunFailedEvent(
                            run_id=run_id,
                            data=RunFailedData(code="PI_RESUME_FAILED", message="后台任务完成，但 Pi Agent 恢复失败"),
                        ),
                    )
