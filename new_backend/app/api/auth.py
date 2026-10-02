import base64
import hashlib
import secrets
import sqlite3
from pathlib import Path
from time import time
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.adapters.live.supabase_auth import AuthRateLimited, InvalidCredentials
from app.contracts.models import (
    AuthSessionResponse,
    DemoLoginRequest,
    DemoLoginResponse,
    EmailLoginRequest,
    EmailRequest,
    OtpVerifyRequest,
    PasswordUpdateRequest,
    RecoveryResponse,
    SignupRequest,
    SignupResponse,
    UserIdentity,
)
from app.db.migrations import migrate_oauth_schema
from app.domain.store import DemoStore
from app.ports.providers import ProviderUnavailable

router = APIRouter(prefix="/api/v1", tags=["auth"])
bearer = HTTPBearer(auto_error=False)


def _rate_limited(exc: AuthRateLimited) -> HTTPException:
    """Translate a Supabase rate limit into an HTTP 429 with Retry-After."""
    headers = {"Retry-After": exc.retry_after} if exc.retry_after else None
    return HTTPException(status_code=429, detail={"code": "AUTH_RATE_LIMITED"}, headers=headers)


async def get_store(request: Request) -> DemoStore:
    """Provide the demo credential store to mock-mode routes."""
    return request.app.state.demo_store


StoreDep = Annotated[DemoStore, Depends(get_store)]


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    request: Request,
) -> UserIdentity:
    """Verify the bearer token and record the authenticated identity."""
    if credentials is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        user = await request.app.state.identity_provider.verify(credentials.credentials)
    except AuthRateLimited as exc:
        raise _rate_limited(exc) from exc
    except ProviderUnavailable as exc:
        raise HTTPException(status_code=503, detail="Identity provider unavailable") from exc
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    request.app.state.identity_policy.observe_verified_user(user.id, user.is_anonymous)
    return user


CurrentUserDep = Annotated[UserIdentity, Depends(get_current_user)]


@router.post("/auth/demo")
async def demo_login(
    payload: DemoLoginRequest, store: StoreDep, request: Request
) -> DemoLoginResponse:
    """Issue a demo token when the backend runs in mock mode."""
    if request.app.state.settings.mode != "mock":
        raise HTTPException(status_code=404, detail="Demo login is unavailable")
    token, user = store.issue_token(payload.email)
    request.app.state.identity_policy.observe_verified_user(user.id, user.is_anonymous)
    return DemoLoginResponse(access_token=token, user=user)


@router.post("/auth/login")
async def email_login(
    payload: EmailLoginRequest, request: Request, response: Response
) -> AuthSessionResponse:
    """Sign in through Supabase and store the refresh token in a secure cookie."""
    if request.app.state.settings.mode != "live":
        raise HTTPException(status_code=404, detail="Email login is unavailable")
    provider = request.app.state.identity_provider
    try:
        session = await provider.sign_in_password(payload.email, payload.password)
        user = await provider.verify(session.access_token)
    except InvalidCredentials as exc:
        raise HTTPException(status_code=401, detail="Invalid credentials") from exc
    except AuthRateLimited as exc:
        raise _rate_limited(exc) from exc
    except ProviderUnavailable as exc:
        raise HTTPException(status_code=503, detail="Identity provider unavailable") from exc
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    request.app.state.identity_policy.observe_verified_user(user.id, user.is_anonymous)
    _set_refresh_cookie(response, session.refresh_token, request)
    return AuthSessionResponse(
        access_token=session.access_token, expires_in=session.expires_in, user=user
    )


@router.post("/auth/signup")
async def signup(
    payload: SignupRequest, request: Request, response: Response,
) -> SignupResponse:
    """Create a Supabase email account and handle email confirmation."""
    if request.app.state.settings.mode != "live":
        raise HTTPException(status_code=404, detail="Signup is unavailable")
    provider = request.app.state.identity_provider
    try:
        session = await provider.sign_up(payload.email, payload.password)
        user = await provider.verify(session.access_token) if session else None
    except AuthRateLimited as exc:
        raise _rate_limited(exc) from exc
    except InvalidCredentials as exc:
        raise HTTPException(status_code=422, detail={"code": "SIGNUP_REJECTED"}) from exc
    except ProviderUnavailable as exc:
        raise HTTPException(status_code=503, detail="Identity provider unavailable") from exc
    if session and user:
        request.app.state.identity_policy.observe_verified_user(user.id, user.is_anonymous)
        _set_refresh_cookie(response, session.refresh_token, request)
        return SignupResponse(status="signed_in", session=AuthSessionResponse(
            access_token=session.access_token, expires_in=session.expires_in, user=user,
        ))
    return SignupResponse(status="check_email")


