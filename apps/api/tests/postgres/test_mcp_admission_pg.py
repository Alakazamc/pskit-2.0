"""Separate PG instances share release holds for direct MCP and PDF work."""

import pytest
from test_release_mcp_admission import identity

from app.contracts.capabilities import McpInvokeResult
from app.db.postgres import PostgresDatabase
from app.db.postgres_migrations import migrate_postgres
from app.db.release_admission_migrations import migrate_admission_postgres
from app.domain.mcp_capacity import McpCapacityStore, PdfCapacityStore
from app.domain.mcp_tool_calls import McpToolCallStore
from app.domain.release_admission import AdmissionClosed, DurableAdmissionGate


def gate(database):
    migrate_admission_postgres(database.pool.conninfo, schema=database.schema)
    component = DurableAdmissionGate(database, environment="staging", project="pskit-agent-staging",
                                     qualification_sha256="a" * 64)
    component.configure()
    return component


@pytest.mark.parametrize("store_type", [McpCapacityStore, PdfCapacityStore])
def test_gate_on_one_instance_blocks_capacity_on_another(pg_schema, store_type):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    databases = [PostgresDatabase(dsn, schema=schema) for _ in range(2)]
    try:
        gates = [gate(database) for database in databases]
        stores = [store_type(database, 2, admission_gate=component)
                  for database, component in zip(databases, gates, strict=True)]
        token = stores[1].claim(30)
        gates[0].enter(identity())
        with pytest.raises(AdmissionClosed):
            stores[1].claim(30)
        assert stores[1].renew(token, 30)
        stores[1].release(token)
        with databases[0].connection() as connection:
            assert connection.execute(f"SELECT count(*) FROM {stores[0].lease_table}").fetchone()[0] == 0
        gates[0].release(identity())
        assert stores[1].claim(30)
    finally:
        for database in databases:
            database.close()


def test_completed_mcp_ack_replays_during_hold_but_new_claim_rolls_back(pg_schema):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    databases = [PostgresDatabase(dsn, schema=schema) for _ in range(2)]
    try:
        gates = [gate(database) for database in databases]
        stores = [McpToolCallStore(database, admission_gate=component)
                  for database, component in zip(databases, gates, strict=True)]
        result = McpInvokeResult(tool="synthetic", result={"safe": True})
        stores[0].claim("run", "old", "synthetic", {})
        stores[0].complete("run", "old", result)
        gates[0].enter(identity())
        assert stores[1].claim("run", "old", "synthetic", {}) == ("completed", result)
        with pytest.raises(AdmissionClosed):
            stores[1].claim("run", "new", "synthetic", {})
        with databases[0].connection() as connection:
            assert connection.execute("SELECT count(*) FROM mcp_tool_calls").fetchone()[0] == 1
        gates[0].release(identity())
        assert stores[1].claim("run", "new", "synthetic", {}) == ("claimed", None)
    finally:
        for database in databases:
            database.close()
