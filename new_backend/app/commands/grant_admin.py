"""Grant a verified member a role from a controlled server shell."""

import argparse
import getpass
import socket

from app.config import Settings
from app.db.postgres import PostgresDatabase
from app.domain.admin.roles import ROLE_PERMISSIONS, AdminStore


def main():
    """Grant or revoke current roles and append the operator's audit source."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--role", choices=sorted(ROLE_PERMISSIONS), required=True)
    parser.add_argument("--service-id", default="")
    parser.add_argument("--reason", default="Controlled server administrator initialization")
    parser.add_argument("--revoke", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_env()
    if settings.mode != "live":
        parser.error("Role initialization requires live PostgreSQL configuration")
    database = PostgresDatabase(settings.database_url, schema=settings.database_schema)
    try:
        with database.connection() as connection:
            verified = connection.execute(
                "SELECT tier FROM account_tiers WHERE user_id=%s", (args.user_id,)
            ).fetchone()
        if not verified or verified[0] != "member":
            parser.error("User must have a verified, observed member identity")
        store = AdminStore(database)
        actor = f"server:{getpass.getuser()}@{socket.gethostname()}"
        action = store.revoke if args.revoke else store.grant
        result = action(
            args.user_id, args.role, service_id=args.service_id, actor=actor, reason=args.reason
        )
        print(result.model_dump_json())
    finally:
        database.close()


if __name__ == "__main__":
    main()