@router.post("/auth/password/recover")
async def recover_password(payload: EmailRequest, request: Request) -> RecoveryResponse:
    """Request a Supabase password recovery email."""
    if request.app.state.settings.mode != "live":
        raise HTTPException(status_code=404, detail="Password recovery is unavailable")
    try:
        await request.app.state.identity_provider.recover_password(payload.email)
    except AuthRateLimited as exc:
        raise _rate_limited(exc) from exc
    except InvalidCredentials as exc:
        raise HTTPException(status_code=422, detail={"code": "RECOVERY_REJECTED"}) from exc
    except ProviderUnavailable as exc:
        raise HTTPException(status_code=503, detail="Identity provider unavailable") from exc
    return RecoveryResponse()


@router.post("/auth/verify")
async def verify_otp(
    payload: OtpVerifyRequest, request: Request, response: Response,
) -> AuthSessionResponse:
    """Verify an email code and issue the resulting login session."""
    if request.app.state.settings.mode != "live":
        raise HTTPException(status_code=404, detail="OTP verification is unavailable")
    provider = request.app.state.identity_provider
    try:
        session = await provider.verify_otp(payload.email, payload.token, payload.type)
        user = await provider.verify(session.access_token)
    except AuthRateLimited as exc:
        raise _rate_limited(exc) from exc
    except InvalidCredentials as exc:
        raise HTTPException(status_code=401, detail={"code": "INVALID_OTP"}) from exc
    except ProviderUnavailable as exc:
        raise HTTPException(status_code=503, detail="Identity provider unavailable") from exc
    if user is None:
        raise HTTPException(status_code=401, detail={"code": "INVALID_OTP"})
    request.app.state.identity_policy.observe_verified_user(user.id, user.is_anonymous)
    _set_refresh_cookie(response, session.refresh_token, request)
    return AuthSessionResponse(
        access_token=session.access_token, expires_in=session.expires_in, user=user,
    )


@router.post("/auth/password/update", status_code=204)
async def update_password(
    payload: PasswordUpdateRequest, user: CurrentUserDep, request: Request,
) -> None:
    """Change the authenticated user's password through Supabase."""
    if request.app.state.settings.mode != "live":
        raise HTTPException(status_code=404, detail="Password update is unavailable")
    del user
    access_token = request.headers["authorization"].removeprefix("Bearer ")
    try:
        await request.app.state.identity_provider.update_password(access_token, payload.password)
    except AuthRateLimited as exc:
        raise _rate_limited(exc) from exc
    except InvalidCredentials as exc:
        raise HTTPException(status_code=422, detail={"code": "PASSWORD_UPDATE_REJECTED"}) from exc
    except ProviderUnavailable as exc:
        raise HTTPException(status_code=503, detail="Identity provider unavailable") from exc


@router.post("/auth/refresh", response_model=AuthSessionResponse)
async def refresh_login(request: Request, response: Response) -> AuthSessionResponse | Response:
    """Refresh the access token from the HttpOnly refresh cookie."""
    if request.app.state.settings.mode == "mock":
        token = request.cookies.get("research_refresh_token", "")
        user = request.app.state.demo_store.user_for_token(token)
        if user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        request.app.state.identity_policy.observe_verified_user(user.id, user.is_anonymous)
        return AuthSessionResponse(access_token=token, expires_in=3600, user=user)
    refresh_token = request.cookies.get("research_refresh_token")
    if not refresh_token:
        raise HTTPException(status_code=401, detail="Authentication required")
    provider = request.app.state.identity_provider
    try:
        session = await provider.refresh(refresh_token)
        user = await provider.verify(session.access_token)
    except InvalidCredentials:
        return _clear_refresh_cookie()
    except AuthRateLimited as exc:
        raise _rate_limited(exc) from exc
    except ProviderUnavailable as exc:
        raise HTTPException(status_code=503, detail="Identity provider unavailable") from exc
    if user is None:
        return _clear_refresh_cookie()
    request.app.state.identity_policy.observe_verified_user(user.id, user.is_anonymous)
    _set_refresh_cookie(response, session.refresh_token, request)
    return AuthSessionResponse(
        access_token=session.access_token, expires_in=session.expires_in, user=user
    )


