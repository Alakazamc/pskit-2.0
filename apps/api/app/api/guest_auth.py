import base64
import hashlib
import re
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from app.adapters.live.supabase_auth import (
    AuthRateLimited,
    EmailAlreadyInUse,
    IdentityAlreadyLinked,
    InvalidCredentials,
)
from app.api.auth import (
    CurrentUserDep,
    _captcha_error,
    _guard_limited,
    _network,
    _rate_limited,
    _record_provider,
    _require_cookie_csrf,
    _set_refresh_cookie,
)
from app.contracts.models import (
    AuthSessionResponse,
    EmailUpgradeRequest,
    EmailUpgradeStartResponse,
    EmailUpgradeVerifyRequest,
    GoogleUpgradeStartResponse,
)
from app.domain.auth_abuse import AuthAbuseLimited
from app.ports.captcha import CaptchaInvalid, CaptchaRequired, CaptchaUnavailable
from app.ports.providers import IdentityTransportUnavailable, ProviderUnavailable

router = APIRouter(prefix="/api/v1", tags=["auth"])


class AnonymousLoginRequest(BaseModel):
    captcha_token: str | None = None


@router.post("/auth/anonymous")
async def anonymous_login(
    payload: AnonymousLoginRequest,
    request: Request,
    response: Response,
) -> AuthSessionResponse:
    """Resume or create a limited anonymous session after abuse checks."""
    settings = request.app.state.settings
    _require_cookie_csrf(request)
    if not settings.effective_anonymous_enabled():
        raise HTTPException(status_code=404, detail="Anonymous login is unavailable")
    if settings.mode == "mock":
        store = request.app.state.demo_store
        bearer = request.headers.get("authorization", "")
        access_token = bearer.removeprefix("Bearer ") if bearer.startswith("Bearer ") else ""
        previous = access_token or request.cookies.get("research_refresh_token", "")
        user = store.user_for_token(previous)
        if user is not None and not user.is_anonymous:
            raise HTTPException(status_code=409, detail={"code": "MEMBER_SESSION_ACTIVE"})
        if user is not None and user.is_anonymous:
            token = previous
        else:
            token, user = store.issue_anonymous_token()
        session = AuthSessionResponse(access_token=token, expires_in=3600, user=user)
        refresh_token = token
    else:
        provider = request.app.state.identity_provider
        authorization = request.headers.get("authorization", "")
        bearer_user = None
        if authorization.startswith("Bearer "):
            try:
                bearer_user = await provider.verify(authorization.removeprefix("Bearer "))
            except AuthRateLimited as exc:
                headers = {"Retry-After": exc.retry_after} if exc.retry_after else None
                raise HTTPException(
                    status_code=429, detail={"code": "AUTH_RATE_LIMITED"}, headers=headers
                ) from exc
            except ProviderUnavailable as exc:
                raise HTTPException(
                    status_code=503, detail={"code": "IDENTITY_UNAVAILABLE"}
                ) from exc
            if bearer_user is not None and not bearer_user.is_anonymous:
                raise HTTPException(status_code=409, detail={"code": "MEMBER_SESSION_ACTIVE"})
        previous_refresh = request.cookies.get("research_refresh_token")
        if previous_refresh:
            try:
                previous_session = await provider.refresh(previous_refresh)
                previous_user = await provider.verify(previous_session.access_token)
            except InvalidCredentials:
                previous_user = None
            except AuthRateLimited as exc:
                headers = {"Retry-After": exc.retry_after} if exc.retry_after else None
                raise HTTPException(
                    status_code=429, detail={"code": "AUTH_RATE_LIMITED"}, headers=headers
                ) from exc
            except ProviderUnavailable as exc:
                raise HTTPException(
                    status_code=503, detail={"code": "IDENTITY_UNAVAILABLE"}
                ) from exc
            if previous_user is not None:
                if not previous_user.is_anonymous:
                    raise HTTPException(status_code=409, detail={"code": "MEMBER_SESSION_ACTIVE"})
                request.app.state.identity_policy.observe_verified_user(previous_user.id, True)
                _set_refresh_cookie(response, previous_session.refresh_token, request)
                return AuthSessionResponse(
                    access_token=previous_session.access_token,
                    expires_in=previous_session.expires_in,
                    user=previous_user,
                )
        if bearer_user is not None:
            request.app.state.identity_policy.observe_verified_user(bearer_user.id, True)
            return AuthSessionResponse(
                access_token=authorization.removeprefix("Bearer "),
                expires_in=3600,
                user=bearer_user,
            )
        if settings.anonymous_captcha_required and not payload.captcha_token:
            raise HTTPException(status_code=422, detail={"code": "CAPTCHA_REQUIRED"})
        address = request.client.host if request.client else "unknown"
        if not request.app.state.guest_rate_limiter.claim(address):
            raise HTTPException(
                status_code=429,
                detail={"code": "ANONYMOUS_RATE_LIMITED"},
                headers={"Retry-After": "3600"},
            )
        try:
            signed = await provider.sign_in_anonymously(payload.captcha_token)
            user = await provider.verify(signed.access_token)
        except AuthRateLimited as exc:
            headers = {"Retry-After": exc.retry_after} if exc.retry_after else None
            raise HTTPException(
                status_code=429, detail={"code": "AUTH_RATE_LIMITED"}, headers=headers
            ) from exc
        except InvalidCredentials as exc:
            raise HTTPException(
                status_code=422, detail={"code": "ANONYMOUS_SIGNUP_REJECTED"}
            ) from exc
        except ProviderUnavailable as exc:
            raise HTTPException(status_code=503, detail={"code": "IDENTITY_UNAVAILABLE"}) from exc
        if user is None or not user.is_anonymous:
            raise HTTPException(status_code=503, detail={"code": "IDENTITY_UNAVAILABLE"})
        session = AuthSessionResponse(
            access_token=signed.access_token,
            expires_in=signed.expires_in,
            user=user,
        )
        refresh_token = signed.refresh_token
    request.app.state.identity_policy.observe_verified_user(user.id, True)
    _set_refresh_cookie(response, refresh_token, request)
    return session


