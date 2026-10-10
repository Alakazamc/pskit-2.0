from app.adapters.live.pi_rpc import PiRpcError
from app.contracts.conversation import (
    RunFailedData,
    RunFailedEvent,
)
from app.domain.quota import TokenQuotaExceeded
from app.services.agent_events import AgentProgressWriter


class ResumeTurnMixin:
    """Internal AgentService responsibility; the facade preserves its API."""

    async def _resume(self, user_id: str, session_id: str, run_id: str, job_id: str) -> None:
        """Wake the original Pi Run from a terminal background-task event.

        The resumed turn reserves Tokens and stops unsafe retries once a tool
        has started. Its result is committed to the original session.
        """
        async with self._locks.hold(session_id):
            if not (await self._db_call(self.store.owns_lease, run_id, self._instance_id)):
                return
            capabilities = getattr(self, "guest_capabilities", None)
            if (
                capabilities is not None
                and (await self._db_call(capabilities.identities.tier_for, user_id)) != "member"
            ):
                (await self._db_call(self.store.set_run_status, run_id, "failed"))
                (
                    await self._db_call(
                        self.store.append_event,
                        user_id,
                        run_id,
                        RunFailedEvent(
                            run_id=run_id,
                            data=RunFailedData(
                                code="LOGIN_REQUIRED",
                                message="登录后才能继续 AF3 分析",
                            ),
                        ),
                    )
                )
                return
            if not (await self._db_call(self.store.start_pi_turn, run_id, self._instance_id)):
                return
            instructions = (await self._db_call(self.store.run_context, run_id)).get(
                "instructions", ""
            )
            estimated_tokens = max(1, (len(instructions) + len(job_id)) // 4)
            try:
                reservation_period = await self._db_call(
                    self.store.reserve_resume_tokens,
                    user_id,
                    run_id,
                    estimated_tokens,
                )
            except TokenQuotaExceeded:
                (await self._db_call(self.store.set_run_status, run_id, "failed"))
                (
                    await self._db_call(
                        self.store.append_event,
                        user_id,
                        run_id,
                        RunFailedEvent(
                            run_id=run_id,
                            data=RunFailedData(
                                code="TOKEN_QUOTA_EXCEEDED",
                                message="本月 Token 额度不足，无法继续分析",
                            ),
                        ),
                    )
                )
                return

            progress = AgentProgressWriter(
                self, user_id, run_id, reservation_period=reservation_period
            )

            workspace_turn = None
            try:
                async with progress:
                    workspace_turn = await self._open_workspace_attempt(
                        user_id,
                        session_id,
                        run_id,
                        required=False,
                    )
                    result = await self.runner.prompt(
                        session_id,
                        f"/pskit_resume {job_id}",
                        progress.on_event,
                        session_file=(
                            await self._db_call(self.store.session_file_for, user_id, session_id)
                        ),
                        environment=(
                            await self._db_call(
                                self._environment,
                                user_id,
                                run_id,
                                session_id=session_id if workspace_turn else None,
                                attempt_id=workspace_turn[0] if workspace_turn else None,
                                workspace_capabilities=workspace_turn[1]
                                if workspace_turn
                                else None,
                            )
                        ),
                        system_prompt_suffix=instructions
                        + (
                            f"\nCurrent workspace attempt: {workspace_turn[0]}. "
                            f"Write downloadable files under artifacts/{workspace_turn[0]}/."
                            if workspace_turn
                            else ""
                        ),
                        allow_handled=True,
                    )
                    await progress.close()
                    await self._close_workspace_attempt_safely(
                        user_id, session_id, run_id, workspace_turn, succeeded=True
                    )
                    if not (await self._db_call(self.store.owns_lease, run_id, self._instance_id)):
                        return
                    (await self._db_call(self.store.settle_current_tokens, user_id, run_id))
                    waiting = (
                        await self._db_call(self.store.has_new_job_for_run, run_id, job_id)
                    ) or (await self._db_call(self.store.has_pending_approval_for_run, run_id))
                    if not result["text"] and not waiting:
                        raise RuntimeError("Pi settled without a resumed answer")
                    (
                        await self._db_call(
                            self.store.finish_pi_turn,
                            user_id,
                            session_id,
                            run_id,
                            self._instance_id,
                            result["session_file"],
                            result["text"],
                            waiting=waiting,
                            resumed_job_id=job_id,
                            tool_results=[]
                            if waiting
                            else (await self._db_call(self._mcp_tool_result_parts, run_id)),
                        )
                    )
            except Exception as exc:  # noqa: BLE001 - Retry recoverable Pi failures only.
                await self._close_workspace_attempt_safely(
                    user_id, session_id, run_id, workspace_turn, succeeded=False
                )
                if not (await self._db_call(self.store.owns_lease, run_id, self._instance_id)):
                    return
                (
                    await self._db_call(
                        self.store.settle_current_tokens,
                        user_id,
                        run_id,
                        refund_if_unreported=isinstance(exc, PiRpcError)
                        and exc.code != "PI_RUN_FAILED",
                    )
                )
                if await self._db_call(self.store.has_started_tool_this_turn, run_id):
                    (await self._db_call(self.store.set_run_status, run_id, "failed"))
                    (
                        await self._db_call(
                            self.store.append_event,
                            user_id,
                            run_id,
                            RunFailedEvent(
                                run_id=run_id,
                                data=RunFailedData(
                                    code="PI_RESUME_UNSAFE_RETRY",
                                    message="工具执行结果不确定，已停止自动重试",
                                ),
                            ),
                        )
                    )
                    return
                if isinstance(exc, PiRpcError) and exc.code != "PI_RUN_FAILED":
                    (await self._db_call(self.store.set_run_status, run_id, "failed"))
                    (
                        await self._db_call(
                            self.store.append_event,
                            user_id,
                            run_id,
                            RunFailedEvent(
                                run_id=run_id,
                                data=RunFailedData(code=exc.code, message="Pi Agent 恢复失败"),
                            ),
                        )
                    )
                    return
                exhausted = await self._db_call(
                    self.store.record_resume_failure, user_id, run_id, self.resume_retry_seconds
                )
                if exhausted:
                    (
                        await self._db_call(
                            self.store.append_event,
                            user_id,
                            run_id,
                            RunFailedEvent(
                                run_id=run_id,
                                data=RunFailedData(
                                    code="PI_RESUME_FAILED",
                                    message="后台任务完成，但 Pi Agent 恢复失败",
                                ),
                            ),
                        )
                    )
