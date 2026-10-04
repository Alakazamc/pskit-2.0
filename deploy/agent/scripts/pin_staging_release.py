"""Update an existing stopped staging environment's artifact pins, keeping its secrets."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import tempfile
from pathlib import Path

from deploy.agent.scripts.prepare_staging import _dist_hash, _image_id
from deploy.agent.scripts.staging_preflight import PROJECTS, _private


def _running_projects() -> list[str]:
    running = []
    for project in PROJECTS.values():
        result = subprocess.run(
            ["docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={project}"],
            check=True, capture_output=True, text=True,
        )
        if result.stdout.strip():
            running.append(project)
    return running


def _atomic_write(path: Path, content: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".release-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def pin_release(config_dir: Path, *, backend_image: str, frontend_dist: Path) -> Path:
    root = Path(config_dir).absolute()
    if root.is_symlink() or not root.is_dir() or root.stat().st_mode & 0o077:
        raise ValueError("Private staging directory is missing or unsafe")
    if ":" not in backend_image or backend_image.rsplit(":", 1)[-1] == "latest":
        raise ValueError("A fixed backend image is required")
    dist = Path(frontend_dist).resolve(strict=True)
    if not (dist / "index.html").is_file():
        raise ValueError("Frontend dist lacks index.html")
    lock = os.open(root / ".release.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock, "wb") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        paths = {name: root / name for name in ("cloud.env", "manifest.json")}
        for path in paths.values():
            _private(path)
        originals = {name: path.read_bytes() for name, path in paths.items()}
        release = json.loads(originals["manifest.json"])
        if release.get("projects") != list(PROJECTS.values()):
            raise ValueError("Unexpected staging projects")
        if _running_projects():
            raise ValueError("All staging projects must be stopped before pinning a release")
        image_id = _image_id(backend_image)
        digest = _dist_hash(dist)
        lines = originals["cloud.env"].decode().splitlines()
        if sum(line.startswith("AGENT_BACKEND_IMAGE=") for line in lines) != 1:
            raise ValueError("Staging backend image pin is missing or duplicated")
        cloud = "\n".join(
            f"AGENT_BACKEND_IMAGE={backend_image}" if line.startswith("AGENT_BACKEND_IMAGE=")
            else line for line in lines
        ) + "\n"
        release.update(backend_image=backend_image, backend_image_id=image_id,
                       frontend_dist=str(dist), frontend_dist_sha256=digest)
        backups = root / "release-backups"
        backups.mkdir(mode=0o700, exist_ok=True)
        if backups.is_symlink() or backups.stat().st_mode & 0o077:
            raise ValueError("Staging backup directory is unsafe")
        backup = Path(tempfile.mkdtemp(prefix="pins-", dir=backups))
        for name, content in originals.items():
            _atomic_write(backup / name, content)
        try:
            _atomic_write(paths["cloud.env"], cloud.encode())
            _atomic_write(paths["manifest.json"], (json.dumps(release, sort_keys=True) + "\n").encode())
        except BaseException:
            for name, content in originals.items():
                _atomic_write(paths[name], content)
            raise
    return backup


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", required=True, type=Path)
    parser.add_argument("--backend-image", required=True)
    parser.add_argument("--frontend-dist", required=True, type=Path)
    options = parser.parse_args()
    backup = pin_release(options.config_dir, backend_image=options.backend_image,
                         frontend_dist=options.frontend_dist)
    print(f"Staging artifact pins updated; previous pins: {backup}")


if __name__ == "__main__":
    main()
