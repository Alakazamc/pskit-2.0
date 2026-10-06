import pytest

from app.config import Settings


def live_settings(**overrides):
    values = {
        "mode": "live",
        "database_url": "postgresql://pskit_app:test@127.0.0.1:5432/postgres",
        "supabase_url": "https://auth.example.test",
        "supabase_publishable_key": "publishable-test",
        "auth_abuse_mode": "enforce",
        "auth_rate_limit_secret": "s" * 32,
        "auth_csrf_secret": "c" * 32,
        "auth_trusted_proxy_cidrs_json": '["127.0.0.1/32", "::1/128"]',
        "auth_captcha_required": True,
        "turnstile_secret_key": "server-only-turnstile-secret",
        "turnstile_hostnames_json": '["agent.bioailab.net"]',
    }
    values.update(overrides)
    return Settings(**values)


def test_live_auth_protection_configuration_is_strict():
    settings = live_settings()

    settings.require_live_config()
    assert settings.auth_trusted_proxy_cidrs() == ("127.0.0.1/32", "::1/128")
    assert settings.turnstile_hostnames() == ("agent.bioailab.net",)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"auth_rate_limit_secret": "short"}, "AUTH_RATE_LIMIT_SECRET"),
        ({"auth_csrf_secret": "short"}, "AUTH_CSRF_SECRET"),
        ({"auth_trusted_proxy_cidrs_json": "[]"}, "AUTH_TRUSTED_PROXY_CIDRS_JSON"),
        ({"auth_trusted_proxy_cidrs_json": '["127.0.0.1/999"]'}, "AUTH_TRUSTED_PROXY_CIDRS_JSON"),
        ({"auth_trusted_proxy_cidrs_json": '["127.0.0.1/8"]'}, "AUTH_TRUSTED_PROXY_CIDRS_JSON"),
        ({"turnstile_secret_key": ""}, "TURNSTILE_SECRET_KEY"),
        ({"turnstile_hostnames_json": "[]"}, "TURNSTILE_HOSTNAMES_JSON"),
        ({"turnstile_hostnames_json": '["https://agent.bioailab.net"]'}, "TURNSTILE_HOSTNAMES_JSON"),
        (
            {"turnstile_hostnames_json": '["agent.bioailab.net", "AGENT.BIOAILAB.NET"]'},
            "TURNSTILE_HOSTNAMES_JSON",
        ),
    ],
)
def test_live_auth_protection_rejects_missing_or_invalid_values(overrides, message):
    with pytest.raises(ValueError, match=message):
        live_settings(**overrides).require_live_config()


def test_auth_protection_can_be_disabled_during_coordinated_rollout():
    live_settings(
        auth_abuse_mode="off",
        auth_rate_limit_secret="",
        auth_csrf_secret="c" * 32,
        auth_trusted_proxy_cidrs_json="[]",
        auth_captcha_required=False,
        turnstile_secret_key="",
        turnstile_hostnames_json="[]",
    ).require_live_config()


def test_settings_from_env_reads_auth_protection(monkeypatch):
    monkeypatch.setenv("RESEARCH_AGENT_AUTH_ABUSE_MODE", "observe")
    monkeypatch.setenv("RESEARCH_AGENT_AUTH_RATE_LIMIT_SECRET", "x" * 32)
    monkeypatch.setenv("RESEARCH_AGENT_AUTH_CSRF_SECRET", "c" * 32)
    monkeypatch.setenv("RESEARCH_AGENT_AUTH_TRUSTED_PROXY_CIDRS_JSON", '["127.0.0.1/32"]')
    monkeypatch.setenv("RESEARCH_AGENT_AUTH_CAPTCHA_REQUIRED", "true")
    monkeypatch.setenv("TURNSTILE_SECRET_KEY", "secret")
    monkeypatch.setenv("TURNSTILE_HOSTNAMES_JSON", '["agent.bioailab.net"]')

    settings = Settings.from_env()

    assert settings.auth_abuse_mode == "observe"
    assert settings.auth_rate_limit_secret == "x" * 32
    assert settings.auth_csrf_secret == "c" * 32
    assert settings.auth_trusted_proxy_cidrs() == ("127.0.0.1/32",)
    assert settings.auth_captcha_required is True
    assert settings.turnstile_secret_key == "secret"
    assert settings.turnstile_hostnames() == ("agent.bioailab.net",)
