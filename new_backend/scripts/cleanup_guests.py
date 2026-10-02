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
    if not Path(settings.agent_db_path).is_file():
        print("Guest cleanup: database file does not exist")
        return 2
    if not args.execute:
        service = GuestCleanupService(settings.agent_db_path, admin=None)
        candidates = service.collect_candidates(datetime.now(UTC))[:args.limit]
        print(f"Guest cleanup dry run: {len(candidates)} eligible accounts")
        return 0
    secret = os.getenv("SUPABASE_SECRET_KEY", "")
    parsed = urlsplit(settings.supabase_url)
    if (settings.mode != "live" or not secret or parsed.scheme != "https"
            or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        print("Guest cleanup requires live mode, a valid SUPABASE_URL and SUPABASE_SECRET_KEY")
        return 2
    admin = SupabaseGuestAdmin(settings.supabase_url, secret)
    service = GuestCleanupService(
        settings.agent_db_path, admin, pi_session_dir=settings.pi_session_dir,
    )
    candidates = service.collect_candidates(datetime.now(UTC))[:args.limit]
    return asyncio.run(_execute(service, candidates))


if __name__ == "__main__":
    raise SystemExit(main())