def _upgrade_email(raw: str) -> str:
    """Confirm that the verified email identity still owns the guest data."""
    email = raw.strip().lower()
    if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) is None:
        raise HTTPException(status_code=422, detail={"code": "INVALID_EMAIL"})
    return email


def _require_guest(user: CurrentUserDep, request: Request) -> None:
    """Require an authenticated anonymous user for account upgrade."""
    if not user.is_anonymous or request.app.state.identity_policy.tier_for(user.id) != "guest":
        raise HTTPException(status_code=409, detail={"code": "GUEST_REQUIRED"})


@router.post("/auth/upgrade/email")
async def start_email_upgrade(
    payload: EmailUpgradeRequest,
    user: CurrentUserDep,
    request: Request,
) -> EmailUpgradeStartResponse:
    """Attach an email address to a guest account for verification."""
    _require_guest(user, request)
    email = _upgrade_email(payload.email)
    if request.app.state.settings.mode == "mock":
        if request.app.state.demo_store.email_is_used(email):
            raise HTTPException(status_code=409, detail={"code": "EMAIL_ALREADY_IN_USE"})
    else:
        access_token = request.headers["authorization"].removeprefix("Bearer ")
        protection = request.app.state.auth_protection
        try:
            authorized = await protection.authorize_email_send(
                "guest_upgrade_email",
                email,
                payload.captcha_token,
                **_network(request),
            )
        except AuthAbuseLimited as exc:
            raise _guard_limited(exc) from exc
        except (CaptchaRequired, CaptchaInvalid, CaptchaUnavailable) as exc:
            raise _captcha_error(exc) from exc
        try:
            await request.app.state.identity_provider.update_guest_email(
                access_token,
                email,
                client_ip=authorized.client_ip,
            )
        except EmailAlreadyInUse as exc:
            protection.settle(authorized.claim, "rejected")
            _record_provider(request, "guest_upgrade_email", "rejected")
            raise HTTPException(status_code=409, detail={"code": "EMAIL_ALREADY_IN_USE"}) from exc
        except AuthRateLimited as exc:
            protection.settle(authorized.claim, "rejected")
            _record_provider(request, "guest_upgrade_email", "rate_limited")
            raise _rate_limited(exc) from exc
        except InvalidCredentials as exc:
            protection.settle(authorized.claim, "rejected")
            _record_provider(request, "guest_upgrade_email", "rejected")
            raise HTTPException(status_code=422, detail={"code": "EMAIL_UPGRADE_REJECTED"}) from exc
        except IdentityTransportUnavailable as exc:
            protection.settle(authorized.claim, exc.delivery)
            _record_provider(request, "guest_upgrade_email", exc.delivery)
            raise HTTPException(status_code=503, detail={"code": "IDENTITY_UNAVAILABLE"}) from exc
        except ProviderUnavailable as exc:
            protection.settle(authorized.claim, "unknown")
            _record_provider(request, "guest_upgrade_email", "unknown")
            raise HTTPException(status_code=503, detail={"code": "IDENTITY_UNAVAILABLE"}) from exc
        protection.settle(authorized.claim, "success")
        _record_provider(request, "guest_upgrade_email", "success")
    request.app.state.identity_policy.begin_email_upgrade(user.id, email)
    return EmailUpgradeStartResponse()


