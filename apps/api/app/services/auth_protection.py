"""One application boundary for public authentication abuse controls."""

from dataclasses import dataclass
from typing import Literal

from app.domain.auth_abuse import (
    AuthAbuseGuard,
    AuthAbuseLimited,
    AuthAction,
    AuthClaim,
    AuthOutcome,
)
from app.ports.captcha import CaptchaInvalid, CaptchaRequired, CaptchaUnavailable, CaptchaVerifier
from app.services.client_ip import TrustedClientIpResolver
from app.services.observability import RequestMetrics

PublicEmailAction = Literal["signup", "recovery", "guest_upgrade_email"]
ProtectionMode = Literal["off", "observe", "enforce"]

_SEND_ACTIONS: dict[PublicEmailAction, AuthAction] = {
    "signup": "signup_send",
    "recovery": "recovery_send",
    "guest_upgrade_email": "guest_upgrade_send",
}


@dataclass(frozen=True)
class AuthAuthorization:
    """Admission decision and canonical address forwarded to Supabase."""

    claim: AuthClaim | None
    client_ip: str


class AuthProtection:
    """Coordinate trusted IPs, PostgreSQL claims, CAPTCHA and metrics."""

    def __init__(
        self,
        *,
        guard: AuthAbuseGuard | None,
        captcha: CaptchaVerifier | None,
        resolver: TrustedClientIpResolver,
        metrics: RequestMetrics,
        mode: ProtectionMode,
        captcha_required: bool,
    ) -> None:
        self.guard = guard
        self.captcha = captcha
        self.resolver = resolver
        self.metrics = metrics
        self.mode = mode
        self.captcha_required = captcha_required
        if mode != "off" and guard is None:
            raise ValueError("Auth abuse guard is required when protection is enabled")
        if captcha_required and captcha is None:
            raise ValueError("CAPTCHA verifier is required when CAPTCHA is enabled")

    def resolve_client_ip(self, *, peer_ip: str | None, asserted_ip: str | None) -> str:
        """Resolve the only client address allowed in provider requests."""
        return self.resolver.resolve(peer_ip, asserted_ip)

    async def authorize_email_send(
        self,
        action: PublicEmailAction,
        email: str,
        captcha_token: str | None,
        *,
        peer_ip: str | None,
        asserted_ip: str | None,
    ) -> AuthAuthorization:
        """Preclaim an email send after IP admission and CAPTCHA verification."""
        client_ip = self.resolve_client_ip(peer_ip=peer_ip, asserted_ip=asserted_ip)
        if self.mode == "off":
            return AuthAuthorization(None, client_ip)
        assert self.guard is not None
        guard_action = _SEND_ACTIONS[action]
        ip_denied = False
        try:
            self.guard.consume_send_ip(guard_action, client_ip)
        except AuthAbuseLimited as exc:
            ip_denied = True
            self._limited(action, exc)
        if self.captcha_required:
            if not captcha_token:
                self.metrics.record_auth_security("captcha", action, "required")
                raise CaptchaRequired
            assert self.captcha is not None
            try:
                await self.captcha.verify(captcha_token, remote_ip=client_ip, action=action)
            except CaptchaInvalid:
                self.metrics.record_auth_security("captcha", action, "invalid")
                raise
            except CaptchaUnavailable:
                self.metrics.record_auth_security("captcha", action, "unavailable")
                raise
            self.metrics.record_auth_security("captcha", action, "accepted")
        if ip_denied:
            return AuthAuthorization(None, client_ip)
        try:
            claim = self.guard.claim_email_send(guard_action, email)
        except AuthAbuseLimited as exc:
            self._limited(action, exc)
            claim = None
        return AuthAuthorization(claim, client_ip)

    def authorize_login(
        self,
        email: str,
        *,
        peer_ip: str | None,
        asserted_ip: str | None,
    ) -> AuthAuthorization:
        """Preclaim a password attempt across its account and IP budgets."""
        client_ip = self.resolve_client_ip(peer_ip=peer_ip, asserted_ip=asserted_ip)
        if self.mode == "off":
            return AuthAuthorization(None, client_ip)
        assert self.guard is not None
        try:
            claim = self.guard.claim_login(email, client_ip)
        except AuthAbuseLimited as exc:
            self._limited("login", exc)
            claim = None
        return AuthAuthorization(claim, client_ip)

    def authorize_verification(
        self,
        action: Literal["otp_verify", "guest_upgrade_verify"],
        email: str,
        *,
        peer_ip: str | None,
        asserted_ip: str | None,
    ) -> AuthAuthorization:
        """Preclaim an OTP attempt across its account and IP budgets."""
        client_ip = self.resolve_client_ip(peer_ip=peer_ip, asserted_ip=asserted_ip)
        if self.mode == "off":
            return AuthAuthorization(None, client_ip)
        assert self.guard is not None
        public_action = "guest_upgrade_email" if action == "guest_upgrade_verify" else "verify"
        try:
            claim = self.guard.claim_verification(action, email, client_ip)
        except AuthAbuseLimited as exc:
            self._limited(public_action, exc)
            claim = None
        return AuthAuthorization(claim, client_ip)

    def settle(self, claim: AuthClaim | None, outcome: AuthOutcome) -> None:
        """Settle a real guard claim; observe/off decisions have no claim."""
        if claim is not None:
            assert self.guard is not None
            self.guard.settle(claim, outcome)

    def _limited(self, action: str, error: AuthAbuseLimited) -> None:
        outcome = "would_reject" if self.mode == "observe" else "rejected"
        self.metrics.record_auth_security("guard", action, outcome, error.rule)
        if self.mode == "enforce":
            raise error
