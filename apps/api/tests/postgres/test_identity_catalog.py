"""Guest identity, file catalog, and cleanup on shared PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest

from app.contracts.conversation import MessageRequest
from app.db.postgres import PostgresDatabase
from app.db.postgres_migrations import migrate_postgres
from app.domain.catalog import CatalogStore
from app.domain.guest_rate_limit import GuestRateLimiter
from app.domain.identity_policy import GuestAccountDeleting, IdentityPolicyStore
from app.domain.persistent_conversation import PersistentConversationStore
from app.services.guest_cleanup import GuestCleanupService


def test_guest_cleanup_blocks_concurrent_upload_and_run(pg_schema: tuple[str, str]) -> None:
    """Writes wait for the account row lock and reject a deleting guest."""
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    setup = PostgresDatabase(dsn, schema=schema)
    try:
        IdentityPolicyStore(setup).observe_verified_user("guest-1", True)
    finally:
        setup.close()
    started = Event()

    def try_writes() -> tuple[bool, bool, bool]:
        database = PostgresDatabase(dsn, schema=schema)
        try:
            store = PersistentConversationStore(database)
            store.identity_policy = IdentityPolicyStore(database)
            catalog = CatalogStore(db_path=database)
            started.set()
            with pytest.raises(GuestAccountDeleting):
                store.send_message("guest-1", "session-guest-1", MessageRequest(content="run"))
            with pytest.raises(GuestAccountDeleting):
                catalog.add_uploaded_file("guest-1", "notes.txt", b"notes")
            with pytest.raises(GuestAccountDeleting):
                store.create_project("guest-1", "New project", "After cleanup")
            return True, True, True
        finally:
            database.close()

    blocker = PostgresDatabase(dsn, schema=schema)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            with blocker.transaction() as connection:
                connection.execute(
                    "UPDATE account_tiers SET cleanup_state='deleting' WHERE user_id='guest-1'"
                )
                future = executor.submit(try_writes)
                assert started.wait(timeout=5)
                assert not future.done()
            assert future.result(timeout=5) == (True, True, True)
    finally:
        blocker.close()


def test_upgrade_keeps_same_user_and_data(pg_schema: tuple[str, str]) -> None:
    """Email upgrade changes tier in place without moving project or file IDs."""
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    try:
        policy = IdentityPolicyStore(database)
        policy.observe_verified_user("guest-1", True)
        store = PersistentConversationStore(database)
        project = store.create_project("guest-1", "RNA", "Research")
        catalog = CatalogStore(db_path=database)
        file = catalog.add_uploaded_file("guest-1", "notes.txt", b"notes")
        policy.begin_email_upgrade("guest-1", "new@example.org")
        assert policy.complete_email_upgrade("guest-1", "new@example.org")
        policy.observe_verified_user("guest-1", True)
        assert policy.tier_for("guest-1") == "member"
        assert project in store.projects_for("guest-1")
        assert catalog.file_bytes_for("guest-1", file.id) == b"notes"
    finally:
        database.close()


def test_file_bytes_and_skill_grants_survive_reopen(pg_schema: tuple[str, str]) -> None:
    """Original upload bytes and authorized Skills are durable and owner scoped."""
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    first_database = PostgresDatabase(dsn, schema=schema)
    first = CatalogStore(db_path=first_database)
    file = first.add_uploaded_file("alice", "notes.txt", b"alpha\x00beta")
    first.set_skill_grants("alice", ["structure-review"])
    first_database.close()

    second_database = PostgresDatabase(dsn, schema=schema)
    try:
        second = CatalogStore(db_path=second_database)
        assert second.file_bytes_for("alice", file.id) == b"alpha\x00beta"
        assert second.file_content_for("alice", file.id) == "alpha\ufffdbeta"
        assert second.file_bytes_for("bob", file.id) is None
        assert second.files_for("alice") == [file]
        assert [skill.id for skill in second.skills_for("alice")] == ["structure-review"]
    finally:
        second_database.close()


def test_guest_rate_limit_is_shared_across_connections(pg_schema: tuple[str, str]) -> None:
    """Two app instances share the same anonymous creation allowance."""
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    first_database = PostgresDatabase(dsn, schema=schema)
    second_database = PostgresDatabase(dsn, schema=schema)
    try:
        first = GuestRateLimiter(first_database, secret="test", limit_per_hour=1)
        second = GuestRateLimiter(second_database, secret="test", limit_per_hour=1)
        assert first.claim("127.0.0.1")
        assert not second.claim("127.0.0.1")
    finally:
        first_database.close()
        second_database.close()


def test_cleanup_claim_marks_guest_without_touching_auth(pg_schema: tuple[str, str]) -> None:
    """The local claim changes only private PSKit rows, not Supabase Auth."""
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    try:
        policy = IdentityPolicyStore(database)
        policy.observe_verified_user("guest-1", True)
        now = datetime.now(UTC)
        with database.transaction() as connection:
            connection.execute(
                "UPDATE account_tiers SET last_seen_at=%s WHERE user_id='guest-1'",
                ((now - timedelta(days=31)).isoformat(),),
            )
        service = GuestCleanupService(database, admin=None)
        assert service._claim("guest-1", now) is not None
        with database.connection() as connection:
            assert connection.execute(
                "SELECT cleanup_state FROM account_tiers WHERE user_id='guest-1'"
            ).fetchone() == ("deleting",)
    finally:
        database.close()


@pytest.mark.asyncio
async def test_cleanup_removes_owned_state_and_keeps_audit(pg_schema: tuple[str, str]) -> None:
    """A verified stale guest is purged from private tables once."""
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    try:
        policy = IdentityPolicyStore(database)
        policy.observe_verified_user("guest-1", True)
        store = PersistentConversationStore(database)
        project = store.create_project("guest-1", "Private", "")
        file = CatalogStore(db_path=database).add_uploaded_file("guest-1", "notes.txt", b"notes")
        with database.transaction() as connection:
            connection.execute(
                "UPDATE account_tiers SET last_seen_at=%s WHERE user_id='guest-1'",
                ((datetime.now(UTC) - timedelta(days=31)).isoformat(),),
            )

        class Admin:
            def __init__(self) -> None:
                self.deleted: list[str] = []

            async def get_user(self, user_id: str) -> dict:
                return {"id": user_id, "is_anonymous": True}

            async def delete_user(self, user_id: str) -> None:
                self.deleted.append(user_id)

        admin = Admin()
        service = GuestCleanupService(database, admin)
        assert await service.purge_one("guest-1")
        assert not await service.purge_one("guest-1")
        assert admin.deleted == ["guest-1"]
        with database.connection() as connection:
            assert connection.execute(
                "SELECT id FROM workspace_projects WHERE user_id='guest-1'"
            ).fetchall() == []
            assert connection.execute(
                "SELECT id FROM catalog_files WHERE user_id='guest-1'"
            ).fetchall() == []
            assert connection.execute(
                "SELECT outcome FROM guest_cleanup_audit WHERE outcome='deleted'"
            ).fetchall() == [("deleted",)]
        with pytest.raises(GuestAccountDeleting):
            policy.observe_verified_user("guest-1", True)
        with pytest.raises(GuestAccountDeleting):
            store.create_project("guest-1", "Resurrected", "")
        with pytest.raises(GuestAccountDeleting):
            CatalogStore(db_path=database).add_uploaded_file("guest-1", "late.txt", b"late")
        assert project.id and file.id
    finally:
        database.close()
