import httpx
import pytest

from app.adapters.live.turnstile import TurnstileVerifier
from app.ports.captcha import CaptchaInvalid, CaptchaUnavailable


@pytest.mark.asyncio
async def test_turnstile_accepts_only_matching_action_and_hostname():
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "success": True,
                "action": "signup",
                "hostname": "agent.bioailab.net",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        verifier = TurnstileVerifier(
            "secret",
            ("agent.bioailab.net",),
            client=client,
        )
        await verifier.verify(
            "browser-token",
            remote_ip="203.0.113.8",
            action="signup",
        )

    body = requests[0].content.decode()
    assert requests[0].url == httpx.URL("https://challenges.cloudflare.com/turnstile/v0/siteverify")
    assert "secret=secret" in body
    assert "response=browser-token" in body
    assert "remoteip=203.0.113.8" in body


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"success": False, "error-codes": ["timeout-or-duplicate"]},
        {"success": True, "action": "recovery", "hostname": "agent.bioailab.net"},
        {"success": True, "action": "signup", "hostname": "evil.example"},
    ],
)
async def test_turnstile_rejects_failed_or_mismatched_proofs(payload):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=payload))
    ) as client:
        verifier = TurnstileVerifier("secret", ("agent.bioailab.net",), client=client)
        with pytest.raises(CaptchaInvalid):
            await verifier.verify("token", remote_ip="203.0.113.8", action="signup")


@pytest.mark.asyncio
async def test_turnstile_transport_failure_is_unavailable_and_not_retried():
    calls = 0

    def fail(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timeout")

    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
        verifier = TurnstileVerifier("secret", ("agent.bioailab.net",), client=client)
        with pytest.raises(CaptchaUnavailable):
            await verifier.verify("token", remote_ip="203.0.113.8", action="signup")

    assert calls == 1
