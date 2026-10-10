"""Release accounting queries against a unique, disposable local PostgreSQL schema."""

import ast
import os
import uuid
from urllib.parse import urlsplit

import pytest

from deploy.agent.scripts import release_control as rc


def probe_queries():
    """Exercise the exact SQL dictionary embedded in the backend read-only probe."""
    for node in ast.walk(ast.parse(rc.DATABASE_PROBE)):
        if isinstance(node, ast.Dict):
            try:
                value = ast.literal_eval(node)
            except (ValueError, TypeError):
                continue
            if isinstance(value, dict) and "jobs_unreconciled" in value:
                return value
    raise AssertionError("Release probe query dictionary is missing")


@pytest.fixture
def accounting_db():
    dsn = os.environ.get("TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip(
            "Set TEST_POSTGRES_DSN for isolated PostgreSQL release accounting tests"
        )
    assert urlsplit(dsn).hostname in {"localhost", "127.0.0.1", "::1"}
    import psycopg
    from psycopg import sql

    schema = "pskit_release_test_" + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            connection.execute(
                sql.SQL("SET search_path TO {}").format(sql.Identifier(schema))
            )
            # Exact production table definitions, not permissive substitutes or real auth/global roles.
            migrations = rc.ROOT / "new_backend/app/db/postgres_migrations"
            connection.execute((migrations / "001_core.sql").read_text())
            connection.execute((migrations / "002_components.sql").read_text())
            connection.execute((migrations / "004_compute.sql").read_text())
            connection.execute((migrations / "007_session_titles.sql").read_text())
            yield connection
        finally:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


def job(connection, accounting):
    connection.execute(
        "INSERT INTO agent_jobs(id,user_id,status,progress,estimated_minutes,created_at,"
        "gpu_accounting_status) VALUES ('job','synthetic-user','completed',100,1,'2026-10-10',%s)",
        (accounting,),
    )


@pytest.mark.parametrize("component", ["mcp", "pdf"])
@pytest.mark.parametrize("expires_at", [1.0, 99999999999.0])
def test_retained_execution_slot_blocks_release_even_after_expiry(
    accounting_db, component, expires_at
):
    # Expiry is not evidence of provider exit. Every retained slot is unresolved.
    accounting_db.execute(
        f"INSERT INTO {component}_execution_leases(token,expires_at) VALUES (%s,%s)",
        ("synthetic-slot", expires_at),
    )
    assert accounting_db.execute(probe_queries()[f"{component}_leases"]).fetchone()[0] == 1


@pytest.mark.parametrize(
    "status,expected",
    [("running", 1), ("unknown", 1), ("future-state", 1), ("", 1),
     ("completed", 0), ("failed", 0)],
)
def test_mcp_call_ledger_preserves_unknown_work_after_slot_release(
    accounting_db, status, expected
):
    accounting_db.execute(
        "INSERT INTO mcp_tool_calls(run_id,tool_call_id,name,arguments_hash,status,created_at) "
        "VALUES ('synthetic-scope','synthetic-call','synthetic-tool','synthetic-hash',%s,'2026-10-10')",
        (status,),
    )
    assert accounting_db.execute(probe_queries()["mcp_leases"]).fetchone()[0] == 0
    assert accounting_db.execute(probe_queries()["mcp_calls_unresolved"]).fetchone()[0] == expected


@pytest.mark.parametrize(
    "status,expected",
    [("running", 1), ("unknown", 1), ("future-state", 1), ("", 1),
     ("waiting", 0), ("pending", 0), ("generated", 0), ("failed", 0), ("manual", 0)],
)
def test_title_call_activity_and_unknown_states_block_release(
    accounting_db, status, expected
):
    accounting_db.execute(
        "INSERT INTO agent_session_titles(session_id,user_id,status,created_at) "
        "VALUES ('synthetic-session','synthetic-user',%s,'2026-10-10')", (status,),
    )
    assert accounting_db.execute(probe_queries()["title_calls"]).fetchone()[0] == expected


@pytest.mark.parametrize(
    "accounting",
    ["reserved", "pending_reconciliation", "unknown", "new-future-state", ""],
)
def test_terminal_unresolved_or_unknown_gpu_accounting_blocks_release(
    accounting_db, accounting
):
    job(accounting_db, accounting)
    assert (
        accounting_db.execute(probe_queries()["jobs_unreconciled"]).fetchone()[0] == 1
    )


@pytest.mark.parametrize("accounting", ["settled", "reconciled", "released"])
def test_known_closed_gpu_accounting_and_legacy_null_minutes_are_valid(
    accounting_db, accounting
):
    job(accounting_db, accounting)
    # Generic compute usage does not use legacy minute charges; released legacy work can have no charge.
    assert (
        accounting_db.execute("SELECT actual_minutes FROM agent_jobs").fetchone()[0]
        is None
    )
    assert (
        accounting_db.execute(probe_queries()["jobs_unreconciled"]).fetchone()[0] == 0
    )


@pytest.mark.parametrize("active,expected", [(True, 1), (False, 0)])
def test_compute_cpu_gpu_reservation_is_checked_even_for_zero_amount(
    accounting_db, active, expected
):
    job(accounting_db, "settled")
    accounting_db.execute(
        "INSERT INTO compute_reservations VALUES ('job','synthetic-user','2026-10-10',0,0,%s)",
        (active,),
    )
    assert (
        accounting_db.execute(probe_queries()["compute_reservations"]).fetchone()[0]
        == expected
    )


@pytest.mark.parametrize(
    "status,expected",
    [
        ("active", 1),
        ("unknown", 1),
        ("future-state", 1),
        ("reported", 0),
        ("unreported", 0),
        ("rejected", 0),
    ],
)
def test_model_call_guard_pending_known_and_conservatively_charged_states(
    accounting_db, status, expected
):
    accounting_db.execute(
        "INSERT INTO agent_runs(id,user_id,session_id,status,created_at) "
        "VALUES ('run','synthetic-user','session','completed','2026-10-10')"
    )
    accounting_db.execute(
        "INSERT INTO agent_model_call_guards VALUES "
        "('call','synthetic-user','run',10,10,20,%s,'2026-10-10')",
        (status,),
    )
    assert (
        accounting_db.execute(probe_queries()["model_calls_unresolved"]).fetchone()[0]
        == expected
    )


@pytest.mark.parametrize(
    "owner_matches,reserved,expected", [(False, 20, 1), (True, -1, 1), (True, 0, 0)]
)
def test_closed_guard_still_requires_owned_run_and_valid_reserved_amount(
    accounting_db, owner_matches, reserved, expected
):
    accounting_db.execute(
        "INSERT INTO agent_runs(id,user_id,session_id,status,created_at) "
        "VALUES ('run','synthetic-user','session','completed','2026-10-10')"
    )
    accounting_db.execute(
        "INSERT INTO agent_model_call_guards VALUES "
        "('call',%s,'run',10,10,%s,'reported','2026-10-10')",
        ("synthetic-user" if owner_matches else "wrong-owner", reserved),
    )
    assert (
        accounting_db.execute(probe_queries()["model_calls_unresolved"]).fetchone()[0]
        == expected
    )
