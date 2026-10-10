"""Validate immutable, secret-free application release metadata."""

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = ROOT / "contracts/release-manifest.schema.json"


def load_manifest(path: Path, *, require_qualified: bool = False) -> dict:
    """Reject mutable references, malformed evidence and lost runtime overlays."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("A regular local release manifest is required")
    if path.stat().st_size > 64 * 1024:
        raise ValueError("Release manifest is too large")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))
    if next(validator.iter_errors(manifest), None) is not None:
        raise ValueError("Release manifest does not match pskit.release.v1")
    files = manifest["compose"]["files"]
    if files[0] != "deploy/agent/compose.yaml":
        raise ValueError("Base Compose must be first")
    environment = manifest["compose"]["project"].removeprefix("pskit-agent-")
    required = {
        "local": ["deploy/agent/compose.local.yaml"],
        "cloud": ["deploy/agent/compose.cloud.yaml", "deploy/agent/compose.postgres.yaml"],
        "staging": ["deploy/agent/compose.cloud.yaml", "deploy/agent/compose.postgres.yaml",
                    "deploy/agent/compose.staging.yaml"],
    }[environment]
    sandbox = "deploy/agent/compose.opensandbox.yaml"
    if manifest["compose"]["workspace_provider"] == "opensandbox":
        required.append(sandbox)
        if environment in {"cloud", "staging"}:
            required.append("deploy/agent/compose.opensandbox."
                            + ("production" if environment == "cloud" else "staging") + ".yaml")
    elif any("opensandbox" in name for name in files):
        raise ValueError("Runtime provider and Compose overlays disagree")
    # v1 deliberately permits only this known, ordered application combination.
    # Arbitrary files can introduce unrelated services or override safety controls.
    if files != ["deploy/agent/compose.yaml", *required]:
        raise ValueError("Missing, unknown or reordered application overlays")
    if require_qualified and any(check["status"] != "passed"
                                 for check in manifest["checks"].values()):
        raise ValueError("CI, preflight and smoke must have recorded passing evidence")
    return manifest


def dist_hash(directory: Path) -> str:
    """Hash an artifact tree deterministically; refuse symlinks and missing index."""
    if directory.is_symlink() or not directory.is_dir() or not (directory / "index.html").is_file():
        raise ValueError("A local frontend dist with index.html is required")
    digest = hashlib.sha256()
    for item in sorted(directory.rglob("*")):
        if item.is_symlink():
            raise ValueError("Frontend artifact cannot contain symlinks")
        if item.is_file():
            name = item.relative_to(directory).as_posix().encode("utf-8")
            digest.update(len(name).to_bytes(8, "big"))
            digest.update(name)
            with item.open("rb") as stream:
                digest.update(hashlib.file_digest(stream, "sha256").digest())
    return digest.hexdigest()
