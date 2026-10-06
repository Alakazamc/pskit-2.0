"""Private PostgreSQL schema and migration contract."""

from importlib.resources import files

import psycopg
import pytest
from psycopg import sql

from app.db.migrations.components import (
    migrate_catalog_schema,
    migrate_identity_policy_schema,
    migrate_internal_tool_auth_schema,
    migrate_mcp_capacity_schema,
    migrate_mcp_tool_calls_schema,
    migrate_oauth_schema,
    migrate_pdf_capacity_schema,
    migrate_tool_run_schema,
)
from app.db.postgres import PostgresDatabase
from app.db.postgres_migrations import SCHEMA_VERSION, migrate_postgres
from app.domain.guest_rate_limit import GuestRateLimiter
from app.domain.persistent_conversation.store import PersistentConversationStore

EXPECTED_TABLES = {
    "agent_messages", "agent_runs", "agent_events", "pi_sessions", "agent_jobs",
    "agent_artifact_blobs", "agent_approvals", "agent_token_usage", "agent_token_entries",
    "agent_token_limits", "agent_model_call_guards", "agent_gpu_limits", "agent_request_keys",
    "workspace_projects", "workspace_sessions", "workspace_project_skills",
    "agent_af3_request_keys", "agent_gpu_reconciliations", "account_tiers",
    "guest_email_upgrades", "guest_cleanup_claims", "guest_cleanup_audit",
    "catalog_files", "catalog_skill_grants", "catalog_skill_versions", "oauth_flows",
    "tool_runs", "mcp_capacity_config", "mcp_execution_leases", "pdf_capacity_config",
    "pdf_execution_leases", "mcp_tool_calls", "internal_tool_auth",
    "guest_creation_events", "schema_migrations",
    "agent_session_titles",
    "auth_abuse_buckets", "auth_abuse_locks", "auth_abuse_claims", "auth_abuse_claim_items",
    "mcp_service_endpoints", "mcp_service_revisions", "mcp_probe_snapshots",
    "mcp_discovery_snapshots",
    "tool_products", "tool_product_revisions", "capability_bindings",
    "acceptance_suites", "acceptance_cases", "qualification_reports",
    "qualification_case_results", "tool_product_releases", "review_decisions",
    "tool_product_release_capabilities", "tool_product_runs", "tool_product_run_steps",
    "tool_run_events", "tool_product_run_artifacts", "tool_product_run_usage",
}


def test_private_schema_and_version(pg_schema: tuple[str, str]) -> None:
    """Migrations create all business tables privately and remain idempotent."""
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    try:
        database.check_schema_version(SCHEMA_VERSION)
        with database.connection() as connection:
            tables = {
                row[0] for row in connection.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname=%s", (schema,)
                )
            }
            assert EXPECTED_TABLES <= tables
            assert not (EXPECTED_TABLES - {"schema_migrations"}) & {
                row[0] for row in connection.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public'"
                )
            }
            assert connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall() == [(version,) for version in range(1, SCHEMA_VERSION + 1)]
            assert connection.execute(
                "SELECT is_identity FROM information_schema.columns "
                "WHERE table_schema=%s AND table_name='agent_token_entries' AND column_name='id'",
                (schema,),
            ).fetchone() == ("YES",)
            for table in (
                "agent_messages", "agent_jobs", "catalog_files", "mcp_tool_calls",
                "agent_model_call_guards",
            ):
                assert connection.execute(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema=%s AND table_name=%s AND column_name='ordinal'",
                    (schema, table),
                ).fetchone()
            connection.execute(
                sql.SQL("INSERT INTO {}.agent_jobs "
                        "(id,user_id,run_id,tool_call_id,status,progress,estimated_minutes,created_at) "
                        "VALUES ('one','u','r','call','queued',0,1,'now')").format(sql.Identifier(schema))
            )
        with psycopg.connect(dsn) as independent:
            with pytest.raises(psycopg.errors.UniqueViolation):
                independent.execute(
                    sql.SQL("INSERT INTO {}.agent_jobs "
                            "(id,user_id,run_id,tool_call_id,status,progress,estimated_minutes,created_at) "
                            "VALUES ('two','u','r','call','queued',0,1,'now')").format(
                                sql.Identifier(schema)
                            )
                )
            independent.rollback()
    finally:
        database.close()
    migrate_postgres(dsn, schema=schema)
    with psycopg.connect(dsn) as connection:
        assert connection.execute(
            sql.SQL("SELECT count(*) FROM {}.schema_migrations").format(sql.Identifier(schema))
        ).fetchone() == (SCHEMA_VERSION,)
        assert connection.execute(
            sql.SQL("SELECT count(*) FROM {}.agent_jobs").format(sql.Identifier(schema))
        ).fetchone() == (1,)


