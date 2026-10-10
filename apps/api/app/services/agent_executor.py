import base64

from app.adapters.live.pi_rpc import PiRpcError
from app.contracts.conversation import (
    RunFailedData,
    RunFailedEvent,
)
from app.domain.catalog import image_mime_type
from app.services.agent_events import AgentProgressWriter


class InitialTurnMixin:
    """Internal AgentService responsibility; the facade preserves its API."""

    async def _execute(self, user_id: str, session_id: str, run_id: str, content: str) -> None:
        """Run an initial Pi turn under its session lock and durable lease.

        Token usage, progress, terminal failures, and the committed Pi branch
        are persisted before the lease is released.
        """
        async with self._locks.hold(session_id):
            prepared = await self._db_call(self._prepare_turn_context, user_id, session_id, run_id)
            if prepared is None:
                return
            context, session_file, environment = prepared
            instructions = context.get("instructions", "")

            progress = AgentProgressWriter(self, user_id, run_id)

            workspace_turn = None
            try:
                async with progress:
                    workspace_turn = await self._open_workspace_attempt(
                        user_id,
                        session_id,
                        run_id,
                        required=bool(context.get("file_ids", [])),
                    )
                    workspace_refs = []
                    if self.workspace_transfer is not None:
                        workspace_refs = await self.workspace_transfer.prepare(
                            user_id,
                            session_id,
                            context.get("file_ids", []),
                        )
                    image_ids = context.get("image_ids", [])
                    images = []
                    for file_id in image_ids:
                        if self.catalog is None:
                            raise RuntimeError("Image catalog is unavailable")
                        file = next(
                            (
                                item
                                for item in (await self._db_call(self.catalog.files_for, user_id))
                                if item.id == file_id
                            ),
                            None,
                        )
                        raw = (
                            (await self._db_call(self.catalog.file_bytes_for, user_id, file_id))
                            if file
                            else None
                        )
                        mime = image_mime_type(file.name, raw) if raw is not None and file else None
                        if mime is None:
                            raise RuntimeError("Run image is unavailable")
                        images.append(
                            {
                                "type": "image",
                                "data": base64.b64encode(raw).decode(),
                                "mimeType": mime,
                            }
                        )
                    result = await self.runner.prompt(
                        session_id,
                        content,
                        progress.on_event,
                        session_file=session_file,
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
                                context=context,
                            )
                        )
                        if workspace_turn
                        else environment,
                        system_prompt_suffix=instructions
                        + (
                            "\nWorkspace uploads:\n"
                            + "\n".join(
                                f"{ref.name}: {ref.relative_path}" for ref in workspace_refs
                            )
                            if workspace_refs
                            else ""
                        )
                        + (
                            f"\nCurrent workspace attempt: {workspace_turn[0]}. "
                            f"Write downloadable files under artifacts/{workspace_turn[0]}/."
                            if workspace_turn
                            else ""
                        ),
                        **({"images": images} if images else {}),
                    )
                    await progress.close()
                    await self._close_workspace_attempt_safely(
                        user_id, session_id, run_id, workspace_turn, succeeded=True
                    )
                    if not (await self._db_call(self.store.owns_lease, run_id, self._instance_id)):
                        return
                    (await self._db_call(self.store.settle_current_tokens, user_id, run_id))
                    waiting = (await self._db_call(self.store.has_any_job_for_run, run_id)) or (
                        await self._db_call(self.store.has_pending_approval_for_run, run_id)
                    )
                    if not result["text"] and not waiting:
                        raise RuntimeError("Pi settled without an answer")
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
                            tool_results=[]
                            if waiting
                            else (await self._db_call(self._mcp_tool_result_parts, run_id)),
                        )
                    )
            except Exception as exc:  # noqa: BLE001 - Persist external Pi failures.
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
                if isinstance(exc, PiRpcError) and exc.retryable and exc.code == "PI_RUN_FAILED":
                    (
                        await self._db_call(
                            self.store.recover_transient_initial_failure,
                            run_id,
                            self._instance_id,
                            self.resume_retry_seconds,
                        )
                    )
                    return
                (await self._db_call(self.store.set_run_status, run_id, "failed"))
                (
                    await self._db_call(
                        self.store.append_event,
                        user_id,
                        run_id,
                        RunFailedEvent(
                            run_id=run_id,
                            data=RunFailedData(
                                code=exc.code if isinstance(exc, PiRpcError) else "PI_RUN_FAILED",
                                message="Pi Agent 执行失败",
                            ),
                        ),
                    )
                )
