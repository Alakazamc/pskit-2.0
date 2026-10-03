"""Live app and auxiliary stores share one PostgreSQL persistence boundary."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import httpx
import pytest

from app.api.auth import OAuthFlowStore
from app.config import Settings
from app.contracts.capabilities import McpInvokeResult
from app.db.postgres import PostgresDatabase
from app.db.postgres_migrations import migrate_postgres
from app.domain.internal_auth import load_or_create_internal_tool_secret
from app.domain.mcp_capacity import McpCapacityStore, PdfCapacityStore
from app.domain.mcp_tool_calls import McpToolCallStore
from app.domain.tool_runs import ToolRunStore
from app.main import create_app
from scripts.cleanup_guests import main as cleanup_main


def _live_settings(dsn: str = "", schema: str = "pskit") -> Settings:
    return Settings(
        mode="live", database_url=dsn, database_schema=schema,
        supabase_url="https://identity.example", supabase_publishable_key="publishable",
        mcp_executor="disabled", af3_executor="disabled",
    )


@pytest.mark.asyncio
async def test_live_rejects_missing_or_stale_database(pg_schema: tuple[str, str]) -> None:
    """Live startup needs a DSN and readiness checks the schema version."""
    with pytest.raises(ValueError, match="RESEARCH_AGENT_DATABASE_URL"):
        create_app(_live_settings())

    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    app = create_app(_live_settings(dsn, schema))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/health/ready")).status_code == 200
        with app.state.database.transaction() as connection:
            connection.execute("DELETE FROM schema_migrations WHERE version=(SELECT MAX(version) FROM schema_migrations)")
        stale = await client.get("/health/ready")
        assert stale.status_code == 503
        assert stale.json()["code"] == "DATABASE_UNAVAILABLE"
    app.state.database.close()


def test_tools_and_oauth_survive_reopen(pg_schema: tuple[str, str]) -> None:
    """OAuth, MCP results, tool history, and the HMAC secret persist."""
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    first_db = PostgresDatabase(dsn, schema=schema)
    try:
        state, verifier = OAuthFlowStore(first_db).create("guest-1")
        calls = McpToolCallStore(first_db)
        assert calls.claim("run-1", "call-1", "search", {"q": "RNA"}) == ("claimed", None)
        calls.complete("run-1", "call-1", McpInvokeResult(tool="search", result={"hits": 1}))
        tool = ToolRunStore(first_db).add("guest-1", "search", {"query": "RNA"}, {"hits": 1})
        secret = load_or_create_internal_tool_secret(first_db)
    finally:
        first_db.close()

    second_db = PostgresDatabase(dsn, schema=schema)
    try:
        oauth = OAuthFlowStore(second_db)
        assert oauth.consume(state) == (verifier, "guest-1")
        assert oauth.consume(state) is None
        assert McpToolCallStore(second_db).claim("run-1", "call-1", "search", {"q": "RNA"})[0] == "completed"
        assert ToolRunStore(second_db).list_for("guest-1")[0].id == tool.id
        assert load_or_create_internal_tool_secret(second_db) == secret
    finally:
        second_db.close()


def test_shared_mcp_and_pdf_capacity(pg_schema: tuple[str, str]) -> None:
    """Two independent pools cannot admit beyond the configured global limit."""
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    first_db = PostgresDatabase(dsn, schema=schema)
    second_db = PostgresDatabase(dsn, schema=schema)
    try:
        for capacity in (McpCapacityStore, PdfCapacityStore):
            first = capacity(first_db, 1)
            second = capacity(second_db, 1)
            barrier = Barrier(2)

            def claim(store: McpCapacityStore | PdfCapacityStore, start: Barrier = barrier) -> str | None:
                start.wait(timeout=5)
                return store.claim(30)

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(claim, store) for store in (first, second)]
                tokens = [future.result(timeout=5) for future in futures]
            assert sum(token is not None for token in tokens) == 1
            winner = first if tokens[0] else second
            winner.release(next(token for token in tokens if token))
            assert second.claim(30)
    finally:
        first_db.close()
        second_db.close()


def test_live_guest_cleanup_preview_uses_postgres(
    pg_schema: tuple[str, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    """The maintenance command reads the live pool without requiring a SQLite file."""
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    monkeypatch.setenv("RESEARCH_AGENT_MODE", "live")
    monkeypatch.setenv("RESEARCH_AGENT_DATABASE_URL", dsn)
    monkeypatch.setenv("RESEARCH_AGENT_DATABASE_SCHEMA", schema)
    monkeypatch.setenv("RESEARCH_AGENT_DB_PATH", "/nonexistent/agent.sqlite3")
    assert cleanup_main([]) == 0
    assert "0 eligible accounts" in capsys.readouterr().out


def test_live_cleanup_accepts_internal_supabase_gateway(
    pg_schema: tuple[str, str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The operator can use the Compose-only Supabase URL for cleanup."""
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    monkeypatch.setenv("RESEARCH_AGENT_MODE", "live")
    monkeypatch.setenv("RESEARCH_AGENT_DATABASE_URL", dsn)
    monkeypatch.setenv("RESEARCH_AGENT_DATABASE_SCHEMA", schema)
    monkeypatch.setenv("SUPABASE_URL", "http://api-gw:8000")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "test-only-secret")
    assert cleanup_main(["--execute"]) == 0
