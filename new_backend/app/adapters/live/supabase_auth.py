from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from app.contracts.models import UserIdentity
from app.ports.providers import ProviderUnavailable


class SupabaseIdentityAdapter:
    def __init__(
        self, base_url: str, publishable_key: str, client: httpx.AsyncClient | None = None
    ) -> None:
        """Configure a Supabase Auth endpoint and optional shared HTTP client."""
        self.base_url = base_url.rstrip("/")
        self.publishable_key = publishable_key
        self.client = client

    async def sign_in_password(self, email: str, password: str) -> "SupabaseSession":
        """Exchange an email and password for a Supabase session."""
        if self.client is None:
            async with httpx.AsyncClient(timeout=10) as client:
                return await self._exchange(client, "password", {"email": email, "password": password})
        return await self._exchange(self.client, "password", {"email": email, "password": password})

    async def sign_up(self, email: str, password: str) -> "SupabaseSession | None":
        """Create an email account; return a session when confirmation is not required."""
        if self.client is None:
            async with httpx.AsyncClient(timeout=10) as client:
                return await self._sign_up(client, email, password)
        return await self._sign_up(self.client, email, password)

    async def sign_in_anonymously(self, captcha_token: str | None = None) -> "SupabaseSession":
        """Create an anonymous Supabase identity with an optional CAPTCHA proof."""
        payload = {
            "data": {},
            "gotrue_meta_security": {"captcha_token": captcha_token},
        }
        if self.client is None:
            async with httpx.AsyncClient(timeout=10) as client:
                return self._session_from_payload(await self._post_auth(client, "signup", payload))
        return self._session_from_payload(await self._post_auth(self.client, "signup", payload))

    async def _sign_up(
        self, client: httpx.AsyncClient, email: str, password: str
    ) -> "SupabaseSession | None":
        """Submit account creation through the supplied HTTP client."""
        data = await self._post_auth(client, "signup", {"email": email, "password": password})
        return self._session_from_payload(data) if data.get("access_token") else None

    async def recover_password(self, email: str) -> None:
        """Request a password reset email from Supabase Auth."""
        if self.client is None:
            async with httpx.AsyncClient(timeout=10) as client:
                await self._post_auth(client, "recover", {"email": email})
        else:
            await self._post_auth(self.client, "recover", {"email": email})

    async def verify_otp(self, email: str, token: str, kind: str) -> "SupabaseSession":
        """Exchange an email verification or recovery code for a session."""
        payload = {"email": email, "token": token, "type": kind}
        if self.client is None:
            async with httpx.AsyncClient(timeout=10) as client:
                return self._session_from_payload(await self._post_auth(client, "verify", payload))
        return self._session_from_payload(await self._post_auth(self.client, "verify", payload))

    async def update_guest_email(self, access_token: str, email: str) -> None:
        """Attach an email address to the current anonymous identity."""
        if self.client is None:
            async with httpx.AsyncClient(timeout=10) as client:
                await self._update_guest_email(client, access_token, email)
        else:
            await self._update_guest_email(self.client, access_token, email)

    async def link_google_identity(
        self, access_token: str, redirect_to: str, code_challenge: str,
    ) -> str:
        """Start Google identity linking for an authenticated user."""
        if self.client is None:
            async with httpx.AsyncClient(timeout=10) as client:
                return await self._link_google_identity(
                    client, access_token, redirect_to, code_challenge,
                )
        return await self._link_google_identity(
            self.client, access_token, redirect_to, code_challenge,
        )

    async def _link_google_identity(
        self, client: httpx.AsyncClient, access_token: str,
        redirect_to: str, code_challenge: str,
    ) -> str:
        """Validate the HTTPS Google authorization URL returned by Supabase."""
        try:
            response = await client.get(
                f"{self.base_url}/auth/v1/user/identities/authorize",
                params={
                    "provider": "google", "redirect_to": redirect_to,
                    "code_challenge": code_challenge,
                    "code_challenge_method": "s256", "skip_http_redirect": "true",
                    "scopes": "openid email profile",
                },
                headers={"apikey": self.publishable_key,
                         "Authorization": f"Bearer {access_token}"},
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("Supabase Auth unavailable") from exc
        if response.status_code == 429:
            raise AuthRateLimited(response.headers.get("Retry-After"))
        if response.status_code in {400, 409, 422}:
            try:
                body = response.json()
            except ValueError:
                body = {}
            code = body.get("code") if isinstance(body, dict) else None
            if code in {"identity_already_exists", "email_conflict_identity_not_deletable"}:
                raise IdentityAlreadyLinked
            raise InvalidCredentials
        if response.status_code in {401, 403}:
            raise InvalidCredentials
        if response.status_code != 200:
            raise ProviderUnavailable("Supabase Auth unavailable")
        try:
            body = response.json()
        except ValueError as exc:
            raise ProviderUnavailable("Supabase Auth returned invalid JSON") from exc
        url = body.get("url") if isinstance(body, dict) else None
        parsed = urlsplit(url) if isinstance(url, str) else None
        if (parsed is None or parsed.scheme != "https" or not parsed.hostname
                or parsed.username or parsed.password):
            raise ProviderUnavailable("Supabase Auth returned an invalid OAuth URL")
        return url

    async def _update_guest_email(
        self, client: httpx.AsyncClient, access_token: str, email: str,
    ) -> None:
        """Update the current user's email and classify provider errors."""
        try:
            response = await client.put(
                f"{self.base_url}/auth/v1/user",
                headers={"apikey": self.publishable_key,
                         "Authorization": f"Bearer {access_token}"},
                json={"email": email},
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("Supabase Auth unavailable") from exc
        if response.status_code == 429:
            raise AuthRateLimited(response.headers.get("Retry-After"))
        if response.status_code in {400, 409, 422}:
            try:
                body = response.json()
            except ValueError:
                body = {}
            code = body.get("code") if isinstance(body, dict) else None
            if code in {"email_exists", "user_already_exists", "email_address_not_available"}:
                raise EmailAlreadyInUse
            raise InvalidCredentials
        if response.status_code in {401, 403}:
            raise InvalidCredentials
        if response.status_code != 200:
            raise ProviderUnavailable("Supabase Auth unavailable")

    async def update_password(self, access_token: str, password: str) -> None:
        """Set a new password for the authenticated Supabase user."""
        if self.client is None:
            async with httpx.AsyncClient(timeout=10) as client:
                await self._update_password(client, access_token, password)
        else:
            await self._update_password(self.client, access_token, password)

    async def _update_password(
        self, client: httpx.AsyncClient, access_token: str, password: str
    ) -> None:
        """Send a password update and classify provider errors."""
        try:
            response = await client.put(
                f"{self.base_url}/auth/v1/user",
                headers={"apikey": self.publishable_key,
                         "Authorization": f"Bearer {access_token}"},
                json={"password": password},
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("Supabase Auth unavailable") from exc
        if response.status_code == 429:
            raise AuthRateLimited(response.headers.get("Retry-After"))
        if response.status_code in {400, 401, 403, 422}:
            raise InvalidCredentials
        if response.status_code != 200:
            raise ProviderUnavailable("Supabase Auth unavailable")

    async def _post_auth(
        self, client: httpx.AsyncClient, path: str, payload: dict[str, object]
    ) -> dict:
        """Post to Supabase Auth and validate its JSON response."""
        try:
            response = await client.post(
                f"{self.base_url}/auth/v1/{path}",
                headers={"apikey": self.publishable_key}, json=payload,
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("Supabase Auth unavailable") from exc
        if response.status_code == 429:
            raise AuthRateLimited(response.headers.get("Retry-After"))
        if response.status_code in {400, 401, 403, 422}:
            raise InvalidCredentials
        if response.status_code != 200:
            raise ProviderUnavailable("Supabase Auth unavailable")
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderUnavailable("Supabase Auth returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise ProviderUnavailable("Supabase Auth returned invalid JSON")
        return data

    @staticmethod
    def _session_from_payload(data: dict) -> "SupabaseSession":
        """Extract access, refresh, and expiry fields from a provider response."""
        try:
            return SupabaseSession(
                access_token=data["access_token"],
                refresh_token=data["refresh_token"],
                expires_in=int(data["expires_in"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderUnavailable("Supabase Auth returned an invalid session") from exc

    async def refresh(self, refresh_token: str) -> "SupabaseSession":
        """Refresh a Supabase session using its refresh token."""
        if self.client is None:
            async with httpx.AsyncClient(timeout=10) as client:
                return await self._exchange(client, "refresh_token", {"refresh_token": refresh_token})
        return await self._exchange(self.client, "refresh_token", {"refresh_token": refresh_token})

    async def exchange_pkce(self, auth_code: str, code_verifier: str) -> "SupabaseSession":
        """Exchange a PKCE authorization code for a Supabase session."""
        payload = {"auth_code": auth_code, "code_verifier": code_verifier}
        if self.client is None:
            async with httpx.AsyncClient(timeout=10) as client:
                return await self._exchange(client, "pkce", payload)
        return await self._exchange(self.client, "pkce", payload)

    async def sign_out(self, access_token: str) -> None:
        """Revoke the active Supabase session."""
        if self.client is None:
            async with httpx.AsyncClient(timeout=10) as client:
                await self._sign_out(client, access_token)
        else:
            await self._sign_out(self.client, access_token)

    async def _sign_out(self, client: httpx.AsyncClient, access_token: str) -> None:
        """Post a logout request, treating an expired token as already signed out."""
        try:
            response = await client.post(
                f"{self.base_url}/auth/v1/logout",
                headers={"apikey": self.publishable_key, "Authorization": f"Bearer {access_token}"},
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("Supabase Auth unavailable") from exc
        if response.status_code == 429:
            raise AuthRateLimited(response.headers.get("Retry-After"))
        if response.status_code not in {200, 204, 401}:
            raise ProviderUnavailable("Supabase Auth unavailable")

    async def _exchange(
        self, client: httpx.AsyncClient, grant_type: str, payload: dict[str, str]
    ) -> "SupabaseSession":
        """Exchange an Auth grant for a validated access and refresh session."""
        try:
            response = await client.post(
                f"{self.base_url}/auth/v1/token",
                params={"grant_type": grant_type},
                headers={"apikey": self.publishable_key},
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("Supabase Auth unavailable") from exc
        if response.status_code == 429:
            raise AuthRateLimited(response.headers.get("Retry-After"))
        if response.status_code in {400, 401, 403}:
            raise InvalidCredentials
        if response.status_code != 200:
            raise ProviderUnavailable("Supabase Auth unavailable")
        data = response.json()
        try:
            return SupabaseSession(
                access_token=data["access_token"],
                refresh_token=data["refresh_token"],
                expires_in=int(data["expires_in"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderUnavailable("Supabase Auth returned an invalid session") from exc

    async def verify(self, access_token: str) -> UserIdentity | None:
        """Resolve a bearer token to a Supabase user identity."""
        if self.client is None:
            async with httpx.AsyncClient(timeout=10) as client:
                return await self._verify(client, access_token)
        return await self._verify(self.client, access_token)

    async def _verify(self, client: httpx.AsyncClient, access_token: str) -> UserIdentity | None:
        """Read the user profile; return None for invalid or expired tokens."""
        try:
            response = await client.get(
                f"{self.base_url}/auth/v1/user",
                headers={"apikey": self.publishable_key, "Authorization": f"Bearer {access_token}"},
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable("Supabase Auth unavailable") from exc
        if response.status_code == 429:
            raise AuthRateLimited(response.headers.get("Retry-After"))
        if response.status_code in {401, 403}:
            return None
        if response.status_code != 200:
            raise ProviderUnavailable("Supabase Auth unavailable")
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderUnavailable("Supabase Auth returned invalid JSON") from exc
        if not isinstance(data, dict) or not isinstance(data.get("id"), str) or not data["id"]:
            raise ProviderUnavailable("Supabase Auth returned an invalid user")
        email = data.get("email") if isinstance(data.get("email"), str) else ""
        metadata = data.get("user_metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        full_name = metadata.get("full_name")
        name = full_name if isinstance(full_name, str) and full_name else email.split("@")[0]
        return UserIdentity(
            id=str(data["id"]), email=email, name=name,
            is_anonymous=data.get("is_anonymous") is not False,
        )


class InvalidCredentials(Exception):
    pass


class EmailAlreadyInUse(Exception):
    pass


class IdentityAlreadyLinked(Exception):
    pass


class AuthRateLimited(Exception):
    def __init__(self, retry_after: str | None = None) -> None:
        """Preserve a numeric Retry-After delay from Supabase Auth."""
        super().__init__("Supabase Auth rate limited")
        self.retry_after = retry_after if retry_after and retry_after.isdecimal() else None


@dataclass(frozen=True)
class SupabaseSession:
    access_token: str
    refresh_token: str
    expires_in: int
