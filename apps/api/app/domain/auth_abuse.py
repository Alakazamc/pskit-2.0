"""Shared PostgreSQL auth admission with namespaced identifiers and durable claims."""

from __future__ import annotations

import hashlib
import hmac
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from typing import Literal
from uuid import uuid4

from psycopg import sql

from app.db.postgres import PostgresDatabase

AuthAction = Literal[
    "signup_send",
    "recovery_send",
    "guest_upgrade_send",
    "password_login",
    "otp_verify",
    "guest_upgrade_verify",
]
AuthOutcome = Literal["success", "rejected", "not_sent", "unknown"]
_SEND_ACTIONS = {"signup_send", "recovery_send", "guest_upgrade_send"}
_VERIFY_ACTIONS = {"otp_verify", "guest_upgrade_verify"}


@dataclass(frozen=True)
class AuthClaim:
    token: str
    action: AuthAction


class AuthAbuseLimited(Exception):
    """The longest wait among the admission rules that currently reject a request."""

    def __init__(self, retry_after: int, rule: str) -> None:
        self.retry_after = max(1, retry_after)
        self.rule = rule
        super().__init__(f"Auth request limited by {rule}")


@dataclass(frozen=True)
class _Bucket:
    rule: str
    subject: str
    start: datetime
    expires: datetime
    limit: int
    refundable: bool


