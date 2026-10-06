"""Every independently installed Agent vhost carries the same auth-only controls."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
AGENT = ROOT / "deploy/agent"


@pytest.mark.parametrize("name,zone", [
    ("host-nginx-agent-aliyun.conf", "pskit_auth"),
    ("host-nginx-agent-split.conf", "pskit_split_auth"),
    ("host-nginx-agent-staging.conf", "pskit_staging_auth"),
])
def test_vhost_limits_only_expensive_auth_routes(name: str, zone: str):
    text = (AGENT / name).read_text()
    assert f"zone={zone}_mail:" in text and "rate=6r/m" in text
    assert f"limit_req_zone $server_name zone={zone}_mail_site:" in text
    assert f"zone={zone}_mail_site:" in text and "rate=2r/s" in text
    assert f"zone={zone}_login:" in text and "rate=10r/m" in text
    assert f"zone={zone}_verify:" in text and "rate=30r/m" in text
    assert f"zone={zone}_connections:" in text
    assert "limit_req_dry_run on;" in text
    assert "limit_req_status 429;" in text
    assert 'return 429 \'{"detail":{"code":"AUTH_RATE_LIMITED"}}\';' in text
    assert 'add_header Retry-After "60" always;' in text
    assert "proxy_set_header X-PSKit-Client-IP $remote_addr;" in text
    direct_verify = text.split("location = /auth/v1/verify", 1)[1].split("}", 1)[0]
    assert f"limit_req zone={zone}_verify burst=10 nodelay;" in direct_verify
    assert f"limit_conn {zone}_connections 10;" in direct_verify
    for route in (
        "/api/v1/auth/signup",
        "/api/v1/auth/password/recover",
        "/api/v1/auth/upgrade/email",
        "/api/v1/auth/login",
        "/api/v1/auth/verify",
        "/api/v1/auth/upgrade/email/verify",
    ):
        assert f"location = {route}" in text


def test_normal_api_sse_uploads_and_private_admin_do_not_inherit_auth_limits():
    text = (AGENT / "host-nginx-agent-aliyun.conf").read_text()
    generic = text.split("location ^~ /api/v1/ {", 1)[1].split("}", 1)[0]
    admin = text.split("location ^~ /api/v1/admin", 1)[1].split("}", 1)[0]
    assert "limit_req" not in generic
    assert "limit_conn" not in generic
    assert "proxy_set_header X-PSKit-Client-IP $remote_addr;" in generic
    assert "limit_req" not in admin
    assert "limit_conn" not in admin
    assert "location = /auth/v1/verify" in text
