"""Regression contracts for the public login page delivery path."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
FRONTEND = ROOT / "new_frontend"
DEPLOY = ROOT / "deploy/agent"


def test_login_background_prefers_compact_avif_with_webp_fallback():
    css = (FRONTEND / "src/styles/login.css").read_text()

    for theme in ("day", "night"):
        avif = FRONTEND / f"src/assets/login-biology-{theme}.avif"
        assert avif.exists()
        assert avif.stat().st_size < 100_000
        assert f'url("../assets/login-biology-{theme}.avif") type("image/avif")' in css
        assert f'url("../assets/login-biology-{theme}.webp") type("image/webp")' in css

    assert "@supports (background-image: image-set(" in css


def test_public_nginx_compresses_text_and_caches_hashed_assets():
    for filename in ("host-nginx-agent-aliyun.conf", "host-nginx-agent-split.conf"):
        config = (DEPLOY / filename).read_text()
        assert "gzip on;" in config
        assert "gzip_vary on;" in config
        assert "gzip_types text/css application/javascript application/json image/svg+xml;" in config
        assert 'add_header Cache-Control "public, max-age=31536000, immutable" always;' in config

