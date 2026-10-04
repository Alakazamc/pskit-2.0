"""Supabase Storage REST adapter; service credentials stay in Python."""

from urllib.parse import quote

import httpx

from app.ports.providers import ProviderUnavailable


class SupabaseAvatarStorage:
    bucket = "pskit-avatars"

    def __init__(
        self, base_url: str, secret_key: str, client: httpx.AsyncClient | None = None,
    ) -> None:
        """Configure the private bucket using the existing Supabase gateway."""
        self.base_url = base_url.rstrip("/")
        self.secret_key = secret_key
        self.client = client

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Send a bounded provider request without exposing provider errors or keys."""
        if not self.base_url or not self.secret_key:
            raise ProviderUnavailable("Avatar storage is not configured")
        headers = {"apikey": self.secret_key, **kwargs.pop("headers", {})}
        # Legacy service-role JWTs also work with hosted Supabase's gateway.
        if not self.secret_key.startswith("sb_"):
            headers["Authorization"] = f"Bearer {self.secret_key}"
        try:
            if self.client is not None:
                return await self.client.request(
                    method, f"{self.base_url}/storage/v1{path}", headers=headers, **kwargs,
                )
            async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=5)) as client:
                return await client.request(
                    method, f"{self.base_url}/storage/v1{path}", headers=headers, **kwargs,
                )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("Avatar storage unavailable") from exc

    async def _ensure_private_bucket(self) -> None:
        """Create the private bucket on first upload and reject public misconfiguration."""
        response = await self._request("GET", f"/bucket/{self.bucket}")
        if self._missing(response, "NoSuchBucket"):
            created = await self._request("POST", "/bucket", json={
                "id": self.bucket, "name": self.bucket, "public": False,
                "file_size_limit": 524288, "allowed_mime_types": ["image/webp"],
            })
            if created.is_success:
                return
            # A concurrent first upload may already have created the same bucket.
            response = await self._request("GET", f"/bucket/{self.bucket}")
        if response.status_code != 200:
            raise ProviderUnavailable("Avatar bucket unavailable")
        try:
            bucket = response.json()
        except ValueError as exc:
            raise ProviderUnavailable("Avatar bucket unavailable") from exc
        if not isinstance(bucket, dict) or bucket.get("public") is not False:
            raise ProviderUnavailable("Avatar bucket must be private")

    async def put(self, key: str, content: bytes) -> None:
        """Upload a fresh immutable WebP after ensuring the bucket is private."""
        await self._ensure_private_bucket()
        response = await self._request(
            "POST", f"/object/{self.bucket}/{quote(key, safe='/')}", content=content,
            headers={"Content-Type": "image/webp", "x-upsert": "false"},
        )
        if not response.is_success:
            raise ProviderUnavailable("Avatar upload unavailable")

    async def get(self, key: str) -> bytes | None:
        """Read an object through the authenticated Storage route."""
        response = await self._request(
            "GET", f"/object/authenticated/{self.bucket}/{quote(key, safe='/')}",
        )
        if self._missing(response, "NoSuchKey"):
            return None
        if not response.is_success or len(response.content) > 524288:
            raise ProviderUnavailable("Avatar read unavailable")
        return response.content

    @staticmethod
    def _missing(response: httpx.Response, code: str) -> bool:
        """Storage v1.74 can wrap a missing-resource 404 in an HTTP 400 response."""
        if response.status_code == 404:
            return True
        if response.status_code != 400:
            return False
        try:
            body = response.json()
        except ValueError:
            return False
        return isinstance(body, dict) and body.get("code") == code

    async def delete(self, key: str) -> None:
        """Remove an obsolete object via Storage's prefix deletion contract."""
        response = await self._request("DELETE", f"/object/{self.bucket}", json={"prefixes": [key]})
        if not response.is_success and response.status_code != 404:
            raise ProviderUnavailable("Avatar cleanup unavailable")