@router.post("/auth/upgrade/email/verify")
async def verify_email_upgrade(
    payload: EmailUpgradeVerifyRequest,
    user: CurrentUserDep,
    request: Request,
    response: Response,
) -> AuthSessionResponse:
    """Verify a guest's email and retain the same account identity."""
    _require_guest(user, request)
    email = _upgrade_email(payload.email)
    policy = request.app.state.identity_policy
    if policy.pending_email_for(user.id) != email:
        raise HTTPException(status_code=409, detail={"code": "UPGRADE_SESSION_MISMATCH"})
    if request.app.state.settings.mode == "mock":
        if payload.token != "000000":
            raise HTTPException(status_code=401, detail={"code": "INVALID_OTP"})
        previous = request.headers["authorization"].removeprefix("Bearer ")
        upgraded = request.app.state.demo_store.upgrade_anonymous(previous, email)
        if upgraded is None:
            raise HTTPException(status_code=409, detail={"code": "EMAIL_ALREADY_IN_USE"})
        access_token, verified_user = upgraded
        refresh_token = access_token
        expires_in = 3600
    else:
        provider = request.app.state.identity_provider
        protection = request.app.state.auth_protection
        try:
            authorized = protection.authorize_verification(
                "guest_upgrade_verify",
                email,
                **_network(request),
            )
        except AuthAbuseLimited as exc:
            raise _guard_limited(exc) from exc
        try:
            session = await provider.verify_otp(
                email,
                payload.token,
                "email_change",
                client_ip=authorized.client_ip,
            )
            verified_user = await provider.verify(session.access_token)
        except AuthRateLimited as exc:
            protection.settle(authorized.claim, "rejected")
            _record_provider(request, "guest_upgrade_verify", "rate_limited")
            raise _rate_limited(exc) from exc
        except InvalidCredentials as exc:
            protection.settle(authorized.claim, "rejected")
            _record_provider(request, "guest_upgrade_verify", "rejected")
            raise HTTPException(status_code=401, detail={"code": "INVALID_OTP"}) from exc
        except IdentityTransportUnavailable as exc:
            protection.settle(authorized.claim, exc.delivery)
            _record_provider(request, "guest_upgrade_verify", exc.delivery)
            raise HTTPException(status_code=503, detail={"code": "IDENTITY_UNAVAILABLE"}) from exc
        except ProviderUnavailable as exc:
            protection.settle(authorized.claim, "unknown")
            _record_provider(request, "guest_upgrade_verify", "unknown")
            raise HTTPException(status_code=503, detail={"code": "IDENTITY_UNAVAILABLE"}) from exc
        if verified_user is None or verified_user.id != user.id:
            protection.settle(authorized.claim, "rejected")
            _record_provider(request, "guest_upgrade_verify", "rejected")
            raise HTTPException(status_code=409, detail={"code": "UPGRADE_IDENTITY_MISMATCH"})
        if verified_user.is_anonymous or verified_user.email.strip().lower() != email:
            protection.settle(authorized.claim, "rejected")
            _record_provider(request, "guest_upgrade_verify", "rejected")
            raise HTTPException(status_code=409, detail={"code": "UPGRADE_NOT_CONFIRMED"})
        protection.settle(authorized.claim, "success")
        _record_provider(request, "guest_upgrade_verify", "success")
        access_token = session.access_token
        refresh_token = session.refresh_token
        expires_in = session.expires_in
    if not policy.complete_email_upgrade(user.id, email):
        raise HTTPException(status_code=409, detail={"code": "UPGRADE_SESSION_MISMATCH"})
    _set_refresh_cookie(response, refresh_token, request)
    return AuthSessionResponse(
        access_token=access_token,
        expires_in=expires_in,
        user=verified_user,
    )


@router.post("/auth/upgrade/google/start")
async def start_google_upgrade(
    user: CurrentUserDep,
    request: Request,
    response: Response,
) -> GoogleUpgradeStartResponse:
    """Start Google identity linking for an anonymous account."""
    _require_guest(user, request)
    settings = request.app.state.settings
    state, verifier = request.app.state.oauth_flows.create(guest_user_id=user.id)
    challenge = (
        base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest(),
        )
        .decode()
        .rstrip("=")
    )
    callback = (
        f"{settings.public_api_url.rstrip('/')}/api/v1/auth/google/callback?"
        f"{urlencode({'state': state})}"
    )
    if settings.mode == "mock":
        url = f"{callback}&code=mock-google"
    else:
        token = request.headers["authorization"].removeprefix("Bearer ")
        try:
            url = await request.app.state.identity_provider.link_google_identity(
                token,
                callback,
                challenge,
            )
        except IdentityAlreadyLinked as exc:
            request.app.state.oauth_flows.discard(state)
            raise HTTPException(
                status_code=409, detail={"code": "GOOGLE_IDENTITY_CONFLICT"}
            ) from exc
        except AuthRateLimited as exc:
            request.app.state.oauth_flows.discard(state)
            raise _rate_limited(exc) from exc
        except InvalidCredentials as exc:
            request.app.state.oauth_flows.discard(state)
            raise HTTPException(status_code=422, detail={"code": "GOOGLE_LINK_REJECTED"}) from exc
        except ProviderUnavailable as exc:
            request.app.state.oauth_flows.discard(state)
            raise HTTPException(status_code=503, detail={"code": "IDENTITY_UNAVAILABLE"}) from exc
    response.set_cookie(
        "research_oauth_state",
        state,
        httponly=True,
        max_age=300,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/api/v1/auth/google/callback",
    )
    return GoogleUpgradeStartResponse(url=url)
