"""Private configuration safety and actual-backend proof failure behavior."""

import json
import sqlite3
from io import BytesIO
from types import SimpleNamespace

import pytest
from app.db.release_admission_migrations import migrate_admission_sqlite
from app.domain.release_admission import DurableAdmissionGate

from deploy.agent.scripts import admission_adapter as adapter


def identity():
    return {"adapter_id": "synthetic-adapter", "qualification_sha256": "a" * 64,
            "owner_nonce": "b" * 64, "environment": "staging", "project": "pskit-agent-staging",
            "previous_manifest_sha256": "c" * 64, "target_manifest_sha256": "d" * 64,
            "scopes": ["agent_runs", "compute_jobs", "workspace_attempts", "scheduler_claims"]}


def configuration():
    return {"schema_version": "pskit.admission-adapter.v1", "database_url": "postgresql://synthetic:private@127.0.0.1/isolated",
            "database_schema": "isolated", "backend_url": "http://127.0.0.1:18090",
            "backend_token": "private-observation-" + "k" * 40,
            **{key: value for key, value in identity().items()
               if key in {"adapter_id", "qualification_sha256", "environment", "project"}}}


def config_file(tmp_path, config=None):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    path = directory / "admission.json"
    path.write_text(json.dumps(config or configuration()))
    path.chmod(0o600)
    return path


def make_gate():
    connection = sqlite3.connect(":memory:")
    migrate_admission_sqlite(connection)
    gate = DurableAdmissionGate(connection, environment="staging", project="pskit-agent-staging",
                                qualification_sha256="a" * 64)
    gate.configure()
    return connection, gate


def test_private_config_accepts_only_owned_regular_pinned_file(tmp_path):
    path = config_file(tmp_path)
    assert adapter.read_config(path) == configuration()


@pytest.mark.parametrize("location,mode", [("file", 0o644), ("file", 0o400), ("directory", 0o755)])
def test_private_config_refuses_unsafe_modes(tmp_path, location, mode):
    path = config_file(tmp_path)
    (path if location == "file" else path.parent).chmod(mode)
    with pytest.raises(adapter.AdapterError):
        adapter.read_config(path)


@pytest.mark.parametrize("which", ["file", "parent", "ancestor"])
def test_private_config_refuses_symlinks_in_every_path_component(tmp_path, which):
    path = config_file(tmp_path)
    if which == "file":
        target = path.with_name("target.json")
        path.rename(target)
        path.symlink_to(target)
    elif which == "parent":
        target = path.parent.with_name("target")
        path.parent.rename(target)
        path.parent.symlink_to(target, target_is_directory=True)
    else:
        target = tmp_path / "ancestor"
        target.symlink_to(path.parent, target_is_directory=True)
        path = target / path.name
    with pytest.raises(OSError):
        adapter.read_config(path)


@pytest.mark.parametrize("content", ['{"key":1,"key":2}', '{"key":NaN}', '{"key":Infinity}', '[]'])
def test_private_config_rejects_ambiguous_json(tmp_path, content):
    path = config_file(tmp_path)
    path.write_text(content)
    with pytest.raises((adapter.AdapterError, ValueError)):
        adapter.read_config(path)


@pytest.mark.parametrize("url", ["https://public.example:443", "http://10.9.8.1:4000", "http://127.0.0.1:80?secret=x",
                                  "http://user:password@127.0.0.1:80", "http://127.0.0.1:80/arbitrary"])
def test_private_proof_has_no_public_proxy_or_arbitrary_path(tmp_path, url):
    path = config_file(tmp_path, {**configuration(), "backend_url": url})
    with pytest.raises((adapter.AdapterError, ValueError)):
        adapter.read_config(path)


@pytest.mark.parametrize("field,value", [("enabled", False), ("component_version", True),
    ("enforcement_version", "unqualified"), ("deployment_id", "f" * 32), ("generation", 9),
    ("qualification_sha256", "f" * 64), ("state", "closed"), ("observed_at", -1), ("challenge", "0" * 64)])
def test_unverified_actual_backend_never_closes_admission(field, value):
    connection, gate = make_gate()

    def fetch(_config, challenge):
        return {**gate.observe(challenge), field: value}

    before = connection.execute("SELECT * FROM release_admission_state").fetchall()
    with pytest.raises(adapter.AdapterError):
        adapter.execute("enter", configuration(), identity(), gate, fetch=fetch)
    assert connection.execute("SELECT * FROM release_admission_state").fetchall() == before
    connection.close()


def test_lost_backend_proof_after_enter_retains_durable_closed_owner():
    connection, gate = make_gate()
    observations = 0

    def fetch(_config, challenge):
        nonlocal observations
        observations += 1
        proof = gate.observe(challenge)
        if observations == 2:
            proof["enabled"] = False
        return proof

    with pytest.raises(adapter.AdapterError):
        adapter.execute("enter", configuration(), identity(), gate, fetch=fetch)
    assert gate.verify(identity())["state"] == "closed"
    connection.close()


def test_lost_backend_proof_after_release_recloses_durable_owner():
    connection, gate = make_gate()
    gate.enter(identity())
    observations = 0

    def fetch(_config, challenge):
        nonlocal observations
        observations += 1
        proof = gate.observe(challenge)
        if observations == 2:
            proof["enabled"] = False
        return proof

    with pytest.raises(adapter.AdapterError):
        adapter.execute("release", configuration(), identity(), gate, fetch=fetch)
    assert gate.verify(identity())["state"] == "closed"
    connection.close()


def test_receipt_is_bound_to_every_identity_and_explicit_release():
    connection, gate = make_gate()
    fetch = lambda _config, challenge: gate.observe(challenge)
    for operation in ("enter", "enter", "verify", "release", "release"):
        receipt = adapter.execute(operation, configuration(), identity(), gate, fetch=fetch)
        assert receipt["state"] == ("released" if operation == "release" else "closed")
        assert all(receipt[key] == value for key, value in identity().items())
    connection.close()


def test_cli_failures_never_print_private_values(tmp_path, monkeypatch, capsys):
    secret = "should-never-appear-private-secret"
    path = config_file(tmp_path, {**configuration(), "backend_token": secret, "unknown": secret})
    monkeypatch.setattr(adapter.sys, "stdin", SimpleNamespace(buffer=BytesIO(secret.encode())))
    assert adapter.main(["enter", "--config", str(path)]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert secret not in output.err
    assert "pending recovery marker" in output.err
