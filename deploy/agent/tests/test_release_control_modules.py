"""Parity at the private release seams; all operations use synthetic host adapters."""

import importlib
import json

import pytest

from deploy.agent.scripts import release_control as rc
from deploy.agent.tests.test_release_control import setup as setup


def test_config_fingerprint_preserves_canonical_unicode_and_key_order():
    config = importlib.import_module("deploy.agent.scripts.release_config")
    expected = "4c0b316649cf06b9af63f868c781ba13af4a9763deebcd541b8ae536f813d947"
    assert config.fingerprint({"z": "é", "a": [3, 1]}) == expected
    assert rc.fingerprint({"a": [3, 1], "z": "é"}) == expected


def test_config_normalization_only_substitutes_backend_consumers():
    config = importlib.import_module("deploy.agent.scripts.release_config")
    model = {
        "services": {
            "backend": {"image": "backend-pin", "environment": {"KEY": "synthetic"}},
            "af3-callback-proxy": {"image": "backend-pin"},
            "database": {"image": "database-pin"},
        },
        "networks": {"app": {"internal": True}},
    }
    assert config.normalized(model, "backend-pin") == {
        "services": {
            "backend": {
                "image": "<unchanged-backend-artifact>",
                "environment": {"KEY": "synthetic"},
            },
            "af3-callback-proxy": {"image": "<unchanged-backend-artifact>"},
            "database": {"image": "database-pin"},
        },
        "networks": {"app": {"internal": True}},
    }
    assert model["services"]["backend"]["image"] == "backend-pin"
    with pytest.raises(rc.ReleaseError, match="infrastructure service"):
        config.normalized({"services": {"database": {"image": "backend-pin"}}}, "backend-pin")


def test_config_operations_keep_registered_compose_and_source_binds(setup):
    config = importlib.import_module("deploy.agent.scripts.release_config")
    controller, docker, root = setup
    assert isinstance(controller, config._ConfigOperations)
    manifest = controller._current_manifest("api")
    rendered = controller.render(manifest)
    assert rendered["name"] == "pskit-agent-cloud"
    assert rendered["services"]["backend"]["image"] == "sha256:" + "a" * 64
    assert rendered["services"]["backend"]["volumes"] == [{
        "type": "bind", "source": str(root / "backend.env"),
        "target": "/run/config", "read_only": True,
    }]
    docker.calls.clear()
    controller.doctor()
    assert all("up" not in call and "down" not in call for call in docker.calls)


@pytest.mark.parametrize("fault", ["none", "startup", "release-acknowledgement"])
def test_api_seam_preserves_owner_across_commit_rollback_and_recovery(setup, fault):
    api = importlib.import_module("deploy.agent.scripts.release_api")
    controller, docker, root = setup
    assert isinstance(controller, api._ApiOperations)
    before = (root / "cloud.env").read_bytes()
    if fault == "startup":
        docker.fail_up = True
        with pytest.raises(rc.ReleaseError, match="rolled back"):
            controller.release("new", "api")
    elif fault == "release-acknowledgement":
        docker.admission_fail.add("release")
        with pytest.raises(rc.ReleaseError, match="pending recovery"):
            controller.release("new", "api")
        pending = json.loads((root / "state/pending.json").read_text())
        assert pending["admission_phase"] == "releasing"
        assert docker.admission_closed is True
        assert controller.current()["api"]["release_id"] == "new"
        docker.admission_fail.clear()
        controller.release("old", "api", rollback=True)
    else:
        controller.release("new", "api")

    expected_release = "new" if fault == "none" else "old"
    assert controller.current()["api"]["release_id"] == expected_release
    assert controller.current()["web"]["release_id"] == "old"
    assert docker.admission_closed is False
    assert not (root / "state/pending.json").exists()
    assert (root / "cloud.env").read_bytes() == (
        before.replace(("sha256:" + "a" * 64).encode(), ("sha256:" + "b" * 64).encode())
        if fault == "none" else before
    )
    requests = [request for _operation, request in docker.admission_calls]
    owners = {request["owner_nonce"] for request in requests}
    assert len(owners) == 1
    assert len(next(iter(owners))) == 64
    assert {request["environment"] for request in requests} == {"prod"}
    assert {request["project"] for request in requests} == {"pskit-agent-cloud"}
    assert all(request["scopes"] == [
        "agent_runs", "compute_jobs", "workspace_attempts", "scheduler_claims",
    ] for request in requests)
    assert all("down" not in call and "run" not in call for call in docker.calls)
    assert all(call[-1] == "backend" for call in docker.calls if "up" in call)


@pytest.mark.parametrize("fault", ["none", "interruption", "pending-recovery"])
def test_web_seam_preserves_assets_api_and_atomic_recovery(setup, fault):
    web = importlib.import_module("deploy.agent.scripts.release_web")
    controller, docker, root = setup
    assert isinstance(controller, web._WebOperations)
    before_state = controller.current()
    before_cloud = (root / "cloud.env").read_bytes()
    old_index = (root / "static/index.html").read_bytes()
    if fault == "interruption":
        original_http = controller.http

        def interrupt_after_switch(url):
            if url.endswith("/login"):
                raise KeyboardInterrupt
            return original_http(url)

        controller.http = interrupt_after_switch
        with pytest.raises(KeyboardInterrupt):
            controller.release("new", "web")
        controller.http = original_http
    else:
        controller.release("new", "web")
        if fault == "pending-recovery":
            pending = {
                "component": "web", "previous": before_state["web"],
                "target": "new", "target_reference": controller.current()["web"],
                "cloud_before": before_cloud.decode(), "created_at": 123.0,
            }
            marker = root / "state/pending.json"
            marker.write_text(json.dumps(pending))
            marker.chmod(0o600)
            controller.release("old", "web", rollback=True)

    expected_release = "new" if fault == "none" else "old"
    assert controller.current()["web"]["release_id"] == expected_release
    assert controller.current()["api"] == before_state["api"]
    assert (root / "cloud.env").read_bytes() == before_cloud
    assert (root / "static/assets/old-hash.js").read_text() == "old script"
    assert (root / "static/assets/new-hash.js").read_text() == "new script"
    assert (root / "static/index.html").read_bytes() == (
        b'<script src="/assets/new-hash.js"></script>' if fault == "none" else old_index
    )
    assert not (root / "state/pending.json").exists()
    assert docker.admission_calls == []
    assert all("up" not in call and "down" not in call for call in docker.calls)
