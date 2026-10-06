"""Real PostgreSQL admission and settlement, including independent worker races."""

import hashlib
import hmac
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest

from app.db.postgres import PostgresDatabase
from app.db.postgres_migrations import migrate_postgres

SECRET = "auth-tests-shared-secret-32-characters-long"


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 6, 12, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def system(pg_schema):
    from app.domain.auth_abuse import AuthAbuseGuard

    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    databases = [PostgresDatabase(dsn, schema=schema) for _ in range(2)]
    clock = Clock()
    try:
        yield (
            [AuthAbuseGuard(db, secret=SECRET, clock=clock) for db in databases],
            clock,
            databases[0],
        )
    finally:
        for db in databases:
            db.close()


def limited(call, retry=None):
    from app.domain.auth_abuse import AuthAbuseLimited

    with pytest.raises(AuthAbuseLimited) as caught:
        call()
    assert caught.value.retry_after > 0
    assert caught.value.rule
    if retry is not None:
        assert caught.value.retry_after == retry
    return caught.value


def test_secret_required_without_database():
    from app.domain.auth_abuse import AuthAbuseGuard

    for secret in ("", "short", "x" * 31):
        with pytest.raises(ValueError, match="secret"):
            AuthAbuseGuard(None, secret=secret)


def test_namespaced_hmac_and_no_raw_identifiers(system):
    (guard, _), _, db = system
    guard.claim_email_send("signup_send", " User+tag@EXAMPLE.com ")
    guard.consume_send_ip("signup_send", "2001:0db8:0:0:0:0:0:1")
    expected = {
        hmac.new(SECRET.encode(), b"email:user+tag@example.com", hashlib.sha256).hexdigest(),
        hmac.new(SECRET.encode(), b"ip:2001:db8::1", hashlib.sha256).hexdigest(),
    }
    with db.connection() as conn:
        assert {
            r[0] for r in conn.execute("SELECT DISTINCT subject_key FROM auth_abuse_buckets")
        } == expected
    limited(lambda: guard.claim_email_send("recovery_send", "user+tag@example.com"), 60)
    guard.claim_email_send("signup_send", "usertag@example.com")


def test_cooldown_crosses_fixed_window_boundary(system):
    (guard, other), clock, _ = system
    clock.advance(59.5)
    guard.claim_email_send("signup_send", "a@example.com")
    clock.advance(1)
    limited(lambda: other.claim_email_send("guest_upgrade_send", "a@example.com"), 59)
    clock.advance(59)
    other.claim_email_send("recovery_send", "a@example.com")


def test_email_hour_and_no_partial_day_deduction(system):
    (guard, _), clock, db = system
    for _ in range(5):
        guard.claim_email_send("signup_send", "a@example.com")
        clock.advance(60)
    limited(lambda: guard.claim_email_send("recovery_send", "a@example.com"), 3300)
    with db.connection() as conn:
        assert sorted(r[0] for r in conn.execute("SELECT count FROM auth_abuse_buckets")) == [5, 5]
    clock.advance(3300)
    guard.claim_email_send("signup_send", "a@example.com")


def test_email_day_and_longest_retry(system):
    (guard, _), clock, _ = system
    for _ in range(2):
        for _ in range(5):
            guard.claim_email_send("signup_send", "a@example.com")
            clock.advance(60)
        clock.advance(3300)
    limited(lambda: guard.claim_email_send("recovery_send", "a@example.com"), 36000)
    clock.now = datetime(2026, 10, 7, tzinfo=UTC)
    guard.claim_email_send("guest_upgrade_send", "a@example.com")