def test_postgres_covers_every_live_sqlite_table_and_column(pg_schema: tuple[str, str]) -> None:
    """The replacement schema has a slot for every persisted SQLite field."""
    dsn, schema = pg_schema
    sqlite_store = PersistentConversationStore(":memory:")
    try:
        for migrate in (
            migrate_identity_policy_schema,
            migrate_catalog_schema,
            migrate_oauth_schema,
            migrate_tool_run_schema,
            migrate_mcp_capacity_schema,
            migrate_pdf_capacity_schema,
            migrate_mcp_tool_calls_schema,
            migrate_internal_tool_auth_schema,
        ):
            migrate(sqlite_store.db)
        source_tables = {
            name for (name,) in sqlite_store.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        limiter = GuestRateLimiter(":memory:", secret="test-only", limit_per_hour=2)
        try:
            source_tables.update(
                name for (name,) in limiter.db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            )
        finally:
            limiter.db.close()
        migrate_postgres(dsn, schema=schema)
        with psycopg.connect(dsn) as connection:
            target_tables = {
                row[0] for row in connection.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname=%s", (schema,)
                )
            }
            assert source_tables <= target_tables
            for table in source_tables - {"guest_creation_events"}:
                source_columns = {
                    row[1] for row in sqlite_store.db.execute(f"PRAGMA table_info({table})")
                }
                target_columns = {
                    row[0] for row in connection.execute(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema=%s AND table_name=%s", (schema, table),
                    )
                }
                assert source_columns <= target_columns, (table, source_columns - target_columns)
    finally:
        sqlite_store.db.close()


def test_quota_migration_upgrades_existing_version_two_schema(pg_schema: tuple[str, str]) -> None:
    """Version three adds a stable model-call order without losing old guards."""
    dsn, schema = pg_schema
    with psycopg.connect(dsn) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        connection.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema)))
        connection.execute("CREATE TABLE schema_migrations (version integer PRIMARY KEY, applied_at text)")
        for version, name in ((1, "001_core.sql"), (2, "002_components.sql")):
            connection.execute(
                files("app.db.postgres_migrations").joinpath(name).read_text(encoding="utf-8")
            )
            connection.execute(
                "INSERT INTO schema_migrations (version) VALUES (%s)", (version,)
            )
        connection.execute(
            "INSERT INTO agent_model_call_guards "
            "(call_id,user_id,run_id,prompt_bytes,max_output_tokens,reserved_tokens,status,created_at) "
            "VALUES ('old-call','alice','run-1',1,2,3,'active','now')"
        )
    migrate_postgres(dsn, schema=schema)
    with psycopg.connect(dsn) as connection:
        assert connection.execute(
            sql.SQL("SELECT call_id,ordinal FROM {}.agent_model_call_guards").format(
                sql.Identifier(schema)
            )
        ).fetchone() == ("old-call", 1)


def test_auth_abuse_schema_contract(pg_schema):
    dsn, schema = pg_schema
    assert SCHEMA_VERSION == 10
    migrate_postgres(dsn, schema=schema)
    with psycopg.connect(dsn) as conn:
        columns = {row[0] for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name LIKE 'auth_abuse_%%'", (schema,)
        )}
        assert not columns & {"email", "ip", "password", "otp", "captcha_token"}
        for table in ("auth_abuse_buckets", "auth_abuse_locks", "auth_abuse_claims", "auth_abuse_claim_items"):
            assert conn.execute(
                "SELECT 1 FROM pg_indexes WHERE schemaname=%s AND tablename=%s AND indexdef LIKE '%%expires_at%%'", (schema, table)
            ).fetchone()


def test_tool_product_schema_has_owned_foreign_keys(pg_schema):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    with psycopg.connect(dsn) as connection:
        foreign_keys = {
            (row[0], row[1])
            for row in connection.execute(
                "SELECT tc.table_name, ccu.table_name "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.constraint_column_usage ccu "
                "ON ccu.constraint_schema=tc.constraint_schema AND ccu.constraint_name=tc.constraint_name "
                "WHERE tc.constraint_schema=%s AND tc.constraint_type='FOREIGN KEY'",
                (schema,),
            )
        }
    assert {
        ("tool_product_revisions", "tool_products"),
        ("capability_bindings", "tool_product_revisions"),
        ("qualification_reports", "tool_product_revisions"),
        ("tool_product_releases", "tool_product_revisions"),
        ("tool_product_release_capabilities", "tool_product_releases"),
        ("tool_product_runs", "tool_product_releases"),
        ("tool_product_run_steps", "tool_product_runs"),
        ("tool_run_events", "tool_product_runs"),
    } <= foreign_keys


def test_database_pool_does_not_auto_migrate_tool_products(pg_schema):
    dsn, schema = pg_schema
    with psycopg.connect(dsn) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    database = PostgresDatabase(dsn, schema=schema)
    try:
        with pytest.raises(RuntimeError, match="not migrated"):
            database.check_schema_version(SCHEMA_VERSION)
        with database.connection() as connection:
            assert connection.execute("SELECT to_regclass('tool_products')").fetchone() == (None,)
    finally:
        database.close()
