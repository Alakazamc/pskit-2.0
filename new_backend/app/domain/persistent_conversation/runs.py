"""Runs persistence methods for the conversation store."""

import json
import uuid
from datetime import datetime, timedelta

from pydantic import TypeAdapter

from app.contracts.conversation import (
    ApprovalResolvedData,
    ApprovalResolvedEvent,
    ArtifactCreatedEvent,
    ArtifactPart,
    FilePart,
    Message,
    MessagePart,
    MessageRequest,
    PlanCreatedEvent,
    PlanSnapshot,
    PlanUpdatedEvent,
    RunCancelledData,
    RunCancelledEvent,
    RunCompletedData,
    RunCompletedEvent,
    RunEvent,
    RunRef,
    RunStatus,
    TextPart,
    ToolCallPart,
    ToolFinishedEvent,
    ToolResultPart,
    ToolStartedEvent,
)
from app.domain.conversation import IdempotencyConflict
from app.domain.quota import TokenQuotaExceeded

from .common import MESSAGE_PARTS_ADAPTER
from .common import current_time as _now


class RunsMixin:
    """Persist messages, Pi Run leases, event streams, and session branches."""

    def set_run_context(
        self, run_id: str, instructions: str, allowed_tools: tuple[str, ...],
        user_prompt: str = "",
    ) -> None:
        """Save the prompt context used when a queued Run starts.

        Args:
            run_id: Run whose context is updated.
            instructions: Resolved Skill and system instructions.
            allowed_tools: Tool names exposed to this Run.
            user_prompt: Original user request to replay after queueing.
        """
        with self.db:
            self.db.execute(
                "UPDATE agent_runs SET context_json=? WHERE id=?",
                (json.dumps({"instructions": instructions, "allowed_tools": allowed_tools,
                             "user_prompt": user_prompt}), run_id),
            )

    def claim_initial_run(
        self, run_id: str, owner: str = "local", lease_seconds: int = 10,
        *, max_active: int = 4, max_user_active: int = 2,
    ) -> bool:
        """Claim a queued Run under global, user, and session limits.

        Args:
            run_id: Queued Run to claim.
            owner: Worker identity stored with the lease.
            lease_seconds: Lease duration.
            max_active: Global active Run limit.
            max_user_active: Default active Run limit per user.

        Returns:
            Whether this worker atomically claimed the Run.
        """
        with self._immediate_transaction():
            row = self.db.execute(
                "SELECT user_id,status FROM agent_runs WHERE id=?", (run_id,),
            ).fetchone()
            if row is None or row[1] != "queued":
                return False
            now = _now().isoformat()
            active = self.db.execute(
                "SELECT COUNT(*) FROM agent_runs WHERE status IN ('running','resume_queued') "
                "AND lease_expires_at>?", (now,),
            ).fetchone()[0]
            user_active = self.db.execute(
                "SELECT COUNT(*) FROM agent_runs WHERE user_id=? "
                "AND status IN ('running','resume_queued') AND lease_expires_at>?",
                (row[0], now),
            ).fetchone()[0]
            if active >= max_active or user_active >= self._max_active_for_user(
                row[0], max_user_active,
            ):
                return False
            return bool(self.db.execute(
                "UPDATE agent_runs SET status='running',lease_owner=?,lease_expires_at=? "
                "WHERE id=? AND status='queued' "
                "AND NOT EXISTS (SELECT 1 FROM agent_runs active WHERE "
                "active.session_id=agent_runs.session_id AND active.id<>agent_runs.id "
                "AND active.status IN ('running','resume_queued'))",
                (owner, (_now() + timedelta(seconds=lease_seconds)).isoformat(), run_id),
            ).rowcount)

    def _max_active_for_user(self, user_id: str, configured_limit: int) -> int:
        """Apply a stricter active Run limit to guest accounts."""
        lookup = getattr(self, "admin_concurrency_limit_for", None)
        admin_limit = lookup(user_id) if lookup else None
        limit = min(configured_limit, admin_limit) if admin_limit is not None else configured_limit
        policy = getattr(self, "identity_policy", None)
        if policy is not None and policy.tier_for(user_id) == "guest":
            return min(limit, policy.guest_max_active_runs)
        return limit

    def claim_queued_runs(
        self, owner: str = "local", lease_seconds: int = 10, limit: int = 8,
        *, max_active: int = 4, max_user_active: int = 2,
    ) -> list[tuple[str, str, str, str]]:
        """Claim ready initial Runs in creation order up to the batch limit.

        Args:
            owner: Worker identity stored with each lease.
            lease_seconds: Lease duration.
            limit: Maximum Runs to claim in this batch.
            max_active: Global active Run limit.
            max_user_active: Default active Run limit per user.

        Returns:
            Tuples of Run ID, user ID, session ID, and original prompt.
        """
        rows = self.db.execute(
            "SELECT id,user_id,session_id,context_json FROM agent_runs "
            "WHERE status='queued' AND (retry_after IS NULL OR retry_after<=?) "
            "ORDER BY created_at,rowid", (_now().isoformat(),),
        ).fetchall()
        claimed: list[tuple[str, str, str, str]] = []
        for run_id, user_id, session_id, context_json in rows:
            if len(claimed) >= limit:
                break
            if self.claim_initial_run(run_id, owner, lease_seconds,
                                      max_active=max_active, max_user_active=max_user_active):
                claimed.append((run_id, user_id, session_id,
                                json.loads(context_json).get("user_prompt", "")))
        return claimed

    def renew_leases(self, owner: str, run_ids: list[str], lease_seconds: int = 10) -> set[str]:
        """Extend active Run leases owned by a worker.

        Args:
            owner: Worker expected to hold the leases.
            run_ids: Runs to renew.
            lease_seconds: New lease duration.

        Returns:
            IDs whose lease rows were updated.
        """
        renewed: set[str] = set()
        with self.db:
            for run_id in run_ids:
                changed = self.db.execute(
                    "UPDATE agent_runs SET lease_expires_at=? WHERE id=? AND lease_owner=? "
                    "AND status IN ('running','resume_queued')",
                    ((_now() + timedelta(seconds=lease_seconds)).isoformat(), run_id, owner),
                ).rowcount
                if changed:
                    renewed.add(run_id)
        return renewed

    def owns_lease(self, run_id: str, owner: str) -> bool:
        """Return whether a worker has a live lease on an active Run."""
        row = self.db.execute(
            "SELECT 1 FROM agent_runs WHERE id=? AND lease_owner=? "
            "AND lease_expires_at>? AND status IN ('running','resume_queued')",
            (run_id, owner, _now().isoformat()),
        ).fetchone()
        return row is not None

    def run_context(self, run_id: str) -> dict:
        """Return saved Run prompt context, or an empty mapping if absent."""
        row = self.db.execute("SELECT context_json FROM agent_runs WHERE id=?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else {}

    def messages_for(self, user_id: str, session_id: str) -> list[Message] | None:
        """Read ordered messages with typed parts from an owned session.

        Args:
            user_id: Session owner.
            session_id: Session whose transcript is requested.

        Returns:
            Messages, or ``None`` when the session is unavailable.
        """
        if not self._owns_session(user_id, session_id):
            return None
        rows = self.db.execute(
            "SELECT id, role, content, created_at, parts_json FROM agent_messages "
            "WHERE user_id=? AND session_id=? ORDER BY created_at, rowid",
            (user_id, session_id),
        ).fetchall()
        return [
            Message(
                id=row[0], session_id=session_id, role=row[1],
                parts=(MESSAGE_PARTS_ADAPTER.validate_json(row[4]) if row[4]
                       else [TextPart(text=row[2])]),
                created_at=datetime.fromisoformat(row[3])
            )
            for row in rows
        ]

    def send_message(
        self, user_id: str, session_id: str, payload: MessageRequest, *, waiting: bool = False
    ) -> RunRef | None:
        """Append a user message and a queued Run in one transaction.

        This direct path does not reserve Tokens; quota-admitted requests use
        :meth:`accept_message`.

        Args:
            user_id: Session owner.
            session_id: Destination session.
            payload: User text and attachments.
            waiting: Compatibility argument; this path always queues the Run.

        Returns:
            New Run reference, or ``None`` when the session is unavailable.

        Raises:
            GuestAccountDeleting: Guest cleanup has claimed the account.
        """
        if not self._owns_session(user_id, session_id):
            return None
        run_id = str(uuid.uuid4())
        with self._immediate_transaction():
            self._require_not_deleting(user_id)
            self.db.execute(
                "INSERT INTO agent_messages "
                "(id,user_id,session_id,role,content,created_at,parts_json) "
                "VALUES (?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), user_id, session_id, "user", payload.content,
                 _now().isoformat(), self._user_parts_json(payload)),
            )
            self.db.execute(
                "INSERT INTO agent_runs (id,user_id,session_id,status,created_at) VALUES (?, ?, ?, ?, ?)",
                (run_id, user_id, session_id, "queued", _now().isoformat()),
            )
        return RunRef(run_id=run_id)

    def accept_message(
        self, user_id: str, session_id: str, payload: MessageRequest,
        quota, estimated_tokens: int, idempotency_key: str | None, request_hash: str,
        *, waiting: bool = False, instructions: str = "",
        allowed_tools: tuple[str, ...] = (), user_prompt: str = "", model_id: str = "",
        image_ids: tuple[str, ...] = (),
        model_supports_images: bool = False,
        reasoning_effort: str | None = None,
    ) -> tuple[RunRef, bool] | None:
        """Admit a message, reserve Tokens, and queue its Run atomically.

        A matching idempotency key returns the prior Run without another
        message or reservation. The quota and ``waiting`` arguments remain
        for the public store interface; limits come from persisted policy.

        Args:
            user_id: Session owner.
            session_id: Destination session.
            payload: User message and attachment references.
            quota: Compatibility argument; persisted limits are authoritative.
            estimated_tokens: Initial Token hold for the Run.
            idempotency_key: Optional client request key.
            request_hash: Fingerprint used to reject key reuse with new input.
            waiting: Compatibility argument; the Run starts queued.
            instructions: Saved Skill and system instructions.
            allowed_tools: Tools exposed when the Run executes.
            user_prompt: Original prompt for queue recovery.

        Returns:
            Run and whether it is new, or ``None`` when the session is absent.

        Raises:
            GuestAccountDeleting: Guest cleanup has claimed the account.
            IdempotencyConflict: A key was reused with different input.
            TokenQuotaExceeded: The monthly allowance cannot cover the hold.
        """
        del quota, waiting
        period = _now().strftime("%Y-%m")
        with self._immediate_transaction():
            self._require_not_deleting(user_id)
            if not self._owns_session(user_id, session_id):
                return None
            if idempotency_key:
                existing = self.db.execute(
                    "SELECT request_hash,run_id FROM agent_request_keys "
                    "WHERE user_id=? AND session_id=? AND key=?",
                    (user_id, session_id, idempotency_key),
                ).fetchone()
                if existing:
                    if existing[0] != request_hash:
                        raise IdempotencyConflict
                    return RunRef(run_id=existing[1]), False
            used_row = self.db.execute(
                "SELECT used FROM agent_token_usage WHERE user_id=? AND period=?",
                (user_id, period),
            ).fetchone()
            used = used_row[0] if used_row else 0
            limit = self._token_limit_for(user_id)
            if used + estimated_tokens > limit:
                raise TokenQuotaExceeded
            self.db.execute(
                "INSERT INTO agent_token_usage VALUES (?,?,?) "
                "ON CONFLICT(user_id,period) DO UPDATE SET used=excluded.used",
                (user_id, period, used + estimated_tokens),
            )
            run_id = str(uuid.uuid4())
            self.db.execute(
                "INSERT INTO agent_messages "
                "(id,user_id,session_id,role,content,created_at,parts_json) "
                "VALUES (?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), user_id, session_id, "user", payload.content,
                 _now().isoformat(), self._user_parts_json(payload)),
            )
            self.db.execute(
                "INSERT INTO agent_runs "
                "(id,user_id,session_id,status,created_at,context_json) VALUES (?,?,?,?,?,?)",
                (run_id, user_id, session_id, "queued", _now().isoformat(),
                 json.dumps({"instructions": instructions, "allowed_tools": allowed_tools,
                             "user_prompt": user_prompt, "model_id": model_id,
                             "model_supports_images": model_supports_images,
                             "reasoning_effort": reasoning_effort,
                             "image_ids": list(image_ids),
                             "file_ids": [item.id for item in payload.attachments]})),
            )
            if idempotency_key:
                self.db.execute(
                    "INSERT INTO agent_request_keys VALUES (?,?,?,?,?)",
                    (user_id, session_id, idempotency_key, request_hash, run_id),
                )
            self._append_token_entry(user_id, period, "reservation", estimated_tokens, run_id)
        return RunRef(run_id=run_id), True

    def existing_message_for(
        self, user_id: str, session_id: str, idempotency_key: str | None,
        request_hash: str,
    ) -> RunRef | None:
        """Resolve a prior message request by its idempotency key.

        Args:
            user_id: Session owner.
            session_id: Session receiving the request.
            idempotency_key: Optional client request key.
            request_hash: Fingerprint of the current request.

        Returns:
            Prior Run, or ``None`` when no key or record exists.

        Raises:
            IdempotencyConflict: The key belongs to a different request.
        """
        if not idempotency_key:
            return None
        row = self.db.execute(
            "SELECT request_hash,run_id FROM agent_request_keys "
            "WHERE user_id=? AND session_id=? AND key=?",
            (user_id, session_id, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if row[0] != request_hash:
            raise IdempotencyConflict
        return RunRef(run_id=row[1])

    def owns_run(self, user_id: str, run_id: str) -> bool:
        """Return whether a Run belongs to the user."""
        return self.db.execute(
            "SELECT 1 FROM agent_runs WHERE id=? AND user_id=?", (run_id, user_id)
        ).fetchone() is not None

    def append_event(self, user_id: str, run_id: str, event: RunEvent) -> None:
        """Append an event after checking Run ownership.

        Args:
            user_id: Run owner.
            run_id: Event stream receiving the event.
            event: Typed Run event to persist.

        Raises:
            ValueError: The Run is absent or belongs to another user.
        """
        if not self.owns_run(user_id, run_id):
            raise ValueError("Run owner mismatch")
        with self.db:
            self._append_event_in_transaction(run_id, event)

    def _append_event_in_transaction(self, run_id: str, event: RunEvent) -> None:
        """Assign the next Run sequence and persist an event inside a write transaction."""
        seq = self.db.execute(
            "SELECT COALESCE(MAX(seq), 0)+1 FROM agent_events WHERE run_id=?", (run_id,)
        ).fetchone()[0]
        persisted = event.model_copy(update={"id": str(seq), "created_at": _now()})
        self.db.execute(
            "INSERT INTO agent_events VALUES (?, ?, ?)",
            (run_id, seq, persisted.model_dump_json()),
        )

    def record_plan(self, user_id: str, run_id: str, snapshot: PlanSnapshot) -> None:
        """Append a created or updated plan event only when it changed.

        Args:
            user_id: Run owner.
            run_id: Active Run whose plan is recorded.
            snapshot: Current plan state.

        Raises:
            ValueError: The Run is absent, foreign, or no longer running.
        """
        with self._immediate_transaction():
            row = self.db.execute(
                "SELECT status FROM agent_runs WHERE id=? AND user_id=?", (run_id, user_id),
            ).fetchone()
            if row is None or row[0] != "running":
                raise ValueError("Run is not active")
            previous = self.db.execute(
                "SELECT payload FROM agent_events WHERE run_id=? AND "
                "json_extract(payload, '$.type') IN ('plan.created', 'plan.updated') "
                "ORDER BY seq DESC LIMIT 1", (run_id,),
            ).fetchone()
            if previous is not None and json.loads(previous[0])["data"] == snapshot.model_dump():
                return
            event = (PlanUpdatedEvent(run_id=run_id, data=snapshot) if previous else
                     PlanCreatedEvent(run_id=run_id, data=snapshot))
            self._append_event_in_transaction(run_id, event)

    @staticmethod
    def _user_parts_json(payload: MessageRequest) -> str:
        """Encode user text and attachment references as typed message parts."""
        parts: list[MessagePart] = [TextPart(text=payload.content)]
        parts.extend(FilePart(id=item.id, name=item.name) for item in payload.attachments)
        return MESSAGE_PARTS_ADAPTER.dump_json(parts).decode("utf-8")

    def add_assistant_reply(
        self, user_id: str, run_id: str, answer: str, *, parts: list[MessagePart] | None = None,
    ) -> None:
        """Append an assistant reply and its tool and artifact parts.

        Args:
            user_id: Run owner.
            run_id: Run whose session receives the reply.
            answer: Plain text answer kept for legacy readers.
            parts: Explicit typed parts; derived parts are used when absent.

        Raises:
            ValueError: The Run is absent or belongs to another user.
        """
        row = self.db.execute(
            "SELECT session_id FROM agent_runs WHERE id=? AND user_id=?", (run_id, user_id)
        ).fetchone()
        if row is None:
            raise ValueError("Run owner mismatch")
        if parts is None:
            parts = [TextPart(text=answer), *self._tool_parts_for_run(user_id, run_id),
                     *self._artifact_parts_for_run(user_id, run_id)]
        with self.db:
            self.db.execute(
                "INSERT INTO agent_messages "
                "(id,user_id,session_id,role,content,created_at,parts_json) "
                "VALUES (?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), user_id, row[0], "assistant", answer, _now().isoformat(),
                 MESSAGE_PARTS_ADAPTER.dump_json(parts).decode("utf-8")),
            )

    def _tool_parts_for_run(self, user_id: str, run_id: str) -> list[ToolCallPart]:
        """Project tool events and completed background jobs into message parts."""
        tools: dict[str, ToolCallPart] = {}
        for event in self.events_for(user_id, run_id, None) or []:
            if isinstance(event, ToolStartedEvent):
                tools[event.data.tool_call_id] = ToolCallPart(
                    tool_call_id=event.data.tool_call_id, tool=event.data.tool,
                    status="running", summary="Tool running",
                )
            elif isinstance(event, ToolFinishedEvent):
                tools[event.data.tool_call_id] = ToolCallPart(
                    tool_call_id=event.data.tool_call_id, tool=event.data.tool,
                    status=event.data.status, summary=event.data.summary,
                )
        for tool_call_id, status in self.db.execute(
            "SELECT tool_call_id,status FROM agent_jobs WHERE run_id=? AND user_id=?",
            (run_id, user_id),
        ):
            part = tools.get(tool_call_id)
            if part and part.status in {"pending", "approval_required"} and status in {
                "completed", "failed", "cancelled",
            }:
                tools[tool_call_id] = part.model_copy(update={
                    "status": status, "summary": f"Background task {status}",
                })
        return list(tools.values())

    def _artifact_parts_for_run(self, user_id: str, run_id: str) -> list[ArtifactPart]:
        """Project artifact-created events into assistant message parts."""
        return [ArtifactPart(id=event.data.artifact_id, name=event.data.name,
                             kind=event.data.kind)
                for event in self.events_for(user_id, run_id, None) or []
                if isinstance(event, ArtifactCreatedEvent)]

    def _af3_tool_results_for_run(self, user_id: str, run_id: str) -> list[ToolResultPart]:
        """Build tool result parts for completed AF3 jobs in a Run."""
        rows = self.db.execute(
            "SELECT tool_call_id,id,status,actual_minutes,artifacts,simulation "
            "FROM agent_jobs WHERE json_extract(resource_requirements_json, '$.capability')='af3' "
            "AND user_id=? AND run_id=? AND tool_call_id IS NOT NULL "
            "AND status='completed' ORDER BY created_at,rowid",
            (user_id, run_id),
        ).fetchall()
        return [ToolResultPart(
            tool_call_id=row[0], tool="submit_af3",
            result={"task_id": row[1], "status": row[2], "actual_gpu_minutes": row[3],
                    "artifacts": json.loads(row[4]), "simulation": bool(row[5])},
        ) for row in rows]

    def _compute_tool_results_for_run(self, user_id: str, run_id: str) -> list[ToolResultPart]:
        """Project generic terminal reports without calling them AlphaFold outputs."""
        if getattr(self, "database", None) is None:
            return []
        rows = self.db.execute(
            "SELECT j.tool_call_id,j.id,j.status,d.report_json FROM agent_jobs j "
            "JOIN compute_job_data d ON d.job_id=j.id WHERE j.user_id=? AND j.run_id=? "
            "AND j.status IN ('completed','failed','cancelled') ORDER BY j.ordinal",
            (user_id, run_id),
        ).fetchall()
        return [ToolResultPart(tool_call_id=call_id, tool="submit_compute",
                              result={"job_id": job_id, "status": status, "report": report})
                for call_id, job_id, status, report in rows if call_id]

    def events_for(
        self, user_id: str, run_id: str, after: str | None, *, limit: int | None = None,
    ) -> list[RunEvent] | None:
        """Read owned Run events after a sequence cursor.

        Args:
            user_id: Run owner.
            run_id: Event stream to read.
            after: Exclusive sequence cursor; invalid cursors yield no events.
            limit: Optional maximum event count.

        Returns:
            Ordered events, or ``None`` when the Run is unavailable.
        """
        if not self.owns_run(user_id, run_id):
            return None
        try:
            cursor = int(after) if after is not None else 0
        except ValueError:
            return []
        sql = "SELECT payload FROM agent_events WHERE run_id=? AND seq>? ORDER BY seq"
        params: tuple = (run_id, cursor)
        if limit is not None:
            sql += " LIMIT ?"
            params += (limit,)
        rows = self.db.execute(sql, params).fetchall()
        adapter = TypeAdapter(RunEvent)
        return [adapter.validate_json(row[0]) for row in rows]

    def set_run_status(self, run_id: str, status: str) -> None:
        """Update a Run status and release its lease for nonactive states.

        Args:
            run_id: Run to update.
            status: New persisted Run status.
        """
        with self.db:
            if status in {"waiting", "completed", "failed", "cancelled"}:
                self.db.execute(
                    "UPDATE agent_runs SET status=?,lease_owner=NULL,lease_expires_at=NULL "
                    "WHERE id=?", (status, run_id),
                )
            else:
                self.db.execute("UPDATE agent_runs SET status=? WHERE id=?", (status, run_id))

    def start_pi_turn(self, run_id: str, owner: str) -> bool:
        """Mark a leased Pi turn uncommitted before tools can run.

        Args:
            run_id: Claimed Run starting its Pi turn.
            owner: Worker expected to hold a live lease.

        Returns:
            Whether the turn boundary and running state were recorded.
        """
        with self._immediate_transaction():
            return bool(self.db.execute(
                "UPDATE agent_runs SET status='running',checkpoint_file=NULL,"
                "turn_start_seq=(SELECT COALESCE(MAX(seq),0) FROM agent_events WHERE run_id=?) "
                "WHERE id=? "
                "AND lease_owner=? AND lease_expires_at>? "
                "AND status IN ('running','resume_queued')",
                (run_id, run_id, owner, _now().isoformat()),
            ).rowcount)

    def finish_pi_turn(
        self, user_id: str, session_id: str, run_id: str, owner: str,
        session_file: str, answer: str, *, waiting: bool,
        resumed_job_id: str | None = None,
        tool_results: list[ToolResultPart] | None = None,
    ) -> bool:
        """Commit a settled Pi branch, messages, events, and Run state atomically.

        A waiting Run stores its checkpoint without a final assistant reply;
        a completed Run stores the reply and a completion event.

        Args:
            user_id: Run and session owner.
            session_id: Pi conversation session.
            run_id: Run whose turn settled.
            owner: Worker expected to hold the live lease.
            session_file: Committed Pi transcript path.
            answer: Final answer for a completed Run.
            waiting: Whether an asynchronous tool keeps the Run waiting.
            resumed_job_id: Background job that woke this turn, if any.
            tool_results: Additional typed tool result parts.

        Returns:
            Whether a live owned lease allowed the commit.
        """
        with self._immediate_transaction():
            row = self.db.execute(
                "SELECT 1 FROM agent_runs WHERE id=? AND user_id=? AND session_id=? "
                "AND status='running' AND lease_owner=? AND lease_expires_at>?",
                (run_id, user_id, session_id, owner, _now().isoformat()),
            ).fetchone()
            if row is None:
                return False
            self.db.execute(
                "INSERT INTO pi_sessions(session_id,user_id,session_file) VALUES (?,?,?) "
                "ON CONFLICT(session_id) DO UPDATE SET user_id=excluded.user_id,"
                "session_file=excluded.session_file",
                (session_id, user_id, session_file),
            )
            if not waiting:
                parts: list[MessagePart] = [
                    TextPart(text=answer), *self._tool_parts_for_run(user_id, run_id),
                    *(tool_results or []),
                    *self._af3_tool_results_for_run(user_id, run_id),
                    *self._compute_tool_results_for_run(user_id, run_id),
                    *self._artifact_parts_for_run(user_id, run_id),
                ]
                self.db.execute(
                    "INSERT INTO agent_messages "
                    "(id,user_id,session_id,role,content,created_at,parts_json) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (str(uuid.uuid4()), user_id, session_id, "assistant", answer,
                     _now().isoformat(), MESSAGE_PARTS_ADAPTER.dump_json(parts).decode("utf-8")),
                )
                self._append_event_in_transaction(
                    run_id, RunCompletedEvent(run_id=run_id, data=RunCompletedData()),
                )
            self.db.execute(
                "UPDATE agent_runs SET status=?,checkpoint_file=?,"
                "last_resumed_job_id=COALESCE(?,last_resumed_job_id),"
                "active_resume_job_id=NULL,resume_attempts=0,retry_after=NULL,lease_owner=NULL,"
                "lease_expires_at=NULL WHERE id=?",
                ("waiting" if waiting else "completed", session_file, resumed_job_id, run_id),
            )
            if getattr(self, "database", None) is not None and resumed_job_id:
                self.db.execute("UPDATE compute_outbox SET consumed_at=now() WHERE run_id=? "
                                "AND consumed_at IS NULL", (run_id,))
            return True

    def session_file_for(self, user_id: str, session_id: str) -> str | None:
        """Read the committed Pi transcript path for an owned session.

        Returns:
            Saved path, or ``None`` when no session branch is committed.
        """
        row = self.db.execute(
            "SELECT session_file FROM pi_sessions WHERE user_id=? AND session_id=?",
            (user_id, session_id),
        ).fetchone()
        return row[0] if row else None

    def set_session_file(self, user_id: str, session_id: str, session_file: str) -> None:
        """Persist the committed Pi transcript path for a session.

        Args:
            user_id: Session owner.
            session_id: Session whose path is stored.
            session_file: Pi transcript path to commit.
        """
        with self.db:
            self.db.execute(
                "INSERT INTO pi_sessions (session_id,user_id,session_file) VALUES (?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET user_id=excluded.user_id, "
                "session_file=excluded.session_file",
                (session_id, user_id, session_file),
            )

    def owner_for_run(self, run_id: str) -> str | None:
        """Return a Run's owner ID, or ``None`` when the Run is absent."""
        row = self.db.execute("SELECT user_id FROM agent_runs WHERE id=?", (run_id,)).fetchone()
        return row[0] if row else None

    def run_status_for(self, user_id: str, run_id: str) -> RunStatus | None:
        """Read an owned Run's current status.

        Returns:
            Status snapshot, or ``None`` when the Run is unavailable.
        """
        row = self.db.execute(
            "SELECT status FROM agent_runs WHERE id=? AND user_id=?", (run_id, user_id)
        ).fetchone()
        return RunStatus(run_id=run_id, status=row[0]) if row else None

    def cancel_run(self, user_id: str, run_id: str) -> RunStatus | None:
        """Cancel an active owned Run and reject its pending approvals.

        Terminal Runs keep their existing status. AF3 job cancellation is a
        separate operation handled by the caller.

        Args:
            user_id: Run owner.
            run_id: Run to cancel.

        Returns:
            Latest status, or ``None`` when the Run is unavailable.
        """
        status = self.run_status_for(user_id, run_id)
        if status is None:
            return None
        with self._immediate_transaction():
            changed = self.db.execute(
                "UPDATE agent_runs SET status='cancelled',lease_owner=NULL,lease_expires_at=NULL "
                "WHERE id=? AND user_id=? "
                "AND status NOT IN ('completed','failed','cancelled')",
                (run_id, user_id),
            ).rowcount
            if changed:
                pending = self.db.execute(
                    "SELECT id FROM agent_approvals WHERE run_id=? AND user_id=? AND status='pending'",
                    (run_id, user_id),
                ).fetchall()
                self.db.execute(
                    "UPDATE agent_approvals SET status='rejected' WHERE run_id=? AND user_id=? "
                    "AND status='pending'", (run_id, user_id),
                )
                for (approval_id,) in pending:
                    self._append_event_in_transaction(run_id, ApprovalResolvedEvent(
                        run_id=run_id,
                        data=ApprovalResolvedData(approval_id=approval_id, decision="rejected"),
                    ))
                self._append_event_in_transaction(
                    run_id, RunCancelledEvent(run_id=run_id, data=RunCancelledData())
                )
        return self.run_status_for(user_id, run_id)
