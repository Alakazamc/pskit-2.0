"""One durable, auxiliary naming request per conversation; manual names take priority."""

import json
from datetime import timedelta

from .common import current_time as _now


class TitlesMixin:
    def title_status_for(self, user_id: str, session_id: str) -> str:
        row = self.db.execute(
            "SELECT status FROM agent_session_titles WHERE session_id=? AND user_id=?",
            (session_id, user_id),
        ).fetchone()
        status = row[0] if row else "manual"
        return "idle" if status == "waiting" else "pending" if status == "running" else status

    def _queue_session_title(self, user_id: str, session_id: str, run_id: str, answer: str) -> None:
        """Queue only the first complete reply inside the reply's commit transaction."""
        context = self.run_context(run_id)
        prompt = json.dumps({
            "user_message": (context.get("title_user_text") or context.get("user_prompt") or "")[:1000],
            "assistant_reply": answer[:1500],
        }, ensure_ascii=False)
        self.db.execute(
            "UPDATE agent_session_titles SET status='pending',run_id=?,prompt_json=? "
            "WHERE session_id=? AND user_id=? AND status='waiting'",
            (run_id, prompt, session_id, user_id),
        )

    def claim_session_title(self, stale_seconds: float) -> tuple[str, str, str, str] | None:
        """Claim pending work once; uncertain crashed requests are never automatically resent."""
        with self._immediate_transaction():
            self.db.execute(
                "UPDATE agent_session_titles SET status='failed' WHERE status='running' AND started_at<?",
                ((_now() - timedelta(seconds=stale_seconds)).isoformat(),),
            )
            if self.db.execute(
                "SELECT COUNT(*) FROM agent_session_titles WHERE status='running'",
            ).fetchone()[0] >= 2:
                return None
            row = self.db.execute(
                "SELECT t.session_id,t.user_id,t.run_id,t.prompt_json FROM agent_session_titles t "
                "JOIN workspace_sessions s ON s.id=t.session_id AND s.user_id=t.user_id "
                "WHERE t.status='pending' AND s.archived_at IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM workspace_projects p "
                "WHERE p.id=s.project_id AND p.archived_at IS NOT NULL) "
                "AND NOT EXISTS (SELECT 1 FROM account_tiers a "
                "WHERE a.user_id=t.user_id AND a.cleanup_state='deleting') "
                "ORDER BY t.created_at,t.session_id LIMIT 1",
            ).fetchone()
            if row is None:
                return None
            self.db.execute(
                "UPDATE agent_session_titles SET status='running',started_at=? WHERE session_id=?",
                (_now().isoformat(), row[0]),
            )
            return tuple(row)

    def title_request_active(self, user_id: str, session_id: str) -> bool:
        return self.title_status_for(user_id, session_id) == "pending" and self._owns_session(user_id, session_id)

    def finish_session_title(self, user_id: str, session_id: str, title: str | None) -> bool:
        """Commit a generated name only if no manual rename or archive won the race."""
        with self._immediate_transaction():
            row = self.db.execute(
                "SELECT status FROM agent_session_titles WHERE session_id=? AND user_id=?",
                (session_id, user_id),
            ).fetchone()
            if row is None or row[0] != "running":
                return False
            if not self._owns_session(user_id, session_id):
                title = None
            if title:
                self._require_not_deleting(user_id)
                self.db.execute(
                    "UPDATE workspace_sessions SET title=? WHERE id=? AND user_id=? AND archived_at IS NULL",
                    (title, session_id, user_id),
                )
            self.db.execute(
                "UPDATE agent_session_titles SET status=? WHERE session_id=? AND user_id=?",
                ("generated" if title else "failed", session_id, user_id),
            )
            return bool(title)
