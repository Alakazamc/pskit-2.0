"""Server-only Supabase Auth administration for inactive guest cleanup."""

from urllib.parse import quote

import httpx

from app.ports.providers import ProviderUnavailable


class SupabaseGuestAdmin:
    """Read and delete Auth users using a server-side secret key."""

    def __init__(
        self, base_url: str, secret_key: str, client: httpx.AsyncClient | None = None,
    ) -> None:
        """Keep the server secret for Auth administration only.

        Raises:
            ValueError: The secret key was omitted.
        """
        if not secret_key:
            raise ValueError("SUPABASE_SECRET_KEY is required for guest cleanup")
        self.base_url = base_url.rstrip("/")
        self.secret_key = secret_key
        self.client = client

    async def _request(self, method: str, user_id: str) -> httpx.Response:
        """Send an authenticated user administration request to Supabase."""
        url = f"{self.base_url}/auth/v1/admin/users/{quote(user_id, safe='')}"
        headers = {"apikey": self.secret_key}
        try:
            if self.client is not None:
                return await self.client.request(method, url, headers=headers)
            async with httpx.AsyncClient(timeout=10) as client:
                return await client.request(method, url, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("Supabase Auth administration unavailable") from exc

    async def get_user(self, user_id: str) -> dict | None:
        """Return the current Auth identity, or None if already deleted."""
        response = await self._request("GET", user_id)
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise ProviderUnavailable("Supabase Auth administration unavailable")
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderUnavailable("Supabase Auth administration returned invalid JSON") from exc
        if not isinstance(body, dict) or body.get("id") != user_id:
            raise ProviderUnavailable("Supabase Auth administration returned invalid identity")
        return body

    async def delete_user(self, user_id: str) -> None:
        """Delete the Auth identity; a 404 is an idempotent success."""
        response = await self._request("DELETE", user_id)
        if response.status_code not in {200, 204, 404}:
            raise ProviderUnavailable("Supabase Auth administration unavailable")
