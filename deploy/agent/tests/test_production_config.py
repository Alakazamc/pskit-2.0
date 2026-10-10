"""Production auth preflight is local, non-executing and fails closed."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
VALIDATOR = ROOT / "deploy/agent/scripts/production_config.py"

# Independently transcribed from Cloudflare's official testing documentation.
PUBLIC_TEST_KEYS = (
    "1x00000000000000000000AA", "2x00000000000000000000AB",
    "1x00000000000000000000BB", "2x00000000000000000000BB",
    "3x00000000000000000000FF", "1x0000000000000000000000000000000AA",
    "2x0000000000000000000000000000000AA", "3x0000000000000000000000000000000AA",
)


@pytest.fixture
def production_env(tmp_path):
    backend = {
        "RESEARCH_AGENT_AUTH_ABUSE_MODE": "enforce",
        "RESEARCH_AGENT_AUTH_RATE_LIMIT_SECRET": "r" * 32,
        "RESEARCH_AGENT_AUTH_CSRF_SECRET": "c" * 32,
        "RESEARCH_AGENT_AUTH_TRUSTED_PROXY_CIDRS_JSON": '["127.0.0.1/32"]',
        "RESEARCH_AGENT_AUTH_CAPTCHA_REQUIRED": "true",
        "TURNSTILE_SECRET_KEY": "private-production-secret",
        "TURNSTILE_HOSTNAMES_JSON": '["agent.bioailab.net"]',
    }
    cloud = {"TURNSTILE_SITE_KEY": "production-site-key"}
    return tmp_path, backend, cloud


def run_validator(production_env):
    directory, backend, cloud = production_env
    for name, values in (("backend.env", backend), ("cloud.env", cloud)):
        path = directory / name
        path.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
        path.chmod(0o600)
    return subprocess.run(
        [sys.executable, str(VALIDATOR),
         "--backend-env-file", str(directory / "backend.env"),
         "--cloud-env-file", str(directory / "cloud.env")],
        cwd=directory, env={"PATH": os.environ["PATH"]},
        capture_output=True, text=True, timeout=5, check=False,
    )


def test_enforced_production_config_passes_without_docker_or_network(production_env):
    result = run_validator(production_env)
    assert result.returncode == 0, result.stderr
    assert "configuration" in result.stdout.lower()
    assert "private-production-secret" not in result.stdout + result.stderr


@pytest.mark.parametrize("key,value", [
    ("RESEARCH_AGENT_AUTH_ABUSE_MODE", "observe"),
    ("RESEARCH_AGENT_AUTH_ABUSE_MODE", "off"),
    ("RESEARCH_AGENT_AUTH_CAPTCHA_REQUIRED", "false"),
    ("RESEARCH_AGENT_AUTH_RATE_LIMIT_SECRET", "short"),
    ("RESEARCH_AGENT_AUTH_CSRF_SECRET", "short"),
    ("RESEARCH_AGENT_AUTH_TRUSTED_PROXY_CIDRS_JSON", '["0.0.0.0/0"]'),
    ("RESEARCH_AGENT_AUTH_TRUSTED_PROXY_CIDRS_JSON", '["127.0.0.1/32", "10.0.0.0/8"]'),
    ("RESEARCH_AGENT_AUTH_TRUSTED_PROXY_CIDRS_JSON", "not-json"),
    ("TURNSTILE_HOSTNAMES_JSON", "[]"),
    ("TURNSTILE_HOSTNAMES_JSON", '["localhost"]'),
    ("TURNSTILE_HOSTNAMES_JSON", '["127.0.0.1"]'),
    ("TURNSTILE_HOSTNAMES_JSON", '["staging.example.org"]'),
    ("TURNSTILE_HOSTNAMES_JSON", '["*"]'),
    ("TURNSTILE_HOSTNAMES_JSON", '"agent.bioailab.net"'),
])
def test_unsafe_auth_configuration_is_rejected(production_env, key, value):
    production_env[1][key] = value
    result = run_validator(production_env)
    assert result.returncode != 0
    assert key in result.stderr
    assert "private-production-secret" not in result.stdout + result.stderr


@pytest.mark.parametrize("field", ["TURNSTILE_SITE_KEY", "TURNSTILE_SECRET_KEY"])
@pytest.mark.parametrize("value", ["", "   ", "replace-with-real-key", *PUBLIC_TEST_KEYS])
def test_missing_placeholder_and_every_official_test_key_are_rejected(
    production_env, field, value,
):
    target = production_env[2] if field == "TURNSTILE_SITE_KEY" else production_env[1]
    target[field] = f'"{value}"'
    result = run_validator(production_env)
    assert result.returncode != 0
    assert field in result.stderr


@pytest.mark.parametrize("field", ["TURNSTILE_SITE_KEY", "TURNSTILE_SECRET_KEY"])
def test_absent_captcha_key_is_rejected(production_env, field):
    target = production_env[2] if field == "TURNSTILE_SITE_KEY" else production_env[1]
    del target[field]
    result = run_validator(production_env)
    assert result.returncode != 0
    assert field in result.stderr


def test_quoted_config_passes_and_shell_substitution_is_never_executed(production_env):
    directory, backend, cloud = production_env
    backend["RESEARCH_AGENT_AUTH_ABUSE_MODE"] = '"enforce"'
    cloud["UNUSED_OPERATOR_VALUE"] = "$(touch injected)"
    result = run_validator(production_env)
    assert result.returncode == 0, result.stderr
    assert not (directory / "injected").exists()


def test_csrf_and_rate_limit_secrets_must_be_distinct(production_env):
    production_env[1]["RESEARCH_AGENT_AUTH_CSRF_SECRET"] = "r" * 32
    result = run_validator(production_env)
    assert result.returncode != 0
    assert "RESEARCH_AGENT_AUTH_CSRF_SECRET" in result.stderr


@pytest.mark.parametrize("field", ["TURNSTILE_SITE_KEY", "TURNSTILE_SECRET_KEY"])
@pytest.mark.parametrize("suffix", [" # comment", "${UNSET:-}", "$UNSET"])
def test_compose_normalization_cannot_hide_public_test_keys(production_env, field, suffix):
    target = production_env[2] if field == "TURNSTILE_SITE_KEY" else production_env[1]
    target[field] = PUBLIC_TEST_KEYS[0] + suffix
    result = run_validator(production_env)
    assert result.returncode != 0
    assert field in result.stderr


@pytest.mark.parametrize("value", [
    '"production-site-key" # a comment', "'production-site-key' # a comment",
    "production-site-key # a comment", '"production#site-key"', "production#site-key",
])
def test_literal_quotes_and_compose_comments_are_parsed(production_env, value):
    production_env[2]["TURNSTILE_SITE_KEY"] = value
    result = run_validator(production_env)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("value", ['"production-key"suffix', '"production\\nkey"'])
def test_ambiguous_escape_or_quote_suffix_is_rejected(production_env, value):
    production_env[2]["TURNSTILE_SITE_KEY"] = value
    result = run_validator(production_env)
    assert result.returncode != 0


def test_comment_cannot_inflate_secret_length(production_env):
    production_env[1]["RESEARCH_AGENT_AUTH_CSRF_SECRET"] = "short # " + "x" * 40
    result = run_validator(production_env)
    assert result.returncode != 0
    assert "RESEARCH_AGENT_AUTH_CSRF_SECRET" in result.stderr


def test_comments_cannot_make_equal_secrets_distinct(production_env):
    production_env[1]["RESEARCH_AGENT_AUTH_RATE_LIMIT_SECRET"] = "r" * 32 + " # rate"
    production_env[1]["RESEARCH_AGENT_AUTH_CSRF_SECRET"] = "r" * 32 + " # csrf"
    result = run_validator(production_env)
    assert result.returncode != 0
    assert "distinct" in result.stderr


@pytest.mark.parametrize("field,value", [
    ("RESEARCH_AGENT_AUTH_CAPTCHA_REQUIRED", '"true "'),
    ("RESEARCH_AGENT_AUTH_CAPTCHA_REQUIRED", '" true"'),
    ("RESEARCH_AGENT_AUTH_ABUSE_MODE", '"enforce "'),
    ("RESEARCH_AGENT_AUTH_CSRF_SECRET", '"short' + ' ' * 32 + '"'),
])
def test_quoted_inner_whitespace_is_rejected_not_normalized(production_env, field, value):
    production_env[1][field] = value
    result = run_validator(production_env)
    assert result.returncode != 0
    assert field in result.stderr
