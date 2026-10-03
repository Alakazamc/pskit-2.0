"""A fresh staging setup may only create independent, private configuration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deploy.agent.scripts.prepare_staging import prepare_staging


@pytest.fixture(autouse=True)
def fixed_backend_image(monkeypatch):
    monkeypatch.setattr(
        "deploy.agent.scripts.prepare_staging._image_id", lambda image: "sha256:" + "a" * 64,
    )


@pytest.fixture
def release(tmp_path: Path) -> tuple[Path, Path]:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>staging</html>")
    return tmp_path / "private" / "staging", dist


def _values(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text().splitlines()
                if line and not line.startswith("#") and "=" in line)


def test_generates_distinct_private_staging_secrets(release, monkeypatch):
    target, dist = release
    monkeypatch.setenv("STAGING_AUTH_KEYS_NODE", "node")
    prepare_staging(target, backend_image="pskit-agent-backend:test-fixed", frontend_dist=dist)

    assert target.stat().st_mode & 0o777 == 0o700
    assert sorted(p.name for p in target.iterdir()) == sorted([
        "supabase.env", "litellm.env", "backend.env.base", "admin.env", "cloud.env",
        "proxy.env", "seed.env", "manifest.json",
    ])
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in target.iterdir())
    supabase = _values(target / "supabase.env")
    litellm = _values(target / "litellm.env")
    backend = _values(target / "backend.env.base")
    assert supabase["POSTGRES_PASSWORD"] != "your-super-secret-and-long-postgres-password"
    assert len({supabase["POSTGRES_PASSWORD"], supabase["JWT_SECRET"],
                litellm["LITELLM_MASTER_KEY"], litellm["LITELLM_SALT_KEY"]}) == 4
    assert supabase["SUPABASE_PUBLISHABLE_KEY"].startswith("sb_publishable_")
    assert supabase["SUPABASE_SECRET_KEY"].startswith("sb_secret_")
    private_keys = json.loads(supabase["JWT_KEYS"])
    public_keys = json.loads(supabase["JWT_JWKS"])["keys"]
    assert any(key.get("d") for key in private_keys)
    assert all("d" not in key for key in public_keys)
    assert supabase["SITE_URL"] == "http://10.9.8.1:18132"
    assert supabase["API_EXTERNAL_URL"] == "http://10.9.8.1:18132/auth/v1"
    assert backend["RESEARCH_AGENT_DATABASE_URL"].startswith("postgresql://pskit_app:")
    assert len(backend["RESEARCH_AGENT_ADMIN_API_KEY"]) >= 32
    assert "MODEL_GATEWAY_API_KEY" not in backend
    assert litellm["LITELLM_PUBLIC_PORT"] == "4002"
    cloud = _values(target / "cloud.env")
    assert cloud["AGENT_BACKEND_ENV_FILE"] == str(target / "backend.env")
    assert cloud["AGENT_AF3_PROXY_KEY_FILE"] == str(target / "proxy.env")
    assert cloud["AGENT_WEB_IMAGE"] == "pskit-agent-web:unused-staging"
    manifest = json.loads((target / "manifest.json").read_text())
    assert manifest["backend_image"] == "pskit-agent-backend:test-fixed"
    assert manifest["frontend_dist"] == str(dist)
    assert not any(secret in json.dumps(manifest) for secret in (
        supabase["POSTGRES_PASSWORD"], supabase["JWT_SECRET"],
    ))


def test_existing_or_symlink_target_is_untouched(release, monkeypatch):
    target, dist = release
    monkeypatch.setenv("STAGING_AUTH_KEYS_NODE", "node")
    target.parent.mkdir(parents=True)
    target.mkdir()
    marker = target / "marker"
    marker.write_text("keep")
    with pytest.raises(FileExistsError):
        prepare_staging(target, backend_image="fixed", frontend_dist=dist)
    assert marker.read_text() == "keep"
    target.rename(target.with_name("original"))
    target.symlink_to(target.with_name("original"), target_is_directory=True)
    with pytest.raises((FileExistsError, ValueError)):
        prepare_staging(target, backend_image="fixed", frontend_dist=dist)
    assert marker.read_text() == "keep"


def test_mid_generation_failure_leaves_no_valid_manifest(release, monkeypatch):
    target, dist = release
    monkeypatch.setenv("STAGING_AUTH_KEYS_NODE", "/does/not/exist")
    with pytest.raises((RuntimeError, OSError)):
        prepare_staging(target, backend_image="fixed", frontend_dist=dist)
    assert not (target / "manifest.json").exists()
    assert not target.exists()


def test_output_does_not_contain_secrets(release, monkeypatch, capsys):
    target, dist = release
    monkeypatch.setenv("STAGING_AUTH_KEYS_NODE", "node")
    prepare_staging(target, backend_image="fixed", frontend_dist=dist)
    output = "".join(capsys.readouterr())
    assert _values(target / "supabase.env")["POSTGRES_PASSWORD"] not in output
    assert _values(target / "litellm.env")["LITELLM_MASTER_KEY"] not in output
