from app.contracts.models import UserIdentity
from app.domain.store import DemoStore


class MockIdentityProvider:
    def __init__(self, store: DemoStore) -> None:
        """Use the demo credential store for identity lookup.

        Args:
            store: In-memory demo user and token store.
        """
        self.store = store

    async def verify(self, access_token: str) -> UserIdentity | None:
        """Resolve a demo access token to its user, if present."""
        return self.store.user_for_token(access_token)
