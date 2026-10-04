"""Workspace persistence methods for the conversation store."""

import uuid

from app.contracts.conversation import Project, ProjectSkillSettings, Session
from app.domain.project_routes import new_project_id

from .common import current_time as _now


class WorkspaceMixin:
    """Persist projects, sessions, and per-project Skill selections."""

    def projects_for(self, user_id: str) -> list[Project]:
        """List a user's active projects, with the default project first.

        Args:
            user_id: Owner whose projects are returned.

        Returns:
            Active projects; archived projects are omitted.
        """
        rows = self.db.execute(
            "SELECT id,name,description,archived_at,icon FROM workspace_projects "
            "WHERE user_id=? ORDER BY rowid", (user_id,),
        ).fetchall()
        default = self.project_for(user_id)
        override = next((row for row in rows if row[0] == default.id), None)
        first = ([] if override and override[3] else
                 [Project(id=default.id, name=override[1], description=override[2], icon=override[4])]
                 if override else [default])
        return first + [Project(id=row[0], name=row[1], description=row[2], icon=row[4])
                        for row in rows if row[0] != default.id and row[3] is None]

    def create_project(self, user_id: str, name: str, description: str, icon: str = "folder") -> Project:
        """Create a project owned by the user.

        Args:
            user_id: New project owner.
            name: Display name used to derive the project ID.
            description: Project description.
            icon: Display icon name.

        Returns:
            The created project.
        """
        project = Project(id=new_project_id(name), name=name, description=description, icon=icon)
        with self.db:
            self._require_not_deleting(user_id)
            self.db.execute(
                "INSERT INTO workspace_projects (id,user_id,name,description,archived_at,icon) VALUES (?,?,?,?,NULL,?)",
                (project.id, user_id, name, description, icon),
            )
        return project

    def rename_project(self, user_id: str, project_id: str, name: str) -> Project | None:
        """Rename a visible project owned by the user.

        Args:
            user_id: Project owner.
            project_id: Project to rename.
            name: Replacement display name.

        Returns:
            Updated project, or ``None`` when it is unavailable to the user.
        """
        project = next((item for item in self.projects_for(user_id) if item.id == project_id), None)
        if project is None:
            return None
        with self.db:
            self._require_not_deleting(user_id)
            self.db.execute(
                "INSERT INTO workspace_projects (id,user_id,name,description,archived_at,icon) VALUES (?,?,?,?,NULL,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name",
                (project_id, user_id, name, project.description, project.icon),
            )
        return project.model_copy(update={"name": name})

    def set_project_icon(self, user_id: str, project_id: str, icon: str) -> Project | None:
        """Update the display icon of an active project.

        Args:
            user_id: Project owner.
            project_id: Project to update.
            icon: Replacement display icon name.

        Returns:
            Updated project, or ``None`` when it is unavailable to the user.
        """
        project = next((item for item in self.projects_for(user_id) if item.id == project_id), None)
        if project is None:
            return None
        with self.db:
            self._require_not_deleting(user_id)
            self.db.execute(
                "INSERT INTO workspace_projects (id,user_id,name,description,archived_at,icon) VALUES (?,?,?,?,NULL,?) "
                "ON CONFLICT(id) DO UPDATE SET icon=excluded.icon",
                (project_id, user_id, project.name, project.description, icon),
            )
        return project.model_copy(update={"icon": icon})

    def archive_project(self, user_id: str, project_id: str) -> bool:
        """Hide an owned project from active project listings.

        Args:
            user_id: Project owner.
            project_id: Project to archive.

        Returns:
            Whether an active project was found and archived.
        """
        project = next((item for item in self.projects_for(user_id) if item.id == project_id), None)
        if project is None:
            return False
        with self.db:
            self._require_not_deleting(user_id)
            self.db.execute(
                "INSERT INTO workspace_projects (id,user_id,name,description,archived_at,icon) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET archived_at=excluded.archived_at",
                (project_id, user_id, project.name, project.description, _now().isoformat(), project.icon),
            )
        return True

    def create_session(self, user_id: str, project_id: str, title: str, *, auto_title: bool = False) -> Session | None:
        """Create a session inside an active project owned by the user.

        Args:
            user_id: Session owner.
            project_id: Destination project.
            title: Initial session title.

        Returns:
            Created session, or ``None`` when the project is unavailable.
        """
        if not any(project.id == project_id for project in self.projects_for(user_id)):
            return None
        session = Session(id=str(uuid.uuid4()), project_id=project_id, title=title)
        with self.db:
            self._require_not_deleting(user_id)
            self.db.execute(
                "INSERT INTO workspace_sessions VALUES (?,?,?,?,NULL)",
                (session.id, user_id, project_id, title),
            )
            if auto_title:
                self.db.execute(
                    "INSERT INTO agent_session_titles (session_id,user_id,status,created_at) "
                    "VALUES (?,?,'waiting',?)", (session.id, user_id, _now().isoformat()),
                )
                session = session.model_copy(update={"title_status": "idle"})
        return session

    def move_session(self, user_id: str, session_id: str, project_id: str) -> Session | None:
        """Move an owned, stored session into another active project.

        The built-in default session cannot be moved.

        Args:
            user_id: Session and project owner.
            session_id: Session to move.
            project_id: Destination project.

        Returns:
            Updated session, or ``None`` when either side is unavailable.
        """
        if session_id == f"session-{user_id}" or not self._owns_session(user_id, session_id):
            return None
        if not any(project.id == project_id for project in self.projects_for(user_id)):
            return None
        with self.db:
            self._require_not_deleting(user_id)
            row = self.db.execute(
                "SELECT title FROM workspace_sessions WHERE id=? AND user_id=? AND archived_at IS NULL",
                (session_id, user_id),
            ).fetchone()
            if row is None:
                return None
            self.db.execute(
                "UPDATE workspace_sessions SET project_id=? WHERE id=? AND user_id=?",
                (project_id, session_id, user_id),
            )
        return Session(id=session_id, project_id=project_id, title=row[0],
                       title_status=self.title_status_for(user_id, session_id))

    def project_skill_settings(self, user_id: str, project_id: str) -> ProjectSkillSettings | None:
        """Read ordered Skill grants and defaults for an active project.

        Args:
            user_id: Project owner.
            project_id: Project whose settings are requested.

        Returns:
            Skill settings, or ``None`` when the project is unavailable.
        """
        if not any(project.id == project_id for project in self.projects_for(user_id)):
            return None
        rows = self.db.execute(
            "SELECT skill_id,is_default FROM workspace_project_skills "
            "WHERE user_id=? AND project_id=? ORDER BY position",
            (user_id, project_id),
        ).fetchall()
        return ProjectSkillSettings(
            skill_ids=[row[0] for row in rows],
            default_skill_ids=[row[0] for row in rows if row[1]],
        )

    def project_id_for_session(self, user_id: str, session_id: str) -> str | None:
        """Resolve the active project containing an owned session.

        Args:
            user_id: Session owner.
            session_id: Session to resolve.

        Returns:
            Project ID, or ``None`` when the session is unavailable.
        """
        if not self._owns_session(user_id, session_id):
            return None
        row = self.db.execute(
            "SELECT project_id FROM workspace_sessions WHERE id=? AND user_id=? AND archived_at IS NULL",
            (session_id, user_id),
        ).fetchone()
        return row[0] if row else self.project_for(user_id).id

    def set_project_skill_settings(
        self, user_id: str, project_id: str, settings: ProjectSkillSettings,
    ) -> ProjectSkillSettings | None:
        """Replace the ordered Skill grants for an owned project atomically.

        Args:
            user_id: Project owner.
            project_id: Project to update.
            settings: Skill IDs and the subset selected by default.

        Returns:
            Saved settings, or ``None`` when the project is unavailable.
        """
        if self.project_skill_settings(user_id, project_id) is None:
            return None
        defaults = set(settings.default_skill_ids)
        with self.db:
            self._require_not_deleting(user_id)
            self.db.execute(
                "DELETE FROM workspace_project_skills WHERE user_id=? AND project_id=?",
                (user_id, project_id),
            )
            self.db.executemany(
                "INSERT INTO workspace_project_skills "
                "(user_id,project_id,skill_id,is_default,position) VALUES (?,?,?,?,?)",
                [(user_id, project_id, skill_id, int(skill_id in defaults), position)
                 for position, skill_id in enumerate(settings.skill_ids)],
            )
        return settings

    def rename_session(self, user_id: str, session_id: str, title: str) -> Session | None:
        """Rename an owned active session, including the built-in default session.

        Args:
            user_id: Session owner.
            session_id: Session to rename.
            title: Replacement title.

        Returns:
            Updated session, or ``None`` when it is unavailable.
        """
        if not self._owns_session(user_id, session_id):
            return None
        row = self.db.execute(
            "SELECT project_id FROM workspace_sessions WHERE id=? AND user_id=?",
            (session_id, user_id),
        ).fetchone()
        project_id = row[0] if row else self.project_for(user_id).id
        with self._immediate_transaction():
            self._require_not_deleting(user_id)
            self.db.execute(
                "INSERT INTO workspace_sessions VALUES (?,?,?,?,NULL) "
                "ON CONFLICT(id) DO UPDATE SET title=excluded.title",
                (session_id, user_id, project_id, title),
            )
            self.db.execute(
                "UPDATE agent_session_titles SET status='manual' WHERE session_id=? AND user_id=?",
                (session_id, user_id),
            )
        return Session(id=session_id, project_id=project_id, title=title)

    def archive_session(self, user_id: str, session_id: str) -> bool:
        """Hide an owned session from active session listings.

        Args:
            user_id: Session owner.
            session_id: Session to archive.

        Returns:
            Whether an active session was found and archived.
        """
        if not self._owns_session(user_id, session_id):
            return False
        row = self.db.execute(
            "SELECT project_id,title FROM workspace_sessions WHERE id=? AND user_id=?",
            (session_id, user_id),
        ).fetchone()
        project_id = row[0] if row else self.project_for(user_id).id
        title = row[1] if row else "新的科研任务"
        with self._immediate_transaction():
            self._require_not_deleting(user_id)
            self.db.execute(
                "INSERT INTO workspace_sessions VALUES (?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET archived_at=excluded.archived_at",
                (session_id, user_id, project_id, title, _now().isoformat()),
            )
            self.db.execute(
                "UPDATE agent_session_titles SET status='manual' WHERE session_id=? AND user_id=?",
                (session_id, user_id),
            )
        return True

    def _owns_session(self, user_id: str, session_id: str) -> bool:
        """Check ownership and active state, including the default session."""
        row = self.db.execute(
            "SELECT project_id,archived_at FROM workspace_sessions WHERE id=? AND user_id=?",
            (session_id, user_id),
        ).fetchone()
        if row:
            return row[1] is None and any(
                project.id == row[0] for project in self.projects_for(user_id)
            )
        return session_id == f"session-{user_id}" and any(
            project.id == self.project_for(user_id).id for project in self.projects_for(user_id)
        )

    def sessions_for(self, user_id: str, project_id: str) -> list[Session] | None:
        """List active project sessions with their latest Run status.

        Args:
            user_id: Project and session owner.
            project_id: Project whose sessions are requested.

        Returns:
            Sessions, or ``None`` when the project is unavailable.
        """
        if not any(project.id == project_id for project in self.projects_for(user_id)):
            return None
        default_id = f"session-{user_id}"
        rows = self.db.execute(
            "SELECT id,title FROM workspace_sessions "
            "WHERE user_id=? AND project_id=? AND archived_at IS NULL AND id<>? ORDER BY rowid",
            (user_id, project_id, default_id),
        ).fetchall()
        sessions = [Session(id=row[0], project_id=project_id, title=row[1]) for row in rows]
        if project_id == self.project_for(user_id).id:
            default_row = self.db.execute(
                "SELECT title,archived_at FROM workspace_sessions WHERE id=? AND user_id=?",
                (default_id, user_id),
            ).fetchone()
            has_history = self.db.execute(
                "SELECT 1 FROM agent_messages WHERE user_id=? AND session_id=? LIMIT 1",
                (user_id, default_id),
            ).fetchone() or self.db.execute(
                "SELECT 1 FROM agent_runs WHERE user_id=? AND session_id=? LIMIT 1",
                (user_id, default_id),
            ).fetchone()
            if (default_row is not None and default_row[1] is None
                    or default_row is None and has_history):
                sessions.insert(0, Session(
                    id=default_id, project_id=project_id,
                    title=default_row[0] if default_row else "新的科研任务",
                ))
        for index, session in enumerate(sessions):
            sessions[index] = session = session.model_copy(update={
                "title_status": self.title_status_for(user_id, session.id),
            })
            row = self.db.execute(
                "SELECT id,status FROM agent_runs WHERE user_id=? AND session_id=? "
                "ORDER BY created_at DESC,rowid DESC LIMIT 1",
                (user_id, session.id),
            ).fetchone()
            if row:
                status = "running" if row[1] in {"queued", "resume_queued"} else row[1]
                sessions[index] = session.model_copy(
                    update={"status": status, "latest_run_id": row[0]}
                )
        return sessions