def _set_refresh_cookie(response: Response, refresh_token: str, request: Request) -> None:
    """Set the restricted HttpOnly session refresh cookie."""
    response.set_cookie(
        "research_refresh_token", refresh_token,
        httponly=True, secure=request.app.state.settings.auth_cookie_secure,
        samesite="lax", path="/api/v1/auth",
    )


def _clear_refresh_cookie() -> JSONResponse:
    """Clear an invalid refresh cookie and return an unauthorized response."""
    failure = JSONResponse({"detail": "Authentication required"}, status_code=401)
    failure.delete_cookie("research_refresh_token", path="/api/v1/auth")
    return failure


class OAuthFlowStore:
    def __init__(self, db_path: str | None = None) -> None:
        """Use SQLite or memory to hold short-lived OAuth PKCE state."""
        self._pending: dict[str, tuple[str, float, str | None]] = {}
        self.db: sqlite3.Connection | None = None
        if db_path:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(db_path, check_same_thread=False)
            try:
                migrate_oauth_schema(self.db)
                self.db.execute("PRAGMA journal_mode=WAL")
            except BaseException:
                self.db.close()
                raise

    def create(self, guest_user_id: str | None = None) -> tuple[str, str]:
        """Create a five-minute OAuth state and PKCE verifier."""
        now = time()
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        if self.db:
            with self.db:
                self.db.execute("DELETE FROM oauth_flows WHERE expires_at<=?", (now,))
                self.db.execute(
                    "INSERT INTO oauth_flows (state,verifier,expires_at,guest_user_id) "
                    "VALUES (?,?,?,?)", (state, verifier, now + 300, guest_user_id),
                )
            return state, verifier
        self._pending = {
            key: entry for key, entry in self._pending.items() if entry[1] > now
        }
        self._pending[state] = (verifier, now + 300, guest_user_id)
        return state, verifier

    def consume(self, state: str) -> tuple[str, str | None] | None:
        """Atomically consume an unexpired OAuth state once."""
        if self.db:
            self.db.execute("BEGIN IMMEDIATE")
            with self.db:
                entry = self.db.execute(
                    "SELECT verifier,expires_at,guest_user_id FROM oauth_flows WHERE state=?", (state,)
                ).fetchone()
                self.db.execute("DELETE FROM oauth_flows WHERE state=?", (state,))
            return (entry[0], entry[2]) if entry and entry[1] > time() else None
        entry = self._pending.pop(state, None)
        return (entry[0], entry[2]) if entry and entry[1] > time() else None

    def discard(self, state: str) -> None:
        """Delete a pending OAuth flow after an aborted upgrade."""
        if self.db:
            with self.db:
                self.db.execute("DELETE FROM oauth_flows WHERE state=?", (state,))
        else:
            self._pending.pop(state, None)


@router.get("/auth/google/start")
async def google_start(request: Request) -> RedirectResponse:
    """Start a Google OAuth flow through Supabase with PKCE and state."""
    settings = request.app.state.settings
    if settings.mode != "live":
        raise HTTPException(status_code=404, detail="Google login is unavailable")
    state, verifier = request.app.state.oauth_flows.create()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    callback = f"{settings.public_api_url.rstrip('/')}/api/v1/auth/google/callback?{urlencode({'state': state})}"
    authorize = f"{settings.supabase_url.rstrip('/')}/auth/v1/authorize?{urlencode({'provider': 'google', 'redirect_to': callback, 'code_challenge': challenge, 'code_challenge_method': 's256'})}"
    response = RedirectResponse(authorize, status_code=302, headers={"Referrer-Policy": "no-referrer"})
    response.set_cookie(
        "research_oauth_state", state, httponly=True, max_age=300,
        secure=settings.auth_cookie_secure, samesite="lax", path="/api/v1/auth/google/callback",
    )
    return response


