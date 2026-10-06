"""Static contracts for layered public authentication controls."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SUPABASE = ROOT / "infra/supabase"
AGENT = ROOT / "deploy/agent"


def test_cloud_gotrue_has_explicit_inner_limits_and_signup_stays_closed_by_default():
    text = (SUPABASE / "compose.cloud.yaml").read_text()
    for line in (
        "GOTRUE_SMTP_MAX_FREQUENCY: ${GOTRUE_SMTP_MAX_FREQUENCY:-60s}",
        "GOTRUE_RATE_LIMIT_EMAIL_SENT: ${GOTRUE_RATE_LIMIT_EMAIL_SENT:-60}",
        "GOTRUE_RATE_LIMIT_OTP: ${GOTRUE_RATE_LIMIT_OTP:-30}",
        "GOTRUE_RATE_LIMIT_VERIFY: ${GOTRUE_RATE_LIMIT_VERIFY:-30}",
        "GOTRUE_RATE_LIMIT_TOKEN_REFRESH: ${GOTRUE_RATE_LIMIT_TOKEN_REFRESH:-150}",
        "GOTRUE_RATE_LIMIT_HEADER: X-PSKit-Client-IP",
        "GOTRUE_DISABLE_SIGNUP: ${CLOUD_DISABLE_SIGNUP:-true}",
    ):
        assert line in text
    assert "GOTRUE_SECURITY_CAPTCHA_ENABLED" not in text


def test_backend_examples_require_server_only_auth_protection_values():
    cloud = (AGENT / "cloud.backend.env.example").read_text()
    generic = (ROOT / "new_backend/.env.example").read_text()
    for key in (
        "RESEARCH_AGENT_AUTH_ABUSE_MODE",
        "RESEARCH_AGENT_AUTH_RATE_LIMIT_SECRET",
        "RESEARCH_AGENT_AUTH_TRUSTED_PROXY_CIDRS_JSON",
        "RESEARCH_AGENT_AUTH_CAPTCHA_REQUIRED",
        "TURNSTILE_SECRET_KEY",
        "TURNSTILE_HOSTNAMES_JSON",
    ):
        assert key in cloud
        assert key in generic
    assert "replace-with" in cloud
    assert "TURNSTILE_SECRET_KEY=" not in (ROOT / "new_frontend/.env.example").read_text()
