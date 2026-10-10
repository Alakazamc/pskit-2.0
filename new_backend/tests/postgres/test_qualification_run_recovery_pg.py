"""Qualification recovery and ordinary admission on isolated PostgreSQL 17."""

import pytest
from test_qualification_run_recovery import (
    _ordinary_recovery_case,
    _queued_prefix_case,
    _recovery_case,
    _resume_prefix_case,
    _unknown_charge_case,
)

from app.db.postgres import PostgresDatabase
from app.db.postgres_migrations import migrate_postgres
from app.domain.persistent_conversation import PersistentConversationStore


@pytest.fixture
def store(pg_schema):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    try:
        yield PersistentConversationStore(database)
    finally:
        database.close()


@pytest.mark.parametrize("trigger", ["expired", "shutdown", "transient"])
@pytest.mark.parametrize("unknown_status", ["active", "unreported"])
def test_real_driver_interruption_retains_unknown_charge_pg(store, trigger, unknown_status):
    _recovery_case(store, trigger, unknown_status)


def test_partial_observation_cannot_refund_unknown_qualification_guard_pg(store):
    _unknown_charge_case(store)


def test_qualification_initial_prefix_never_reaches_ordinary_worker_pg(store):
    _queued_prefix_case(store)


def test_qualification_resume_prefix_never_reaches_ordinary_worker_pg(store):
    _resume_prefix_case(store)


def test_ordinary_expired_run_still_retries_pg(store):
    _ordinary_recovery_case(store)