def test_send_ip_windows_share_actions_and_do_not_refund(system):
    (guard, other), clock, db = system
    for _ in range(20):
        guard.consume_send_ip("signup_send", "192.0.2.1")
    limited(lambda: other.consume_send_ip("recovery_send", "192.0.2.1"), 600)
    for _ in range(4):
        clock.advance(600)
        for _ in range(20):
            other.consume_send_ip("guest_upgrade_send", "192.0.2.1")
    limited(lambda: guard.consume_send_ip("signup_send", "192.0.2.1"), 40800)
    claim = guard.claim_email_send("signup_send", "a@example.com")
    guard.settle(claim, "not_sent")
    with db.connection() as conn:
        assert conn.execute(
            "SELECT count FROM auth_abuse_buckets WHERE rule='send_ip_day'"
        ).fetchone() == (100,)


@pytest.mark.parametrize(
    "outcome,refunded",
    [("success", False), ("rejected", False), ("unknown", False), ("not_sent", True)],
)
def test_send_settlement_is_idempotent(system, outcome, refunded):
    (guard, other), _, _ = system
    claim = guard.claim_email_send("signup_send", "a@example.com")
    guard.settle(claim, outcome)
    other.settle(claim, "not_sent")
    if refunded:
        other.claim_email_send("signup_send", "a@example.com")
    else:
        limited(lambda: other.claim_email_send("signup_send", "a@example.com"), 60)


def test_old_send_refund_cannot_remove_new_cooldown(system):
    (guard, _), clock, _ = system
    old = guard.claim_email_send("signup_send", "a@example.com")
    clock.advance(60)
    guard.claim_email_send("signup_send", "a@example.com")
    guard.settle(old, "not_sent")
    limited(lambda: guard.claim_email_send("signup_send", "a@example.com"), 60)


def test_login_account_and_ip_windows(system):
    (guard, _), clock, db = system
    for _ in range(10):
        guard.claim_login("a@example.com", "192.0.2.1")
    limited(lambda: guard.claim_login("a@example.com", "192.0.2.2"), 900)
    with db.connection() as conn:
        assert conn.execute(
            "SELECT count(*) FROM auth_abuse_buckets WHERE rule='login_ip'"
        ).fetchone() == (1,)
    for n in range(20):
        guard.claim_login(f"{n}@example.com", "192.0.2.1")
    limited(lambda: guard.claim_login("z@example.com", "192.0.2.1"), 300)
    clock.advance(300)
    guard.claim_login("z@example.com", "192.0.2.1")
    limited(lambda: guard.claim_login("a@example.com", "192.0.2.2"), 600)
    clock.advance(600)
    guard.claim_login("a@example.com", "192.0.2.2")


@pytest.mark.parametrize("action", ["password_login", "otp_verify", "guest_upgrade_verify"])
@pytest.mark.parametrize(
    "outcome,remaining", [("success", 0), ("not_sent", 0), ("unknown", 1), ("rejected", 1)]
)
def test_attempt_settlement_releases_only_account(system, action, outcome, remaining):
    (guard, other), _, db = system
    claim = (
        guard.claim_login("a@example.com", "192.0.2.1")
        if action == "password_login"
        else guard.claim_verification(action, "a@example.com", "192.0.2.1")
    )
    guard.settle(claim, outcome)
    other.settle(claim, "not_sent")
    with db.connection() as conn:
        counts = dict(conn.execute("SELECT rule, count FROM auth_abuse_buckets"))
        assert counts["login_email" if action == "password_login" else "otp_email"] == remaining
        assert counts["login_ip" if action == "password_login" else "otp_ip"] == 1
        assert conn.execute(
            "SELECT outcome FROM auth_abuse_claims WHERE token=%s", (claim.token,)
        ).fetchone() == ("outcome_unknown" if outcome == "unknown" else outcome,)


def test_otp_lock_outlives_window_and_does_not_extend_on_rejection(system):
    (guard, other), clock, _ = system
    for _ in range(10):
        guard.claim_verification("otp_verify", "a@example.com", "192.0.2.1")
    limited(
        lambda: other.claim_verification("guest_upgrade_verify", "a@example.com", "192.0.2.1"), 900
    )
    clock.advance(600)
    limited(lambda: guard.claim_verification("otp_verify", "a@example.com", "192.0.2.1"), 300)
    clock.advance(300)
    guard.claim_verification("otp_verify", "a@example.com", "192.0.2.1")


