"""Quota revision, live resource holds, and audit commit as one transaction."""

import pytest
from fastapi import FastAPI

from app.api import admin_auth, admin_operations
from app.contracts.models import UserIdentity
from app.db.postgres import PostgresDatabase
from app.db.postgres_migrations import migrate_postgres
from app.domain.admin.audit import AdminOperations
from app.domain.admin.roles import AdminStore
from app.domain.identity_policy import IdentityPolicyStore
from app.domain.persistent_conversation import PersistentConversationStore


class Identity:
    async def verify(self, token):
        return UserIdentity(
            id="operator", name="Operator", email="operator@example.org", is_anonymous=False
        )


@pytest.fixture
def admin_system(pg_schema):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    identity = IdentityPolicyStore(database)
    identity.observe_verified_user("operator", False)
    identity.observe_verified_user("member", False)
    quotas = PersistentConversationStore(database)
    quotas.identity_policy = identity
    store = AdminStore(database, identity_policy=identity, quotas=quotas)
    store.grant("operator", "platform_admin", actor="server:test", reason="Controlled grant")
    app = FastAPI()
    app.state.identity_provider = Identity()
    app.state.identity_policy = identity
    app.state.admin_store = store
    app.state.admin_operations = AdminOperations(store)
    app.include_router(admin_auth.router)
    app.include_router(admin_operations.router)
    from app.api import admin

    app.include_router(admin.router)
    try:
        yield app, database
    finally:
        database.close()