@router.get("/auth/google/callback")
async def google_callback(
    request: Request, state: str, code: str | None = None,
    error: str | None = None, error_code: str | None = None,
) -> RedirectResponse:
    """Complete Google login or guest upgrade and redirect to the frontend."""
    settings = request.app.state.settings
    if not secrets.compare_digest(request.cookies.get("research_oauth_state", ""), state):
        raise HTTPException(status_code=400, detail="Invalid OAuth state")
    flow = request.app.state.oauth_flows.consume(state)
    if flow is None:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")
    verifier, guest_user_id = flow
    if settings.mode != "live" and guest_user_id is None:
        raise HTTPException(status_code=404, detail="Google login is unavailable")
    callback_url = f"{settings.frontend_url.rstrip('/')}/auth/callback"
    if guest_user_id and error:
        failure_code = (
            "GOOGLE_IDENTITY_CONFLICT"
            if error_code in {"identity_already_exists", "email_conflict_identity_not_deletable"}
            else "GOOGLE_LINK_REJECTED"
        )
        response = RedirectResponse(
            f"{callback_url}?{urlencode({'error': failure_code})}", status_code=303,
            headers={"Referrer-Policy": "no-referrer"},
        )
        response.delete_cookie("research_oauth_state", path="/api/v1/auth/google/callback")
        return response
    if not code:
        raise HTTPException(status_code=400, detail="Missing OAuth code")
    if settings.mode == "mock":
        token = request.cookies.get("research_refresh_token", "")
        guest = request.app.state.demo_store.user_for_token(token)
        if code != "mock-google" or guest is None or guest.id != guest_user_id or not guest.is_anonymous:
            raise HTTPException(status_code=409, detail={"code": "GOOGLE_IDENTITY_CONFLICT"})
        upgraded = request.app.state.demo_store.upgrade_anonymous(
            token, f"guest-google-{guest_user_id}@example.invalid",
        )
        if upgraded is None:
            raise HTTPException(status_code=409, detail={"code": "GOOGLE_IDENTITY_CONFLICT"})
        refresh_token, user = upgraded
    else:
        provider = request.app.state.identity_provider
        try:
            session = await provider.exchange_pkce(code, verifier)
            user = await provider.verify(session.access_token)
        except InvalidCredentials as exc:
            raise HTTPException(status_code=401, detail="OAuth exchange failed") from exc
        except AuthRateLimited as exc:
            raise _rate_limited(exc) from exc
        except ProviderUnavailable as exc:
            raise HTTPException(status_code=503, detail="Identity provider unavailable") from exc
        if user is None:
            raise HTTPException(status_code=401, detail="OAuth exchange failed")
        refresh_token = session.refresh_token
    if guest_user_id and (user.id != guest_user_id or user.is_anonymous):
        response = RedirectResponse(
            f"{callback_url}?error=GOOGLE_IDENTITY_CONFLICT", status_code=303,
            headers={"Referrer-Policy": "no-referrer"},
        )
        response.delete_cookie("research_oauth_state", path="/api/v1/auth/google/callback")
        return response
    request.app.state.identity_policy.observe_verified_user(user.id, user.is_anonymous)
    response = RedirectResponse(
        callback_url, status_code=303,
        headers={"Referrer-Policy": "no-referrer"},
    )
    _set_refresh_cookie(response, refresh_token, request)
    response.delete_cookie("research_oauth_state", path="/api/v1/auth/google/callback")
    return response


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, response: Response) -> None:
    """Revoke the available session token and clear its refresh cookie."""
    authorization = request.headers.get("authorization", "")
    if request.app.state.settings.mode == "mock":
        if authorization.startswith("Bearer "):
            request.app.state.demo_store.revoke_token(authorization.removeprefix("Bearer "))
        refresh_token = request.cookies.get("research_refresh_token")
        if refresh_token:
            request.app.state.demo_store.revoke_token(refresh_token)
        response.delete_cookie("research_refresh_token", path="/api/v1/auth")
        return
    if authorization.startswith("Bearer "):
        try:
            await request.app.state.identity_provider.sign_out(authorization.removeprefix("Bearer "))
        except (AuthRateLimited, ProviderUnavailable):
            pass
    response.delete_cookie("research_refresh_token", path="/api/v1/auth")


@router.get("/me")
async def get_me(user: CurrentUserDep) -> UserIdentity:
    """Return the identity resolved from the current bearer token."""
    return user