def test_two_instances_race_last_login_slot_without_partial_ip_charge(system):
    guards, _, db = system
    for n in range(9):
        guards[0].claim_login("a@example.com", f"192.0.2.{n + 1}")
    barrier = Barrier(2)

    def attempt(n):
        from app.domain.auth_abuse import AuthAbuseLimited

        barrier.wait()
        try:
            guards[n].claim_login("a@example.com", f"198.51.100.{n + 1}")
            return True
        except AuthAbuseLimited:
            return False

    with ThreadPoolExecutor(2) as executor:
        assert sorted(executor.map(attempt, range(2))) == [False, True]
    with db.connection() as conn:
        assert conn.execute(
            "SELECT sum(count) FROM auth_abuse_buckets WHERE rule='login_ip'"
        ).fetchone() == (10,)


def test_two_instances_race_email_cooldown(system):
    guards, _, _ = system
    barrier = Barrier(2)

    def attempt(n):
        from app.domain.auth_abuse import AuthAbuseLimited

        barrier.wait()
        try:
            guards[n].claim_email_send("signup_send", "a@example.com")
            return True
        except AuthAbuseLimited:
            return False

    with ThreadPoolExecutor(2) as executor:
        assert sorted(executor.map(attempt, range(2))) == [False, True]


def test_cleanup_bounded_and_correctness_without_cleanup(system):
    (guard, _), clock, db = system
    old = guard.claim_login("a@example.com", "192.0.2.1")
    clock.advance(86400)
    fresh = guard.claim_login("a@example.com", "192.0.2.1")
    guard.settle(old, "success")
    assert guard.cleanup_expired(limit=1) == 1
    with db.connection() as conn:
        assert conn.execute(
            "SELECT sum(count) FROM auth_abuse_buckets WHERE expires_at > %s", (clock.now,)
        ).fetchone() == (2,)
    assert guard.cleanup_expired() > 0
    assert guard.cleanup_expired() == 0
    guard.settle(fresh, "success")


def test_valid_otp_releases_account_lock_but_keeps_ip_history(system):
    (guard, other), _, db = system
    claims = [
        guard.claim_verification("otp_verify", "a@example.com", "192.0.2.1") for _ in range(10)
    ]
    limited(
        lambda: other.claim_verification("guest_upgrade_verify", "a@example.com", "192.0.2.1"), 900
    )
    guard.settle(claims[-1], "success")
    other.claim_verification("guest_upgrade_verify", "a@example.com", "192.0.2.2")
    with db.connection() as conn:
        assert sorted(
            row[0]
            for row in conn.execute("SELECT count FROM auth_abuse_buckets WHERE rule='otp_ip'")
        ) == [1, 10]
    limited(lambda: other.claim_verification("otp_verify", "b@example.com", "192.0.2.1"), 900)


def test_ip_exhaustion_does_not_relock_verified_account(system):
    (guard, _), _, _ = system
    claims = [
        guard.claim_verification("otp_verify", "a@example.com", "192.0.2.1") for _ in range(10)
    ]
    limited(lambda: guard.claim_verification("otp_verify", "a@example.com", "192.0.2.1"), 900)
    guard.settle(claims[-1], "success")
    limited(lambda: guard.claim_verification("otp_verify", "a@example.com", "192.0.2.1"), 900)
    guard.claim_verification("otp_verify", "a@example.com", "192.0.2.2")


def test_old_window_otp_settlement_cannot_unlock_new_failures(system):
    (guard, _), clock, _ = system
    old = guard.claim_verification("otp_verify", "a@example.com", "192.0.2.1")
    clock.advance(600)
    for _ in range(10):
        guard.claim_verification("otp_verify", "a@example.com", "192.0.2.2")
    limited(lambda: guard.claim_verification("otp_verify", "a@example.com", "192.0.2.3"), 900)
    guard.settle(old, "success")
    clock.advance(10)
    limited(lambda: guard.claim_verification("otp_verify", "a@example.com", "192.0.2.3"), 890)


