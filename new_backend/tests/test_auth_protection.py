from dataclasses import dataclass

import pytest

from app.domain.auth_abuse import AuthAbuseLimited, AuthClaim
from app.ports.captcha import CaptchaInvalid, CaptchaRequired
from app.services.auth_protection import AuthProtection
from app.services.client_ip import TrustedClientIpResolver
from app.services.observability import RequestMetrics


@dataclass
class FakeGuard:
    limited: bool = False

    def __post_init__(self):
        self.calls: list[tuple] = []

    def consume_send_ip(self, action, client_ip):
        self.calls.append(("ip", action, client_ip))
        if self.limited:
            raise AuthAbuseLimited(17, "send_ip_10m")

    def claim_email_send(self, action, email):
        self.calls.append(("email", action, email))
        return AuthClaim("claim-token", action)

    def claim_login(self, email, client_ip):
        self.calls.append(("login", email, client_ip))
        return AuthClaim("login-token", "password_login")

    def claim_verification(self, action, email, client_ip):
        self.calls.append(("verify", action, email, client_ip))
        return AuthClaim("verify-token", action)

    def settle(self, claim, outcome):
        self.calls.append(("settle", claim.token, outcome))


class FakeCaptcha:
    def __init__(self, failure=None):
        self.calls = []
        self.failure = failure

    async def verify(self, token, *, remote_ip, action):
        self.calls.append((token, remote_ip, action))
        if self.failure:
            raise self.failure


def protection(guard, captcha, *, mode="enforce"):
    return AuthProtection(
        guard=guard,
        captcha=captcha,
        resolver=TrustedClientIpResolver(("10.0.0.0/8",)),
        metrics=RequestMetrics(),
        mode=mode,
        captcha_required=True,
    )


@pytest.mark.asyncio
async def test_email_send_consumes_ip_before_requiring_captcha():
    guard = FakeGuard()
    service = protection(guard, FakeCaptcha())

    with pytest.raises(CaptchaRequired):
        await service.authorize_email_send(
            "signup",
            "User@Example.org",
            None,
            peer_ip="10.0.0.4",
            asserted_ip="203.0.113.9",
        )

    assert guard.calls == [("ip", "signup_send", "203.0.113.9")]


@pytest.mark.asyncio
async def test_valid_captcha_precedes_email_claim_and_returns_canonical_client_ip():
    guard = FakeGuard()
    captcha = FakeCaptcha()
    service = protection(guard, captcha)

    authorized = await service.authorize_email_send(
        "recovery",
        "User@Example.org",
        "proof",
        peer_ip="10.0.0.4",
        asserted_ip="203.0.113.9",
    )

    assert authorized.client_ip == "203.0.113.9"
    assert authorized.claim == AuthClaim("claim-token", "recovery_send")
    assert captcha.calls == [("proof", "203.0.113.9", "recovery")]
    assert guard.calls == [
        ("ip", "recovery_send", "203.0.113.9"),
        ("email", "recovery_send", "User@Example.org"),
    ]


@pytest.mark.asyncio
async def test_invalid_captcha_does_not_consume_email_budget():
    guard = FakeGuard()
    service = protection(guard, FakeCaptcha(CaptchaInvalid()))

    with pytest.raises(CaptchaInvalid):
        await service.authorize_email_send(
            "signup",
            "user@example.org",
            "bad",
            peer_ip="10.0.0.4",
            asserted_ip="203.0.113.9",
        )

    assert guard.calls == [("ip", "signup_send", "203.0.113.9")]


@pytest.mark.asyncio
async def test_observe_mode_records_would_reject_but_allows_upstream():
    guard = FakeGuard(limited=True)
    metrics = RequestMetrics()
    service = AuthProtection(
        guard=guard,
        captcha=FakeCaptcha(),
        resolver=TrustedClientIpResolver(("10.0.0.0/8",)),
        metrics=metrics,
        mode="observe",
        captcha_required=True,
    )

    authorized = await service.authorize_email_send(
        "signup",
        "user@example.org",
        "proof",
        peer_ip="10.0.0.4",
        asserted_ip="203.0.113.9",
    )

    assert authorized.claim is None
    assert metrics.snapshot()["auth_security"] == [
        {
            "component": "captcha",
            "action": "signup",
            "outcome": "accepted",
            "rule": "",
            "count": 1,
        },
        {
            "component": "guard",
            "action": "signup",
            "outcome": "would_reject",
            "rule": "send_ip_10m",
            "count": 1,
        },
    ]


def test_auth_security_metrics_are_low_cardinality_and_contain_no_subjects():
    metrics = RequestMetrics()
    metrics.record_auth_security("captcha", "signup", "invalid", "proof")

    snapshot = metrics.snapshot()

    assert snapshot["auth_security"] == [
        {
            "component": "captcha",
            "action": "signup",
            "outcome": "invalid",
            "rule": "proof",
            "count": 1,
        }
    ]
    assert "example.org" not in repr(snapshot)
    assert "203.0.113" not in repr(snapshot)
