"""Immutable artifact and overlay gates run without Docker or production data."""

import copy
import json
from pathlib import Path

import pytest

from deploy.agent.scripts.release_manifest import dist_hash, load_manifest


def candidate() -> dict:
    return {
        "schema_version": "pskit.release.v1", "source_sha": "a" * 40,
        "release_id": "local-test-only",
        "backend": {"reference": "pskit-agent-backend@sha256:" + "b" * 64},
        "frontend": {"sha256": "c" * 64}, "database_schema_version": 13,
        "pi_version": "0.87.1", "litellm_version": "1.100.3",
        "compatibility_sha256": "d" * 64,
        "opensandbox": {"image_reference": "sandbox@sha256:" + "b" * 64,
                        "capability_sha256": "c" * 64},
        "compose": {"project": "pskit-agent-cloud", "workspace_provider": "opensandbox",
                    "resolved_sha256": "e" * 64,
                    "files": ["deploy/agent/compose.yaml", "deploy/agent/compose.cloud.yaml",
                              "deploy/agent/compose.postgres.yaml",
                              "deploy/agent/compose.opensandbox.yaml",
                              "deploy/agent/compose.opensandbox.production.yaml"]},
        "checks": {key: {"status": "passed", "evidence_sha256": "f" * 64}
                   for key in ("ci", "preflight", "smoke")},
    }


def write(tmp_path: Path, value: dict) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(value))
    return path


def test_qualified_manifest_uses_immutable_full_overlay(tmp_path):
    value = candidate()
    assert load_manifest(write(tmp_path, value), require_qualified=True) == value


def test_manifest_preserves_optional_admission_qualification(tmp_path):
    value = candidate()
    value["admission"] = {
        "component_version": 1,
        "enforcement_version": "pskit.db-admission.v1",
        "qualification_sha256": "a" * 64,
    }
    assert load_manifest(write(tmp_path, value), require_qualified=True) == value


@pytest.mark.parametrize("admission", [
    None, {},
    {"component_version": 1, "enforcement_version": "pskit.db-admission.v1"},
    {"component_version": 0, "enforcement_version": "pskit.db-admission.v1",
     "qualification_sha256": "a" * 64},
    {"component_version": True, "enforcement_version": "pskit.db-admission.v1",
     "qualification_sha256": "a" * 64},
    {"component_version": "1", "enforcement_version": "pskit.db-admission.v1",
     "qualification_sha256": "a" * 64},
    {"component_version": 1, "enforcement_version": "unqualified-v2",
     "qualification_sha256": "a" * 64},
    {"component_version": 1, "enforcement_version": "pskit.db-admission.v1",
     "qualification_sha256": "PLACEHOLDER"},
    {"component_version": 1, "enforcement_version": "pskit.db-admission.v1",
     "qualification_sha256": "a" * 64, "api_key": "forbidden"},
])
def test_manifest_rejects_incomplete_or_unknown_admission_evidence(tmp_path, admission):
    value = candidate()
    value["admission"] = admission
    with pytest.raises(ValueError, match="pskit.release.v1"):
        load_manifest(write(tmp_path, value))


@pytest.mark.parametrize("mutation", [
    lambda m: m["backend"].update(reference="pskit-agent-backend:latest"),
    lambda m: m["checks"]["smoke"].update(evidence_sha256=None),
    lambda m: m["compose"]["files"].pop(),
    lambda m: m["compose"]["files"].append("deploy/agent/compose.a6000.yaml"),
    lambda m: m["compose"].update(workspace_provider="disabled"),
    lambda m: m.update(api_key="forbidden"),
    lambda m: m.update(opensandbox=None),
    lambda m: m["opensandbox"].update(image_reference="sandbox:latest"),
])
def test_manifest_refuses_mutability_missing_evidence_or_runtime(tmp_path, mutation):
    value = copy.deepcopy(candidate())
    mutation(value)
    with pytest.raises(ValueError):
        load_manifest(write(tmp_path, value), require_qualified=True)


def test_not_tested_is_truthful_but_not_releasable(tmp_path):
    value = candidate()
    value["checks"]["smoke"] = {"status": "not_tested", "evidence_sha256": None}
    path = write(tmp_path, value)
    assert load_manifest(path)["checks"]["smoke"]["status"] == "not_tested"
    with pytest.raises(ValueError, match="passing evidence"):
        load_manifest(path, require_qualified=True)


def test_dist_hash_tracks_names_and_content_and_refuses_symlinks(tmp_path):
    (tmp_path / "index.html").write_text("test")
    initial = dist_hash(tmp_path)
    (tmp_path / "asset.js").write_text("asset")
    assert dist_hash(tmp_path) != initial
    (tmp_path / "secret").symlink_to("/etc/passwd")
    with pytest.raises(ValueError, match="symlinks"):
        dist_hash(tmp_path)


def test_dist_hash_has_unambiguous_file_boundaries(tmp_path):
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    (one / "index.html").write_bytes(b"A" + b"z.js\0" + b"B")
    (two / "index.html").write_bytes(b"A")
    (two / "z.js").write_bytes(b"B")
    assert dist_hash(one) != dist_hash(two)
