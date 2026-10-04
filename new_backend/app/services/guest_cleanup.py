"""Conservative selection and deletion of inactive anonymous identities."""

import hashlib
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from app.db.postgres import PostgresDatabase, PostgresStatements
from app.domain.identity_policy import IdentityPolicyStore


class GuestAdmin(Protocol):
    """Server-only identity operations needed by cleanup."""

    async def get_user(self, user_id: str) -> dict | None:
        """Fetch the remote identity or return ``None`` when absent."""
        ...

    async def delete_user(self, user_id: str) -> None:
        """Delete an anonymous identity using server-only credentials."""
        ...


class GuestCleanupService:
    """Select old guests and coordinate deletion through a shared SQLite file."""

    def __init__(
        self, path: str | PostgresDatabase, admin: GuestAdmin | None, *, pi_session_dir: str | None = None,
    ) -> None:
        """Open shared policy storage and bind the remote identity admin.

        Args:
            path: SQLite database shared with identity and conversation data.
            admin: Server-only remote identity client; absent means preview only.
            pi_session_dir: Optional root of owned Pi transcript directories.
        """
        self.policy = IdentityPolicyStore(path)
        self.db = self.policy.db
        self.admin = admin
        self.pi_session_dir = Path(pi_session_dir).resolve() if pi_session_dir else None

    def _tables(self) -> set[str]:
        """List only PSKit tables in the bound persistence schema."""
        statement = ("SELECT tablename FROM pg_tables WHERE schemaname=current_schema()"
                     if isinstance(self.db, PostgresStatements)
                     else "SELECT name FROM sqlite_master WHERE type='table'")
        return {row[0] for row in self.db.execute(statement)}

    def collect_candidates(self, now: datetime) -> list[str]:
        """List inactive guests plus stale deletion claims eligible for retry.

        Args:
            now: Reference UTC time for age and claim checks.

        Returns:
            Sorted guest IDs with no unfinished work, including deletion
            claims older than one hour.
        """
        pending = self.db.execute(
            "SELECT claims.user_id FROM guest_cleanup_claims claims "
            "JOIN account_tiers tiers ON tiers.user_id=claims.user_id "
            "WHERE tiers.tier='guest' AND tiers.cleanup_state='deleting' "
            "AND claims.claimed_at<=?",
            ((now - timedelta(hours=1)).isoformat(),),
        ).fetchall()
        return sorted(set(self._eligible_ids(now)) | {row[0] for row in pending})

    def _eligible_ids(
        self, now: datetime, user_id: str | None = None, *, include_deleting: bool = False,
    ) -> list[str]:
        """Select guests older than 30 days without active work.

        Args:
            now: Reference UTC time for inactivity.
            user_id: Optional identity to filter.
            include_deleting: Select cleanup-claimed identities instead of
                active guests.

        Returns:
            Eligible user IDs in sorted order.
        """
        cutoff = (now - timedelta(days=30)).isoformat()
        tables = self._tables()
        blockers = []
        for table in ("agent_runs", "agent_jobs", "agent_approvals"):
            if table in tables:
                unfinished = ("work.status='pending'" if table == "agent_approvals" else
                              "work.status NOT IN ('completed','failed','cancelled')")
                blockers.append(
                    f"NOT EXISTS (SELECT 1 FROM {table} work "
                    "WHERE work.user_id=account_tiers.user_id "
                    f"AND {unfinished})"
                )
        where = " AND ".join(blockers)
        if where:
            where = " AND " + where
        user_filter = " AND account_tiers.user_id=?" if user_id is not None else ""
        state = "deleting" if include_deleting else "active"
        rows = self.db.execute(
            "SELECT user_id FROM account_tiers WHERE tier='guest' "
            "AND last_seen_at<? AND cleanup_state=?" + where +
            user_filter + " ORDER BY user_id",
            (cutoff, state, user_id) if user_id else (cutoff, state),
        ).fetchall()
        return [row[0] for row in rows]

    def _claim(self, user_id: str, now: datetime) -> tuple[str, str] | None:
        """Atomically recheck a guest and hold one shared deletion claim.

        Args:
            user_id: Candidate guest identity.
            now: Reference UTC time used as the claim token.

        Returns:
            Prior cleanup state and claim token, or ``None`` if ineligible.
        """
        token = now.isoformat()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT tier,cleanup_state FROM account_tiers WHERE user_id=?" +
                (" FOR UPDATE" if isinstance(self.db, PostgresStatements) else ""), (user_id,),
            ).fetchone()
            claim = self.db.execute(
                "SELECT state,claimed_at FROM guest_cleanup_claims WHERE user_id=?", (user_id,),
            ).fetchone()
            if row is None or row[0] != "guest":
                self.db.rollback()
                return None
            if claim is not None:
                if datetime.fromisoformat(claim[1]) > now - timedelta(hours=1):
                    self.db.rollback()
                    return None
                state = claim[0]
                if state == "deleting" and user_id not in self._eligible_ids(
                    now, user_id, include_deleting=True,
                ):
                    self.db.execute(
                        "DELETE FROM guest_cleanup_claims WHERE user_id=?", (user_id,),
                    )
                    self.db.execute(
                        "UPDATE account_tiers SET cleanup_state='active' WHERE user_id=?",
                        (user_id,),
                    )
                    self.db.commit()
                    return None
            elif row[1] == "active" and user_id in self._eligible_ids(now, user_id):
                state = "deleting"
            else:
                self.db.rollback()
                return None
            self.db.execute(
                "UPDATE account_tiers SET cleanup_state='deleting' WHERE user_id=?", (user_id,),
            )
            self.db.execute(
                "INSERT INTO guest_cleanup_claims (user_id,state,claimed_at) VALUES (?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET state=excluded.state,"
                "claimed_at=excluded.claimed_at",
                (user_id, state, token),
            )
            self.db.commit()
            return state, token
        except BaseException:
            self.db.rollback()
            raise

    def _release(self, user_id: str, token: str, *, remote_deleted: bool = False,
                 member: bool = False) -> None:
        """Release a failed claim while preserving remote-deleted retries.

        Args:
            user_id: Claimed identity.
            token: Current cleanup claim token.
            remote_deleted: Keep a retryable claim after remote deletion.
            member: Restore member tier when the remote identity is a member.
        """
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT 1 FROM guest_cleanup_claims WHERE user_id=? AND claimed_at=?",
                (user_id, token),
            ).fetchone()
            if row:
                if remote_deleted:
                    self.db.execute(
                        "UPDATE guest_cleanup_claims SET state='remote_deleted',claimed_at=? "
                        "WHERE user_id=?", ("1970-01-01T00:00:00+00:00", user_id),
                    )
                else:
                    self.db.execute(
                        "DELETE FROM guest_cleanup_claims WHERE user_id=?", (user_id,),
                    )
                    self.db.execute(
                        "UPDATE account_tiers SET tier=?,cleanup_state='active' "
                        "WHERE user_id=?", ("member" if member else "guest", user_id),
                    )
                self.db.execute(
                    "INSERT INTO guest_cleanup_audit (user_hash,outcome,created_at) "
                    "VALUES (?,?,?)",
                    (hashlib.sha256(user_id.encode()).hexdigest(),
                     "retry" if remote_deleted else "member" if member else "released",
                     datetime.now(UTC).isoformat()),
                )
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def _mark_remote_deleted(self, user_id: str, token: str) -> None:
        """Record completed remote deletion before removing local data."""
        self.db.execute(
            "UPDATE guest_cleanup_claims SET state='remote_deleted' "
            "WHERE user_id=? AND claimed_at=?", (user_id, token),
        )
        self.db.commit()

    def _purge_local(self, user_id: str, token: str) -> None:
        """Remove an identity's owned records after remote Auth deletion.

        The Pi directory is validated and removed before the SQLite deletion
        transaction. A lost or non-deleted claim aborts local record removal.

        Args:
            user_id: Remotely deleted guest.
            token: Matching cleanup claim token.

        Raises:
            ValueError: The claim was lost or a Pi path is outside its root.
        """
        self._remove_pi_files(user_id)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            claim = self.db.execute(
                "SELECT state FROM guest_cleanup_claims WHERE user_id=? AND claimed_at=?",
                (user_id, token),
            ).fetchone()
            if claim is None or claim[0] != "remote_deleted":
                raise ValueError("Cleanup claim was lost")
            tables = self._tables()
            if "agent_events" in tables and "agent_runs" in tables:
                self.db.execute(
                    "DELETE FROM agent_events WHERE run_id IN "
                    "(SELECT id FROM agent_runs WHERE user_id=?)", (user_id,),
                )
            if "mcp_tool_calls" in tables and "agent_runs" in tables:
                self.db.execute(
                    "DELETE FROM mcp_tool_calls WHERE run_id IN "
                    "(SELECT id FROM agent_runs WHERE user_id=?)", (user_id,),
                )
            if "oauth_flows" in tables:
                self.db.execute(
                    "DELETE FROM oauth_flows WHERE guest_user_id=?", (user_id,),
                )
            for table in (
                "agent_session_titles", "agent_messages", "agent_runs", "agent_jobs", "agent_artifact_blobs",
                "agent_approvals", "agent_token_usage", "agent_token_entries",
                "agent_token_limits", "agent_model_call_guards", "agent_gpu_limits",
                "agent_request_keys", "agent_af3_request_keys",
                "agent_gpu_reconciliations", "workspace_projects", "workspace_sessions",
                "workspace_project_skills", "pi_sessions", "catalog_files",
                "catalog_skill_grants", "tool_runs",
                "guest_email_upgrades",
            ):
                if table in tables:
                    self.db.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
            self.db.execute(
                "INSERT INTO guest_cleanup_audit (user_hash,outcome,created_at) "
                "VALUES (?,?,?)",
                (hashlib.sha256(user_id.encode()).hexdigest(), "deleted",
                 datetime.now(UTC).isoformat()),
            )
            self.db.execute("DELETE FROM guest_cleanup_claims WHERE user_id=?", (user_id,))
            self.db.execute("DELETE FROM account_tiers WHERE user_id=?", (user_id,))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def _remove_pi_files(self, user_id: str) -> None:
        """Delete only Pi transcript directories owned by this identity.

        Args:
            user_id: Owner of the session rows.

        Raises:
            ValueError: A session path escapes the configured Pi root.
        """
        if self.pi_session_dir is None:
            return
        tables = self._tables()
        if "pi_sessions" not in tables:
            return
        rows = self.db.execute(
            "SELECT session_id,session_file FROM pi_sessions WHERE user_id=?", (user_id,),
        ).fetchall()
        for session_id, session_file in rows:
            directory = self.pi_session_dir / session_id
            if (directory.is_symlink() or directory.resolve().parent != self.pi_session_dir
                    or not Path(session_file).resolve().is_relative_to(directory.resolve())):
                raise ValueError("Pi session path is outside the configured directory")
            if directory.exists():
                shutil.rmtree(directory)

    async def purge_one(self, user_id: str) -> bool:
        """Delete one stale guest after verifying its remote anonymous status.

        Remote deletion is checkpointed before local cleanup so a later call
        can retry local removal without reviving the identity.

        Args:
            user_id: Candidate anonymous identity.

        Returns:
            Whether remote and local cleanup both completed.
        """
        if self.admin is None:
            return False
        claim = self._claim(user_id, datetime.now(UTC))
        if claim is None:
            return False
        state, token = claim
        remote_deleted = state == "remote_deleted"
        try:
            if not remote_deleted:
                identity = await self.admin.get_user(user_id)
                if identity is not None:
                    if identity.get("id") != user_id or identity.get("is_anonymous") is not True:
                        self._release(
                            user_id, token,
                            member=(identity.get("id") == user_id
                                    and identity.get("is_anonymous") is False),
                        )
                        return False
                    await self.admin.delete_user(user_id)
                remote_deleted = True
                self._mark_remote_deleted(user_id, token)
            self._purge_local(user_id, token)
            return True
        except Exception:  # noqa: BLE001 - release the claim after any remote/provider failure
            self._release(user_id, token, remote_deleted=remote_deleted)
            return False