def test_send_not_sent_refunds_cooldown_across_utc_midnight(system):
    (guard, _), clock, _ = system
    clock.now = datetime(2026, 10, 6, 23, 59, 50, tzinfo=UTC)
    claim = guard.claim_email_send("signup_send", "a@example.com")
    clock.advance(20)
    guard.settle(claim, "not_sent")
    guard.claim_email_send("recovery_send", "a@example.com")


def test_cleanup_skips_claim_with_locked_child_without_cascading(system):
    (guard, _), clock, db = system
    claim = guard.claim_login("a@example.com", "192.0.2.1")
    clock.advance(86400)
    with db.transaction() as conn:
        conn.execute(
            "SELECT * FROM auth_abuse_claim_items WHERE token=%s LIMIT 1 FOR UPDATE", (claim.token,)
        ).fetchall()
        # A separate pool connection must skip both the held child and parent.
        with db.connection() as other:
            other.execute("SET lock_timeout='300ms'")
        guard.cleanup_expired()
    with db.connection() as conn:
        assert conn.execute(
            "SELECT token FROM auth_abuse_claims WHERE token=%s", (claim.token,)
        ).fetchone()
    assert guard.cleanup_expired() == 2


def test_sqlite_export_refuses_to_drop_auth_protection_state(system, pg_schema, tmp_path):
    from scripts.agent_data_migrate import export_sqlite_snapshot

    (guard, _), _, _ = system
    dsn, schema = pg_schema
    guard.claim_email_send("signup_send", "a@example.com")
    with pytest.raises(ValueError, match="Auth abuse.*PostgreSQL backup"):
        export_sqlite_snapshot(dsn, tmp_path / "lossy.sqlite3", schema=schema)
    assert not (tmp_path / "lossy.sqlite3").exists()


def test_admission_uses_window_and_cooldown_time_after_lock_wait(system):
    from app.domain.auth_abuse import AuthAbuseGuard

    (guard, _), clock, db = system
    times = iter(
        [datetime(2026, 10, 6, 23, 59, 59, tzinfo=UTC), datetime(2026, 10, 7, 0, 0, 10, tzinfo=UTC)]
    )
    delayed = AuthAbuseGuard(db, secret=SECRET, clock=lambda: next(times))
    delayed.claim_email_send("signup_send", "a@example.com")
    clock.now = datetime(2026, 10, 7, 0, 0, 10, tzinfo=UTC)
    limited(lambda: guard.claim_email_send("recovery_send", "a@example.com"), 60)
    with db.connection() as conn:
        assert conn.execute(
            "SELECT window_start FROM auth_abuse_buckets WHERE rule='send_email_day'"
        ).fetchone() == (datetime(2026, 10, 7, tzinfo=UTC),)


def test_settlement_uses_fresh_time_after_lock_wait(system):
    from app.domain.auth_abuse import AuthAbuseGuard

    (guard, other), clock, db = system
    old = guard.claim_verification("otp_verify", "a@example.com", "192.0.2.1")
    clock.advance(600)
    for _ in range(10):
        other.claim_verification("otp_verify", "a@example.com", "192.0.2.2")
    limited(lambda: other.claim_verification("otp_verify", "a@example.com", "192.0.2.3"), 900)
    times = iter(
        [
            datetime(2026, 10, 6, 12, 9, 59, tzinfo=UTC),
            datetime(2026, 10, 6, 12, 10, 10, tzinfo=UTC),
        ]
    )
    delayed = AuthAbuseGuard(db, secret=SECRET, clock=lambda: next(times))
    delayed.settle(old, "success")
    clock.advance(10)
    limited(lambda: other.claim_verification("otp_verify", "a@example.com", "192.0.2.3"), 890)