class AuthAbuseGuard:
    """Use one shared secret and database for every application worker.

    Send IP admission is intentionally separate: it runs before CAPTCHA and
    is never refunded. Email admission runs only after a valid challenge.
    """

    def __init__(
        self,
        database: PostgresDatabase,
        *,
        secret: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not secret or len(secret) < 32:
            raise ValueError("Auth abuse secret must contain at least 32 characters")
        self.database = database
        self._secret = secret.encode()
        self._clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Auth abuse clock must return a timezone-aware datetime")
        return now.astimezone(UTC)

    def _key(self, namespace: str, value: str) -> str:
        normalized = (
            value.strip().lower() if namespace == "email" else str(ip_address(value.strip()))
        )
        if not normalized:
            raise ValueError("Auth abuse identifier is required")
        return hmac.new(
            self._secret, f"{namespace}:{normalized}".encode(), hashlib.sha256
        ).hexdigest()

    @staticmethod
    def _bucket(
        rule: str, subject: str, now: datetime, seconds: int, limit: int, refundable: bool
    ) -> _Bucket:
        start = datetime.fromtimestamp(math.floor(now.timestamp() / seconds) * seconds, UTC)
        return _Bucket(rule, subject, start, start + timedelta(seconds=seconds), limit, refundable)

    def _lock(self, conn, keys: list[tuple[str, str]]) -> None:
        # Lock signed integers in sorted order, including hash collisions. Claims
        # and refunds take the same subject locks across fixed-window boundaries.
        locks = sorted(
            {
                int.from_bytes(
                    hashlib.sha256(
                        f"auth-abuse:{self.database.schema}:{rule}:{subject}".encode()
                    ).digest()[:8],
                    "big",
                    signed=True,
                )
                for rule, subject in keys
            }
        )
        for key in locks:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (key,))

    def consume_send_ip(self, action: AuthAction, client_ip: str) -> None:
        if action not in _SEND_ACTIONS:
            raise ValueError("Expected an email send action")
        now = self._now()
        subject = self._key("ip", client_ip)
        self._claim(
            action,
            now,
            [
                self._bucket("send_ip_10m", subject, now, 600, 20, False),
                self._bucket("send_ip_day", subject, now, 86400, 100, False),
            ],
            tracked=False,
        )

    def claim_email_send(self, action: AuthAction, email: str) -> AuthClaim:
        if action not in _SEND_ACTIONS:
            raise ValueError("Expected an email send action")
        now = self._now()
        subject = self._key("email", email)
        return self._claim(
            action,
            now,
            [
                self._bucket("send_email_hour", subject, now, 3600, 5, True),
                self._bucket("send_email_day", subject, now, 86400, 10, True),
            ],
            lock=("send_email_cooldown", subject),
            cooldown=60,
        )

    def claim_login(self, email: str, client_ip: str) -> AuthClaim:
        now = self._now()
        return self._claim(
            "password_login",
            now,
            [
                self._bucket("login_email", self._key("email", email), now, 900, 10, True),
                self._bucket("login_ip", self._key("ip", client_ip), now, 300, 30, False),
            ],
        )

    def claim_verification(self, action: AuthAction, email: str, client_ip: str) -> AuthClaim:
        if action not in _VERIFY_ACTIONS:
            raise ValueError("Expected an OTP verification action")
        now = self._now()
        subject = self._key("email", email)
        return self._claim(
            action,
            now,
            [
                self._bucket("otp_email", subject, now, 600, 10, True),
                self._bucket("otp_ip", self._key("ip", client_ip), now, 600, 10, False),
            ],
            otp=True,
        )

    def _claim(
        self,
        action: AuthAction,
        now: datetime,
        buckets: list[_Bucket],
        *,
        lock: tuple[str, str] | None = None,
        cooldown: int = 0,
        otp: bool = False,
        tracked: bool = True,
    ) -> AuthClaim:
        token = uuid4().hex
        denied: list[tuple[int, str]] = []
        with self.database.transaction() as conn:
            locks = (
                [(f"{b.rule}_lock", b.subject) for b in buckets]
                if otp
                else ([lock] if lock else [])
            )
            self._lock(conn, [(b.rule, b.subject) for b in buckets] + locks)
            # Lock waits may cross UTC windows. Subjects are stable, so rebuild
            # windows under the held locks and begin cooldowns at admission time.
            now = self._now()
            buckets = [
                self._bucket(
                    b.rule,
                    b.subject,
                    now,
                    int((b.expires - b.start).total_seconds()),
                    b.limit,
                    b.refundable,
                )
                for b in buckets
            ]
            active_locks = set()
            for lock_key in locks:
                row = conn.execute(
                    "SELECT expires_at FROM auth_abuse_locks WHERE rule=%s AND subject_key=%s",
                    lock_key,
                ).fetchone()
                if row and row[0] > now:
                    active_locks.add(lock_key)
                    denied.append((math.ceil((row[0] - now).total_seconds()), lock_key[0]))
            for bucket in buckets:
                row = conn.execute(
                    "SELECT count FROM auth_abuse_buckets WHERE rule=%s AND subject_key=%s AND window_start=%s",
                    (bucket.rule, bucket.subject, bucket.start),
                ).fetchone()
                if row and row[0] >= bucket.limit:
                    denied.append((math.ceil((bucket.expires - now).total_seconds()), bucket.rule))
                    if otp:
                        lock_key = (f"{bucket.rule}_lock", bucket.subject)
                        if lock_key not in active_locks:
                            # Only the exhausted dimension is locked. Retained
                            # IP history must not recreate a verified email lock.
                            self._write_lock(conn, lock_key, now + timedelta(seconds=900), None)
                            denied.append((900, lock_key[0]))
            if not denied:
                if tracked:
                    expires = max(
                        max(b.expires for b in buckets), now + timedelta(seconds=cooldown)
                    ) + timedelta(seconds=900 if otp else 0)
                    conn.execute(
                        "INSERT INTO auth_abuse_claims (token, action, expires_at) VALUES (%s,%s,%s)",
                        (token, action, expires),
                    )
                for bucket in buckets:
                    conn.execute(
                        "INSERT INTO auth_abuse_buckets (rule,subject_key,window_start,count,expires_at) "
                        "VALUES (%s,%s,%s,1,%s) ON CONFLICT (rule,subject_key,window_start) "
                        "DO UPDATE SET count=auth_abuse_buckets.count+1",
                        (bucket.rule, bucket.subject, bucket.start, bucket.expires),
                    )
                    if tracked:
                        conn.execute(
                            "INSERT INTO auth_abuse_claim_items "
                            "(token,rule,subject_key,window_start,refundable,expires_at) VALUES (%s,%s,%s,%s,%s,%s)",
                            (
                                token,
                                bucket.rule,
                                bucket.subject,
                                bucket.start,
                                bucket.refundable,
                                expires,
                            ),
                        )
                if cooldown:
                    self._write_lock(conn, lock, now + timedelta(seconds=cooldown), token)
        # Raising after commit retains an OTP lock while all denied admission
        # counters remain untouched. A failed transaction never returns a claim.
        if denied:
            retry, rule = max(denied)
            raise AuthAbuseLimited(retry, rule)
        return AuthClaim(token, action)

    @staticmethod
    def _write_lock(conn, lock: tuple[str, str], expires: datetime, token: str | None) -> None:
        conn.execute(
            "INSERT INTO auth_abuse_locks (rule,subject_key,expires_at,claim_token) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (rule,subject_key) DO UPDATE SET expires_at=EXCLUDED.expires_at, claim_token=EXCLUDED.claim_token",
            (*lock, expires, token),
        )

    def settle(self, claim: AuthClaim, outcome: AuthOutcome) -> None:
        """Settle once; only definite pre-send failures refund email sends."""
        if outcome not in {"success", "rejected", "not_sent", "unknown"}:
            raise ValueError("Invalid auth claim outcome")
        with self.database.transaction() as conn:
            row = conn.execute(
                "SELECT action,outcome,expires_at FROM auth_abuse_claims WHERE token=%s FOR UPDATE",
                (claim.token,),
            ).fetchone()
            now = self._now()
            if row is None or row[1] is not None or row[2] <= now:
                return
            if row[0] != claim.action:
                raise ValueError("Auth claim action mismatch")
            items = conn.execute(
                "SELECT rule,subject_key,window_start FROM auth_abuse_claim_items WHERE token=%s AND refundable",
                (claim.token,),
            ).fetchall()
            release = outcome == "not_sent" or (
                outcome == "success" and claim.action not in _SEND_ACTIONS
            )
            if release:
                keys = [(rule, subject) for rule, subject, _ in items]
                lock_rule = (
                    "send_email_cooldown"
                    if claim.action in _SEND_ACTIONS
                    else "otp_email_lock"
                    if claim.action in _VERIFY_ACTIONS
                    else None
                )
                if lock_rule:
                    keys += [(lock_rule, subject) for _, subject, _ in items]
                self._lock(conn, keys)
                now = self._now()
                if row[2] <= now:
                    return
                for rule, subject, start in items:
                    updated = conn.execute(
                        "UPDATE auth_abuse_buckets SET count=count-1 "
                        "WHERE rule=%s AND subject_key=%s AND window_start=%s AND count>0 AND expires_at>%s",
                        (rule, subject, start, now),
                    )
                    if lock_rule and (claim.action in _SEND_ACTIONS or updated.rowcount):
                        conn.execute(
                            "DELETE FROM auth_abuse_locks WHERE rule=%s AND subject_key=%s "
                            "AND (claim_token=%s OR (%s AND claim_token IS NULL))",
                            (lock_rule, subject, claim.token, claim.action in _VERIFY_ACTIONS),
                        )
            conn.execute(
                "UPDATE auth_abuse_claims SET outcome=%s WHERE token=%s",
                ("outcome_unknown" if outcome == "unknown" else outcome, claim.token),
            )

    def cleanup_expired(self, limit: int = 1000) -> int:
        """Delete at most limit expired rows; live decisions never require cleanup."""
        if limit < 1:
            raise ValueError("Auth cleanup limit must be positive")
        now = self._now()
        deleted = 0
        with self.database.transaction() as conn:
            # Skip active settlements; cascade items belong to the parent claim.
            # Delete children explicitly first to keep the total row bound exact.
            for table in (
                "auth_abuse_claim_items",
                "auth_abuse_claims",
                "auth_abuse_locks",
                "auth_abuse_buckets",
            ):
                if deleted == limit:
                    break
                # Never cascade through children another cleanup/settlement
                # currently holds; SKIP LOCKED alone does not cover FK cascades.
                child_filter = (
                    sql.SQL(
                        " AND NOT EXISTS (SELECT 1 FROM auth_abuse_claim_items i "
                        "WHERE i.token=auth_abuse_claims.token)"
                    )
                    if table == "auth_abuse_claims"
                    else sql.SQL("")
                )
                result = conn.execute(
                    sql.SQL(
                        "WITH expired AS (SELECT ctid FROM {} WHERE expires_at<=%s {} "
                        "ORDER BY expires_at LIMIT %s FOR UPDATE SKIP LOCKED) "
                        "DELETE FROM {} WHERE ctid IN (SELECT ctid FROM expired)"
                    ).format(sql.Identifier(table), child_filter, sql.Identifier(table)),
                    (now, limit - deleted),
                )
                deleted += result.rowcount
        return deleted
