import secrets
import uuid

from app.contracts.models import UserIdentity


class DemoStore:
    """Issue isolated in-memory identities and tokens for mock mode."""

    def __init__(self) -> None:
        """Initialize empty email and bearer-token lookup tables."""
        self._users_by_email: dict[str, UserIdentity] = {}
        self._users_by_token: dict[str, UserIdentity] = {}

    def issue_token(self, email: str) -> tuple[str, UserIdentity]:
        """Issue a fresh demo token for a normalized email identity.

        Repeated logins keep the same demo user ID but rotate the bearer token.

        Args:
            email: Email address used as the demo identity.

        Returns:
            New bearer token and user identity.
        """
        normalized = email.strip().lower()
        user = self._users_by_email.get(normalized)
        if user is None:
            user = UserIdentity(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"pskit-demo:{normalized}")),
                email=normalized,
                name=normalized.split("@")[0],
            )
            self._users_by_email[normalized] = user
        token = secrets.token_urlsafe(32)
        self._users_by_token[token] = user
        return token, user

    def issue_anonymous_token(self) -> tuple[str, UserIdentity]:
        """Create a new anonymous mock identity and bearer token.

        Returns:
            New bearer token and guest identity.
        """
        user = UserIdentity(id=str(uuid.uuid4()), email="", name="Guest", is_anonymous=True)
        token = secrets.token_urlsafe(32)
        self._users_by_token[token] = user
        return token, user

    def user_for_token(self, token: str) -> UserIdentity | None:
        """Resolve a demo bearer token to its current identity."""
        return self._users_by_token.get(token)

    def revoke_token(self, token: str) -> None:
        """Invalidate a demo bearer token if it is present."""
        self._users_by_token.pop(token, None)

    def email_is_used(self, email: str) -> bool:
        """Check whether a normalized email is already a demo member."""
        return email.strip().lower() in self._users_by_email

    def upgrade_anonymous(self, access_token: str, email: str) -> tuple[str, UserIdentity] | None:
        """Upgrade a mock guest to a new email account without changing its ID.

        Args:
            access_token: Current guest bearer token, invalidated on success.
            email: New email address to bind.

        Returns:
            Rotated token and member identity, or ``None`` when the token or
            email cannot be upgraded.
        """
        previous = self._users_by_token.get(access_token)
        normalized = email.strip().lower()
        if previous is None or not previous.is_anonymous or normalized in self._users_by_email:
            return None
        member = UserIdentity(
            id=previous.id, email=normalized, name=normalized.split("@")[0],
            is_anonymous=False,
        )
        new_token = secrets.token_urlsafe(32)
        self._users_by_token.pop(access_token)
        self._users_by_token[new_token] = member
        self._users_by_email[normalized] = member
        return new_token, member
