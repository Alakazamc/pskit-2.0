import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from app.db.migrations import migrate_identity_policy_schema

AccountTier = Literal["guest", "member"]


class GuestAccountDeleting(Exception):
    """The guest is locked while its remote and local records are removed."""


class IdentityPolicyStore:
    """Persist guest/member tiers and guest email upgrade state."""

    def __init__(
        self, path: str, *, guest_token_limit: int = 20_000, guest_gpu_limit: int = 0,
        member_token_limit: int = 1_000_000, member_gpu_limit: int = 60,
        guest_max_active_runs: int = 1,
    ) -> None:
        """Open the identity policy database and migrate its tables.

        Args:
            path: SQLite database shared with workspaces and Runs.
            guest_token_limit: Default monthly guest Token allowance.
            guest_gpu_limit: Default daily guest GPU minutes.
            member_token_limit: Default monthly member Token allowance.
            member_gpu_limit: Default daily member GPU minutes.
            guest_max_active_runs: Maximum concurrent Runs for a guest.
        """
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.guest_token_limit = guest_token_limit
        self.guest_gpu_limit = guest_gpu_limit
        self.member_token_limit = member_token_limit
        self.member_gpu_limit = member_gpu_limit
        self.guest_max_active_runs = guest_max_active_runs
        migrate_identity_policy_schema(self.db)

    def observe_verified_user(self, user_id: str, is_anonymous: bool | None) -> None:
        """Record verified identity activity without demoting an existing member.

        A cleanup claim or completed deletion blocks further local access.

        Args:
            user_id: Verified Supabase or mock identity ID.
            is_anonymous: Explicit false for members; true or unknown for guests.

        Raises:
            GuestAccountDeleting: Cleanup owns or deleted this guest identity.
        """
        tier = "member" if is_anonymous is False else "guest"
        seen_at = datetime.now(UTC).isoformat()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            deleted = self.db.execute(
                "SELECT 1 FROM guest_cleanup_audit WHERE user_hash=? "
                "AND outcome='deleted' LIMIT 1",
                (hashlib.sha256(user_id.encode()).hexdigest(),),
            ).fetchone()
            if deleted:
                raise GuestAccountDeleting
            state = self.db.execute(
                "SELECT cleanup_state FROM account_tiers WHERE user_id=?", (user_id,),
            ).fetchone()
            if state is not None and state[0] == "deleting":
                raise GuestAccountDeleting
            self.db.execute(
                "INSERT INTO account_tiers (user_id,tier,last_seen_at) VALUES (?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET "
                "tier=CASE WHEN account_tiers.tier='member' THEN 'member' "
                "ELSE excluded.tier END, "
                "last_seen_at=excluded.last_seen_at",
                (user_id, tier, seen_at),
            )
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def tier_for(self, user_id: str) -> AccountTier:
        """Return the verified tier, treating an unseen account as a guest."""
        row = self.db.execute(
            "SELECT tier FROM account_tiers WHERE user_id=?", (user_id,),
        ).fetchone()
        return row[0] if row else "guest"

    def last_seen_for(self, user_id: str) -> datetime | None:
        """Return the last verified activity timestamp for an account."""
        row = self.db.execute(
            "SELECT last_seen_at FROM account_tiers WHERE user_id=?", (user_id,),
        ).fetchone()
        return datetime.fromisoformat(row[0]) if row else None

    def default_token_limit_for(self, user_id: str) -> int:
        """Select the monthly Token default for the account's tier."""
        return self.guest_token_limit if self.tier_for(user_id) == "guest" else self.member_token_limit

    def default_gpu_limit_for(self, user_id: str) -> int:
        """Select the daily GPU minute default for the account's tier."""
        return self.guest_gpu_limit if self.tier_for(user_id) == "guest" else self.member_gpu_limit

    def begin_email_upgrade(self, user_id: str, email: str) -> None:
        """Record a guest's pending email binding under a write lock.

        Args:
            user_id: Guest identity that will keep its ID after upgrade.
            email: Email address awaiting verification.

        Raises:
            GuestAccountDeleting: Cleanup has claimed the guest.
            ValueError: The identity is already a member.
        """
        self.db.execute("BEGIN IMMEDIATE")
        try:
            state = self.db.execute(
                "SELECT cleanup_state FROM account_tiers WHERE user_id=?", (user_id,),
            ).fetchone()
            if state is not None and state[0] == "deleting":
                raise GuestAccountDeleting
            if self.tier_for(user_id) != "guest":
                raise ValueError("Only guests can upgrade")
            self.db.execute(
                "INSERT INTO guest_email_upgrades (user_id,email,started_at) VALUES (?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET email=excluded.email,"
                "started_at=excluded.started_at",
                (user_id, email, datetime.now(UTC).isoformat()),
            )
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def pending_email_for(self, user_id: str) -> str | None:
        """Return the email awaiting verification for a guest, if any."""
        row = self.db.execute(
            "SELECT email FROM guest_email_upgrades WHERE user_id=?", (user_id,),
        ).fetchone()
        return row[0] if row else None

    def complete_email_upgrade(self, user_id: str, email: str) -> bool:
        """Promote a guest only when the pending email exactly matches.

        Args:
            user_id: Guest identity to promote in place.
            email: Email confirmed by the identity provider.

        Returns:
            Whether a matching pending guest upgrade was committed.

        Raises:
            GuestAccountDeleting: Cleanup has claimed the guest.
        """
        self.db.execute("BEGIN IMMEDIATE")
        try:
            state = self.db.execute(
                "SELECT cleanup_state FROM account_tiers WHERE user_id=?", (user_id,),
            ).fetchone()
            if state is not None and state[0] == "deleting":
                raise GuestAccountDeleting
            row = self.db.execute(
                "SELECT email FROM guest_email_upgrades WHERE user_id=?", (user_id,),
            ).fetchone()
            if row is None or row[0] != email or self.tier_for(user_id) != "guest":
                self.db.rollback()
                return False
            self.db.execute("DELETE FROM guest_email_upgrades WHERE user_id=?", (user_id,))
            self.db.execute(
                "UPDATE account_tiers SET tier='member',last_seen_at=? WHERE user_id=?",
                (datetime.now(UTC).isoformat(), user_id),
            )
            self.db.commit()
            return True
        except BaseException:
            self.db.rollback()
            raise
