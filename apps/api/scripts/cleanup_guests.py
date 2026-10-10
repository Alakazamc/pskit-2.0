"""Preview or remove anonymous accounts inactive for more than 30 days.

The default command is read-only. Use ``--execute`` only in a configured live
environment with a server-side Supabase secret key.
"""

import argparse
import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from app.adapters.live.supabase_admin import SupabaseGuestAdmin
from app.config import Settings
from app.db.postgres import PostgresDatabase
from app.db.postgres_migrations import SCHEMA_VERSION
from app.services.guest_cleanup import GuestCleanupService


async def _execute(service: GuestCleanupService, candidates: list[str]) -> int:
    failed = 0
    for user_id in candidates:
        if not await service.purge_one(user_id):
            failed += 1
    print(f"Guest cleanup: {len(candidates) - failed} deleted, {failed} skipped or failed")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    """Run the bounded cleanup command and return a process exit status."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="perform remote and local deletion")
    parser.add_argument("--limit", type=int, default=100, help="maximum candidates per invocation")
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit must be positive")
    settings = Settings.from_env()
    database = None
    if settings.mode == "live":
        if not settings.database_url:
            print("Guest cleanup: RESEARCH_AGENT_DATABASE_URL is required")
            return 2
        try:
            database = PostgresDatabase(settings.database_url, schema=settings.database_schema)
            database.check_schema_version(SCHEMA_VERSION)
        except Exception:  # noqa: BLE001 - maintenance must fail closed on database errors
            if database is not None:
                database.close()
            print("Guest cleanup: PostgreSQL unavailable or schema version mismatch")
            return 2
    elif not Path(settings.agent_db_path).is_file():
        print("Guest cleanup: database file does not exist")
        return 2
    storage = database if database is not None else settings.agent_db_path
    try:
        if not args.execute:
            service = GuestCleanupService(storage, admin=None)
            candidates = service.collect_candidates(datetime.now(UTC))[:args.limit]
            print(f"Guest cleanup dry run: {len(candidates)} eligible accounts")
            return 0
        secret = os.getenv("SUPABASE_SECRET_KEY", "")
        parsed = urlsplit(settings.supabase_url)
        internal_gateway = (parsed.scheme == "http" and parsed.hostname == "api-gw"
                            and parsed.port == 8000)
        if (settings.mode != "live" or not secret
                or (parsed.scheme != "https" and not internal_gateway)
                or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment):
            print("Guest cleanup requires live mode, a valid SUPABASE_URL and SUPABASE_SECRET_KEY")
            return 2
        admin = SupabaseGuestAdmin(settings.supabase_url, secret)
        service = GuestCleanupService(
            storage, admin, pi_session_dir=settings.pi_session_dir,
        )
        candidates = service.collect_candidates(datetime.now(UTC))[:args.limit]
        return asyncio.run(_execute(service, candidates))
    finally:
        if database is not None:
            database.close()


if __name__ == "__main__":
    raise SystemExit(main())
