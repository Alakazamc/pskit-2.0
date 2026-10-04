"""A repeat release updates artifact pins without regenerating staging secrets."""

import json
from pathlib import Path

import pytest

from deploy.agent.scripts import pin_staging_release as pins
from deploy.agent.scripts.prepare_staging import _dist_hash


@pytest.fixture
def stage(tmp_path, monkeypatch):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    for name, content in {
        "cloud.env": "AGENT_BACKEND_IMAGE=pskit-agent-backend:old\nOTHER=preserve\n",
        "manifest.json": json.dumps({
            "backend_image": "pskit-agent-backend:old",
            "backend_image_id": "sha256:" + "a" * 64,
            "frontend_dist": "/old/dist", "frontend_dist_sha256": "old",
            "projects": list(pins.PROJECTS.values()),
        }),
        "supabase.env": "POSTGRES_PASSWORD=unchanged\n",
        "backend.env": "MODEL_GATEWAY_API_KEY=unchanged\n",
    }.items():
        path = root / name
        path.write_text(content)
        path.chmod(0o600)
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("release")
    monkeypatch.setattr(pins, "_image_id", lambda image: "sha256:" + "b" * 64)
    monkeypatch.setattr(pins, "_running_projects", list)
    return root, dist


def test_pin_preserves_secrets_and_backs_up_old_pins(stage):
    root, dist = stage
    old_cloud = (root / "cloud.env").read_bytes()
    old_manifest = (root / "manifest.json").read_bytes()
    backup = pins.pin_release(root, backend_image="pskit-agent-backend:new", frontend_dist=dist)
    assert (backup / "cloud.env").read_bytes() == old_cloud
    assert (backup / "manifest.json").read_bytes() == old_manifest
    assert (root / "supabase.env").read_text() == "POSTGRES_PASSWORD=unchanged\n"
    assert (root / "backend.env").read_text() == "MODEL_GATEWAY_API_KEY=unchanged\n"
    assert (root / "cloud.env").read_text() == (
        "AGENT_BACKEND_IMAGE=pskit-agent-backend:new\nOTHER=preserve\n")
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["backend_image_id"] == "sha256:" + "b" * 64
    assert manifest["frontend_dist_sha256"] == _dist_hash(dist)
    for path in [root / "manifest.json", root / "cloud.env", backup / "manifest.json"]:
        assert path.stat().st_mode & 0o777 == 0o600


def test_pin_refuses_running_staging_before_writes(stage, monkeypatch):
    root, dist = stage
    original = (root / "manifest.json").read_bytes()
    monkeypatch.setattr(pins, "_running_projects", lambda: ["pskit-agent-staging"])
    with pytest.raises(ValueError, match="stopped"):
        pins.pin_release(root, backend_image="pskit-agent-backend:new", frontend_dist=dist)
    assert (root / "manifest.json").read_bytes() == original


@pytest.mark.parametrize("image", ["pskit-agent-backend:latest", "pskit-agent-backend"])
def test_pin_requires_fixed_image(stage, image):
    root, dist = stage
    with pytest.raises(ValueError, match="fixed"):
        pins.pin_release(root, backend_image=image, frontend_dist=dist)


def test_pin_rejects_unsafe_private_file(stage):
    root, dist = stage
    (root / "cloud.env").chmod(0o644)
    with pytest.raises(ValueError, match="unsafe"):
        pins.pin_release(root, backend_image="pskit-agent-backend:new", frontend_dist=dist)


def test_pin_restores_both_files_on_interrupted_write(stage, monkeypatch):
    root, dist = stage
    original = {name: (root / name).read_bytes() for name in ["manifest.json", "cloud.env"]}
    write = pins._atomic_write
    failed = False

    def interrupted(path: Path, content: bytes):
        nonlocal failed
        if path.name == "manifest.json" and not failed:
            failed = True
            raise OSError("interrupted")
        write(path, content)

    monkeypatch.setattr(pins, "_atomic_write", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        pins.pin_release(root, backend_image="pskit-agent-backend:new", frontend_dist=dist)
    assert all((root / name).read_bytes() == content for name, content in original.items())
