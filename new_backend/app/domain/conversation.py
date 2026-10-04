import uuid
from datetime import UTC, datetime

from app.contracts.conversation import (
    FilePart,
    Message,
    MessageDeltaData,
    MessageDeltaEvent,
    MessagePart,
    MessageRequest,
    Project,
    ProjectSkillSettings,
    RunCancelledData,
    RunCancelledEvent,
    RunCompletedData,
    RunCompletedEvent,
    RunEvent,
    RunRef,
    RunStatus,
    Session,
    TextPart,
)
from app.domain.project_routes import new_project_id
from app.domain.quota import QuotaLedger


class IdempotencyConflict(Exception):
    """A message request key was reused with different content."""


def _now() -> datetime:
    """Return the UTC clock used for mock messages and events."""
    return datetime.now(UTC)


class ConversationStore:
    """Hold demo projects, sessions, messages, and Runs in process memory."""

    def __init__(self) -> None:
        """Initialize isolated per-user mock collections."""
        self._messages: dict[str, list[Message]] = {}
        self._runs: dict[str, tuple[str, str, list[RunEvent]]] = {}
        self._projects: dict[str, dict[str, Project]] = {}
        self._sessions: dict[str, dict[str, Session]] = {}
        self._archived_projects: dict[str, set[str]] = {}
        self._archived_sessions: dict[str, set[str]] = {}
        self._project_skills: dict[tuple[str, str], ProjectSkillSettings] = {}
        self._accepted_requests: dict[tuple[str, str, str], tuple[str, str]] = {}

    def existing_message_for(
        self, user_id: str, session_id: str, idempotency_key: str | None,
        request_hash: str,
    ) -> RunRef | None:
        """Look up a prior mock request by its idempotency key.

        Returns:
            Prior Run reference, or ``None`` when no matching key exists.

        Raises:
            IdempotencyConflict: The key has a different request fingerprint.
        """
        if not idempotency_key:
            return None
        existing = self._accepted_requests.get((user_id, session_id, idempotency_key))
        if existing is None:
            return None
        if existing[0] != request_hash:
            raise IdempotencyConflict
        return RunRef(run_id=existing[1])

    def accept_message(
        self, user_id: str, session_id: str, payload: MessageRequest,
        quota: QuotaLedger, estimated_tokens: int,
        idempotency_key: str | None, request_hash: str,
        *, waiting: bool = False, instructions: str = "",
        allowed_tools: tuple[str, ...] = (), user_prompt: str = "", model_id: str = "",
        image_ids: tuple[str, ...] = (),
        model_supports_images: bool = False,
        reasoning_effort: str | None = None,
    ) -> tuple[RunRef, bool] | None:
        """Charge mock Tokens and accept a user message once per request key.

        Args:
            user_id: Session owner.
            session_id: Destination session.
            payload: User text and attachments.
            quota: In-memory Token ledger.
            estimated_tokens: Initial charge for this message.
            idempotency_key: Optional request key for duplicate suppression.
            request_hash: Fingerprint associated with that key.
            waiting: Emit a background-task placeholder when true.
            instructions: Unused in the demo responder.
            allowed_tools: Unused in the demo responder.
            user_prompt: Unused in the demo responder.

        Returns:
            Run reference and whether it is new, or ``None`` for an absent
            session.

        Raises:
            IdempotencyConflict: The key was reused with different input.
            TokenQuotaExceeded: The mock ledger rejects the charge.
        """
        del instructions, allowed_tools, user_prompt, model_id, image_ids, model_supports_images, reasoning_effort
        if self.messages_for(user_id, session_id) is None:
            return None
        key = (user_id, session_id, idempotency_key) if idempotency_key else None
        existing = self.existing_message_for(user_id, session_id, idempotency_key, request_hash)
        if existing:
            return existing, False
        quota.charge_tokens(user_id, estimated_tokens)
        run = self.send_message(user_id, session_id, payload, waiting=waiting)
        if run is None:
            quota.adjust_tokens(user_id, -estimated_tokens)
            return None
        if key:
            self._accepted_requests[key] = (request_hash, run.run_id)
        return run, True

    @staticmethod
    def project_for(user_id: str) -> Project:
        """Build the deterministic default demo project for one user."""
        return Project(
            id=f"project-{user_id}", name="我的科研项目", description="科研 Agent 演示工作区"
        )

    def projects_for(self, user_id: str) -> list[Project]:
        """List active mock projects with the default project first."""
        archived = self._archived_projects.get(user_id, set())
        default = self.project_for(user_id)
        result = [] if default.id in archived else [self._projects.get(user_id, {}).get(default.id, default)]
        return result + [project for project in self._projects.get(user_id, {}).values()
                         if project.id != default.id and project.id not in archived]

    def create_project(self, user_id: str, name: str, description: str, icon: str = "folder") -> Project:
        """Create an owned mock project with a unique route ID.

        Returns:
            Newly created project.
        """
        project = Project(id=new_project_id(name), name=name, description=description, icon=icon)
        self._projects.setdefault(user_id, {})[project.id] = project
        return project

    def rename_project(self, user_id: str, project_id: str, name: str) -> Project | None:
        """Rename an active owned mock project, if available."""
        project = next((item for item in self.projects_for(user_id) if item.id == project_id), None)
        if project is None:
            return None
        updated = project.model_copy(update={"name": name})
        self._projects.setdefault(user_id, {})[project_id] = updated
        return updated

    def set_project_icon(self, user_id: str, project_id: str, icon: str) -> Project | None:
        """Replace an active owned mock project's display icon."""
        project = next((item for item in self.projects_for(user_id) if item.id == project_id), None)
        if project is None:
            return None
        updated = project.model_copy(update={"icon": icon})
        self._projects.setdefault(user_id, {})[project_id] = updated
        return updated

    def archive_project(self, user_id: str, project_id: str) -> bool:
        """Hide an active owned project from mock project listings."""
        if not any(item.id == project_id for item in self.projects_for(user_id)):
            return False
        self._archived_projects.setdefault(user_id, set()).add(project_id)
        return True

    def create_session(self, user_id: str, project_id: str, title: str) -> Session | None:
        """Create a mock session in an active owned project.

        Returns:
            New session, or ``None`` when the project is unavailable.
        """
        if not any(project.id == project_id for project in self.projects_for(user_id)):
            return None
        session = Session(id=str(uuid.uuid4()), project_id=project_id, title=title)
        self._sessions.setdefault(user_id, {})[session.id] = session
        return session

    def move_session(self, user_id: str, session_id: str, project_id: str) -> Session | None:
        """Move a stored mock session to another owned project.

        The built-in default session cannot move.

        Returns:
            Updated session, or ``None`` when either side is unavailable.
        """
        if session_id == f"session-{user_id}" or not self._owns_session(user_id, session_id):
            return None
        if not any(project.id == project_id for project in self.projects_for(user_id)):
            return None
        session = self._sessions[user_id][session_id]
        moved = session.model_copy(update={"project_id": project_id})
        self._sessions[user_id][session_id] = moved
        return moved

    def project_skill_settings(self, user_id: str, project_id: str) -> ProjectSkillSettings | None:
        """Read Skill selections for an active owned mock project."""
        if not any(project.id == project_id for project in self.projects_for(user_id)):
            return None
        return self._project_skills.get((user_id, project_id), ProjectSkillSettings())

    def project_id_for_session(self, user_id: str, session_id: str) -> str | None:
        """Resolve an active owned session to its mock project ID."""
        if not self._owns_session(user_id, session_id):
            return None
        session = self._sessions.get(user_id, {}).get(session_id)
        return session.project_id if session else self.project_for(user_id).id

    def session_for(self, user_id: str, session_id: str) -> Session | None:
        """Read a mock session only when its project remains active."""
        project_id = self.project_id_for_session(user_id, session_id)
        if project_id is None:
            return None
        return next((session for session in self.sessions_for(user_id, project_id) or []
                     if session.id == session_id), None)

    def set_project_skill_settings(
        self, user_id: str, project_id: str, settings: ProjectSkillSettings,
    ) -> ProjectSkillSettings | None:
        """Replace ordered Skill selections for an active mock project."""
        if self.project_skill_settings(user_id, project_id) is None:
            return None
        self._project_skills[(user_id, project_id)] = settings
        return settings

    def rename_session(self, user_id: str, session_id: str, title: str) -> Session | None:
        """Rename an owned mock session, including the default session."""
        if not self._owns_session(user_id, session_id):
            return None
        default_id = f"session-{user_id}"
        session = self._sessions.get(user_id, {}).get(session_id)
        if session is None and session_id == default_id:
            session = Session(id=default_id, project_id=self.project_for(user_id).id,
                              title="新的科研任务")
        if session is None:
            return None
        updated = session.model_copy(update={"title": title})
        self._sessions.setdefault(user_id, {})[session_id] = updated
        return updated

    def archive_session(self, user_id: str, session_id: str) -> bool:
        """Hide an owned mock session from active listings."""
        if not self._owns_session(user_id, session_id):
            return False
        self._archived_sessions.setdefault(user_id, set()).add(session_id)
        return True

    def sessions_for(self, user_id: str, project_id: str) -> list[Session] | None:
        """List created chats and legacy chats with saved content or Runs."""
        if not any(project.id == project_id for project in self.projects_for(user_id)):
            return None
        archived = self._archived_sessions.get(user_id, set())
        default_id = f"session-{user_id}"
        sessions = [session for session in self._sessions.get(user_id, {}).values()
                    if session.project_id == project_id and session.id != default_id
                    and session.id not in archived]
        stored_default = self._sessions.get(user_id, {}).get(default_id)
        has_history = bool(self._messages.get(default_id)) or any(
            owner == user_id and run_session_id == default_id
            for owner, run_session_id, _ in self._runs.values()
        )
        if (self.project_for(user_id).id == project_id and default_id not in archived
                and (stored_default is not None or has_history)):
            sessions.insert(0, stored_default or Session(
                id=default_id, project_id=project_id, title="新的科研任务"
            ))
        for index, session in enumerate(sessions):
            for run_id, (owner, run_session_id, _) in reversed(self._runs.items()):
                if owner == user_id and run_session_id == session.id:
                    status = self.run_status_for(user_id, run_id)
                    sessions[index] = session.model_copy(
                        update={"latest_run_id": run_id, "status": status.status}
                    )
                    break
        return sessions

    def _owns_session(self, user_id: str, session_id: str) -> bool:
        """Check mock session ownership and active project visibility."""
        if session_id in self._archived_sessions.get(user_id, set()):
            return False
        if session_id == f"session-{user_id}":
            return any(item.id == self.project_for(user_id).id for item in self.projects_for(user_id))
        session = self._sessions.get(user_id, {}).get(session_id)
        return session is not None and any(
            item.id == session.project_id for item in self.projects_for(user_id)
        )

    def messages_for(self, user_id: str, session_id: str) -> list[Message] | None:
        """Return the mutable mock transcript for an active owned session.

        Returns:
            Session messages, or ``None`` when the session is unavailable.
        """
        if not self._owns_session(user_id, session_id):
            return None
        return self._messages.setdefault(session_id, [])

    def send_message(
        self, user_id: str, session_id: str, payload: MessageRequest, *, waiting: bool = False
    ) -> RunRef | None:
        """Append a mock user message and create a demo Run.

        Without ``waiting``, the demo responder immediately stores an answer
        and a completed event. Waiting emits a simulated task placeholder.

        Args:
            user_id: Session owner.
            session_id: Destination session.
            payload: User text and attachment references.
            waiting: Whether to leave the Run awaiting mock background work.

        Returns:
            New Run reference, or ``None`` when the session is unavailable.
        """
        messages = self.messages_for(user_id, session_id)
        if messages is None:
            return None
        messages.append(
            Message(
                id=str(uuid.uuid4()),
                session_id=session_id,
                role="user",
                parts=[TextPart(text=payload.content), *(
                    FilePart(id=item.id, name=item.name) for item in payload.attachments
                )],
                created_at=_now(),
            )
        )
        run_id = str(uuid.uuid4())
        self._runs[run_id] = (user_id, session_id, [])
        if waiting:
            self.append_event(
                user_id,
                run_id,
                MessageDeltaEvent(
                    run_id=run_id, data=MessageDeltaData(delta="AF3 模拟任务已提交，等待后台计算。")
                ),
            )
        else:
            answer = "已收到你的请求。这是演示模式的 Agent 回复。"
            self.append_event(
                user_id,
                run_id,
                MessageDeltaEvent(run_id=run_id, data=MessageDeltaData(delta=answer)),
            )
            self.append_event(
                user_id, run_id, RunCompletedEvent(run_id=run_id, data=RunCompletedData())
            )
            messages.append(
                Message(
                    id=str(uuid.uuid4()),
                    session_id=session_id,
                    role="assistant",
                    parts=[TextPart(text=answer)],
                    created_at=_now(),
                )
            )
        return RunRef(run_id=run_id)

    def owns_run(self, user_id: str, run_id: str) -> bool:
        """Return whether a mock Run belongs to a user."""
        run = self._runs.get(run_id)
        return run is not None and run[0] == user_id

    def run_status_for(self, user_id: str, run_id: str) -> RunStatus | None:
        """Derive a mock Run's state from its latest event.

        Returns:
            Status snapshot, or ``None`` when the Run is unavailable.
        """
        if not self.owns_run(user_id, run_id):
            return None
        events = self._runs[run_id][2]
        last_type = events[-1].type if events else None
        status = ("completed" if last_type == "run.completed" else
                  "failed" if last_type == "run.failed" else
                  "cancelled" if last_type == "run.cancelled" else "waiting")
        return RunStatus(run_id=run_id, status=status)

    def cancel_run(self, user_id: str, run_id: str) -> RunStatus | None:
        """Append a cancellation event to a nonterminal owned mock Run.

        Returns:
            Latest status, or ``None`` when the Run is unavailable.
        """
        status = self.run_status_for(user_id, run_id)
        if status is None:
            return None
        if status.status not in {"completed", "failed", "cancelled"}:
            self.append_event(
                user_id, run_id, RunCancelledEvent(run_id=run_id, data=RunCancelledData())
            )
        return self.run_status_for(user_id, run_id)

    def append_event(self, user_id: str, run_id: str, event: RunEvent) -> None:
        """Append a numbered typed event after checking mock Run ownership.

        Raises:
            ValueError: The Run is absent or belongs to another user.
        """
        if not self.owns_run(user_id, run_id):
            raise ValueError("Run owner mismatch")
        events = self._runs[run_id][2]
        events.append(event.model_copy(update={"id": str(len(events) + 1), "created_at": _now()}))

    def add_assistant_reply(
        self, user_id: str, run_id: str, answer: str, *, parts: list[MessagePart] | None = None,
    ) -> None:
        """Append a typed assistant message to an owned mock Run's session.

        Args:
            user_id: Run owner.
            run_id: Run whose session receives the reply.
            answer: Plain text fallback content.
            parts: Explicit message parts, if available.

        Raises:
            ValueError: The Run is absent or belongs to another user.
        """
        if not self.owns_run(user_id, run_id):
            raise ValueError("Run owner mismatch")
        session_id = self._runs[run_id][1]
        self._messages[session_id].append(
            Message(
                id=str(uuid.uuid4()),
                session_id=session_id,
                role="assistant",
                parts=parts or [TextPart(text=answer)],
                created_at=_now(),
            )
        )

    def events_for(
        self, user_id: str, run_id: str, after: str | None, *, limit: int | None = None,
    ) -> list[RunEvent] | None:
        """Read owned mock Run events after an exclusive sequence cursor.

        Args:
            user_id: Run owner.
            run_id: Event stream to read.
            after: Exclusive cursor; invalid text yields an empty list.
            limit: Optional maximum number of events.

        Returns:
            Ordered events, or ``None`` when the Run is unavailable.
        """
        owned = self._runs.get(run_id)
        if owned is None or owned[0] != user_id:
            return None
        if after is None:
            return owned[2][:limit] if limit is not None else owned[2]
        try:
            cursor = int(after)
        except ValueError:
            return []
        events = [event for event in owned[2] if int(event.id) > cursor]
        return events[:limit] if limit is not None else events
