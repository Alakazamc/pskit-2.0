"""Daily release fault injection uses only fake Docker and temporary artifacts."""

import copy
import hashlib
import json
import time

import pytest

from deploy.agent.scripts import release_control as rc
from deploy.agent.scripts.release_manifest import dist_hash


def private(path, content):
    path.write_text(content)
    path.chmod(0o600)
    return path


def admission_config():
    return {
        "adapter_id": "synthetic-gate",
        "qualification_sha256": "f" * 64,
        "enter_command": ["/synthetic/admission", "enter"],
        "verify_command": ["/synthetic/admission", "verify"],
        "release_command": ["/synthetic/admission", "release"],
    }


class Docker:
    def __init__(self, root):
        self.root = root
        self.calls = []
        self.image = "sha256:" + "a" * 64
        self.project = "pskit-agent-cloud"
        self.files = []
        self.activity = {
            "schema_versions": list(range(1, 14)),
            "auth_grants_safe": True,
            "counts": {key: 0 for key in rc.ACTIVITY_KEYS},
        }
        self.fail_up = False
        self.render_mutation = lambda model: None
        self.inspect_mutation = lambda info: None
        self.admission_calls = []
        self.admission_closed = False
        self.admission_owner = None
        self.admission_mutation = lambda operation, receipt: None
        self.admission_fail = set()

    def __call__(self, command, *, env, input_text=None):
        self.calls.append(command)
        if command[0] == "/synthetic/admission":
            operation = command[1]
            request = json.loads(input_text)
            self.admission_calls.append((operation, request))
            assert set(env) <= {"PATH", "HOME"}
            assert request["operation"] == operation
            if operation == "enter":
                assert (
                    not self.admission_closed
                    or self.admission_owner == request["owner_nonce"]
                )
                self.admission_closed = True
                self.admission_owner = request["owner_nonce"]
            assert self.admission_owner == request["owner_nonce"]
            if operation in self.admission_fail:
                raise rc.ReleaseError("Synthetic admission command unavailable")
            if operation == "release":
                assert self.admission_closed
                self.admission_closed = False
            receipt = {
                key: value
                for key, value in request.items()
                if key not in {"schema_version", "operation"}
            }
            receipt.update(
                schema_version="pskit.admission-receipt.v1",
                state="closed" if self.admission_closed else "released",
                hold_mode="until_explicit_release",
                observed_at=time.time(),
                valid_until=time.time() + 20,
            )
            self.admission_mutation(operation, receipt)
            return json.dumps(receipt)
        if "config" in command:
            self.project = command[command.index("-p") + 1]
            self.files = [
                command[index + 1] for index, item in enumerate(command) if item == "-f"
            ]
            model = {
                "name": self.project,
                "services": {
                    "backend": {
                        "image": env["AGENT_BACKEND_IMAGE"],
                        "environment": {
                            "MODEL_GATEWAY_API_KEY": "synthetic-private-only"
                        },
                        "networks": {"app": {}},
                        "volumes": [
                            {
                                "type": "bind",
                                "source": str(self.root / "backend.env"),
                                "target": "/run/config",
                                "read_only": True,
                            }
                        ],
                    },
                    "af3-callback-proxy": {"image": env["AGENT_BACKEND_IMAGE"]},
                },
                "networks": {"app": {"internal": True}},
                "volumes": {"agent_data": {"name": "synthetic-volume"}},
            }
            self.render_mutation(model)
            return json.dumps(model)
        if command[:3] == ["docker", "image", "inspect"]:
            return json.dumps([{"Id": command[3], "RepoDigests": [command[3]]}])
        if command[:2] == ["docker", "inspect"]:
            info = [
                {
                    "Image": self.image,
                    "Config": {
                        "Env": ["MODEL_GATEWAY_API_KEY=synthetic-private-only"],
                        "Labels": {
                            "com.docker.compose.project": self.project,
                            "com.docker.compose.service": "backend",
                            "com.docker.compose.project.config_files": ",".join(
                                self.files
                            ),
                        },
                    },
                    "Mounts": [
                        {
                            "Type": "bind",
                            "Source": str(self.root / "backend.env"),
                            "Destination": "/run/config",
                            "RW": False,
                        }
                    ],
                    "NetworkSettings": {"Networks": {self.project + "_app": {}}},
                }
            ]
            self.inspect_mutation(info)
            return json.dumps(info)
        if "exec" in command:
            assert "default_transaction_read_only" in input_text
            return json.dumps(self.activity)
        if "up" in command:
            assert (
                command[-6:]
                == [
                    "up",
                    "-d",
                    "--no-deps",
                    "--wait",
                    "--wait-timeout",
                    "60",
                    "backend",
                ][-6:]
            )
            self.image = env["AGENT_BACKEND_IMAGE"]
            if self.fail_up:
                self.fail_up = False
                raise rc.ReleaseError("Fake Docker startup failed")
            return ""
        if "ps" in command:
            return "synthetic-backend-id\n"
        if command[0] == "synthetic-critical-smoke":
            return "synthetic smoke passed"
        if "logs" in command:
            return "synthetic log"
        raise AssertionError(command)


@pytest.fixture
def setup(tmp_path):
    releases, state, static = [
        tmp_path / name for name in ("releases", "state", "static")
    ]
    for path in (releases, state, static):
        path.mkdir(mode=0o700)
    backend = private(
        tmp_path / "backend.env",
        "MODEL_GATEWAY_API_KEY=synthetic-private-only\n"
        "RESEARCH_AGENT_DATABASE_URL=postgresql://pskit_app:synthetic@db/postgres\n"
        "RESEARCH_AGENT_AUTH_ABUSE_MODE=enforce\n"
        "RESEARCH_AGENT_AUTH_RATE_LIMIT_SECRET=" + "r" * 40 + "\n"
        "RESEARCH_AGENT_AUTH_CSRF_SECRET=" + "s" * 40 + "\n"
        'RESEARCH_AGENT_AUTH_TRUSTED_PROXY_CIDRS_JSON=["127.0.0.1/32"]\n'
        "RESEARCH_AGENT_AUTH_CAPTCHA_REQUIRED=true\n"
        "TURNSTILE_SECRET_KEY=synthetic-production-secret\n"
        'TURNSTILE_HOSTNAMES_JSON=["release.example.invalid"]\n',
    )
    cloud = private(
        tmp_path / "cloud.env",
        "# keep exact secret formatting\n"
        "AGENT_BACKEND_IMAGE=sha256:" + "a" * 64 + "\n"
        "AGENT_BACKEND_ENV_FILE=" + str(backend) + "\n"
        "AGENT_WEB_IMAGE=unused\nAGENT_PUBLIC_URL=https://release.example.invalid\n"
        "TURNSTILE_SITE_KEY=synthetic-production-site\n"
        "UNRELATED_SECRET='keep#this' # retained comment\n",
    )
    config = private(
        tmp_path / "control.json",
        json.dumps(
            {
                "schema_version": "pskit.control.v1",
                "environment": "prod",
                "repository": str(rc.ROOT),
                "cloud_env_file": str(cloud),
                "backend_env_file": str(backend),
                "release_directory": str(releases),
                "state_directory": str(state),
                "static_directory": str(static),
                "backend_url": "http://127.0.0.1:18088",
                "public_url": "https://release.example.invalid",
                "critical_smoke_command": ["synthetic-critical-smoke"],
                "receiver_activity_urls": ["http://127.0.0.1:19001/activity"],
                "receiver_activity_required": True,
            }
        ),
    )
    docker = Docker(tmp_path)

    def http(url):
        if url.endswith("/activity"):
            return 200, json.dumps(
                {
                    "schema_version": "pskit.receiver-activity.v1",
                    "observed_at": time.time(),
                    "counts": {
                        "active": 0,
                        "unknown": 0,
                        "journal_pending": 0,
                        "outbox_pending": 0,
                    },
                }
            ).encode()
        return (
            401
            if url.endswith("/me/usage")
            else 404
            if url.endswith("/internal/release-probe")
            else 403
            if url.endswith("/admin")
            else 200,
            b"{}",
        )

    controller = rc.Controller(config, "prod", runner=docker, http=http)
    for name, letter in (("old", "a"), ("new", "b")):
        directory = releases / name
        directory.mkdir()
        dist = directory / "dist"
        dist.mkdir()
        (dist / "assets").mkdir()
        (dist / "assets" / f"{name}-hash.js").write_text(name + " script")
        (dist / "index.html").write_text(
            f'<script src="/assets/{name}-hash.js"></script>'
        )
        manifest = {
            "schema_version": "pskit.release.v1",
            "release_id": name,
            "source_sha": letter * 40,
            "backend": {"reference": "sha256:" + letter * 64},
            "frontend": {"sha256": dist_hash(dist)},
            "database_schema_version": 13,
            "pi_version": "0.87.1",
            "litellm_version": "1.100.3",
            "compatibility_sha256": "c" * 64,
            "opensandbox": None,
            "compose": {
                "project": "pskit-agent-cloud",
                "workspace_provider": "disabled",
                "files": [
                    "deploy/agent/compose.yaml",
                    "deploy/agent/compose.cloud.yaml",
                    "deploy/agent/compose.postgres.yaml",
                ],
                "resolved_sha256": "0" * 64,
            },
            "checks": {
                key: {"status": "passed", "evidence_sha256": "e" * 64}
                for key in ("ci", "preflight", "smoke")
            },
        }
        manifest["compose"]["resolved_sha256"] = rc.fingerprint(
            controller.render(manifest)
        )
        (directory / "manifest.json").write_text(json.dumps(manifest))
    old_path = releases / "old" / "manifest.json"
    reference = {
        "release_id": "old",
        "manifest_sha256": hashlib.sha256(old_path.read_bytes()).hexdigest(),
    }
    private(
        state / "current.json",
        json.dumps(
            {"schema_version": "pskit.control.v1", "api": reference, "web": reference}
        ),
    )
    for file in (releases / "old" / "dist").rglob("*"):
        if file.is_file():
            destination = static / file.relative_to(releases / "old" / "dist")
            destination.parent.mkdir(exist_ok=True)
            destination.write_bytes(file.read_bytes())
    docker.calls.clear()
    # A fake qualified adapter exercises the controller protocol only; not host qualification.
    controller.config["admission_barrier"] = admission_config()
    return controller, docker, tmp_path


def test_api_updates_only_backend_and_preserves_secret_bytes(setup):
    controller, docker, root = setup
    before = (root / "cloud.env").read_text()
    controller.release("new", "api")
    after = (root / "cloud.env").read_text()
    assert after == before.replace("sha256:" + "a" * 64, "sha256:" + "b" * 64)
    ups = [call for call in docker.calls if "up" in call]
    assert len(ups) == 1 and ups[0][-1] == "backend"
    assert all("down" not in call and "run" not in call for call in docker.calls)
    assert controller.current()["api"]["release_id"] == "new"
    assert controller.current()["web"]["release_id"] == "old"


def test_502_readiness_rolls_back_and_returns_failure(setup):
    controller, docker, root = setup
    before = (root / "cloud.env").read_bytes()
    original_http = controller.http
    controller.http = lambda url: (
        (502, b"upstream failed")
        if (url.endswith("/health/ready") and docker.image.endswith("b" * 64))
        else original_http(url)
    )
    with pytest.raises(rc.ReleaseError, match="rolled back"):
        controller.release("new", "api")
    assert docker.image == "sha256:" + "a" * 64
    assert (root / "cloud.env").read_bytes() == before
    assert controller.current()["api"]["release_id"] == "old"


@pytest.mark.parametrize(
    "key",
    [
        "runs_active",
        "runs_unknown",
        "jobs_active",
        "jobs_unknown",
        "attempts_unknown",
        "workspace_leases",
        "compute_outbox",
        "jobs_unreconciled",
        "model_calls_unresolved",
        "compute_reservations",
    ],
)
def test_activity_fails_closed_before_mutation(setup, key):
    controller, docker, root = setup
    before = (root / "cloud.env").read_bytes()
    docker.activity["counts"][key] = 1
    with pytest.raises(rc.ReleaseError, match="activity"):
        controller.release("new", "api")
    assert (root / "cloud.env").read_bytes() == before
    assert not any("up" in call for call in docker.calls)


def test_missing_key_fails_before_docker(setup):
    controller, docker, root = setup
    backend = root / "backend.env"
    backend.write_text(
        backend.read_text().replace(
            "TURNSTILE_SECRET_KEY=synthetic-production-secret", "TURNSTILE_SECRET_KEY="
        )
    )
    with pytest.raises(ValueError, match="TURNSTILE_SECRET_KEY"):
        controller.release("new", "api")
    assert docker.calls == []


def test_stale_manifest_refused(setup):
    controller, docker, root = setup
    path = root / "releases" / "new" / "manifest.json"
    data = json.loads(path.read_text())
    data["compose"]["resolved_sha256"] = "f" * 64
    path.write_text(json.dumps(data))
    with pytest.raises(rc.ReleaseError, match="fingerprint"):
        controller.release("new", "api")
    assert not any("up" in call for call in docker.calls)


def test_lost_overlay_is_rejected_by_single_manifest_schema(setup):
    controller, docker, root = setup
    path = root / "releases" / "new" / "manifest.json"
    data = json.loads(path.read_text())
    data["compose"]["files"].pop()
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="overlays"):
        controller.release("new", "api")
    assert not any("up" in call for call in docker.calls)


def test_web_keeps_old_assets_and_rollback_is_atomic(setup):
    controller, docker, root = setup
    controller.release("new", "web")
    static = root / "static"
    assert (static / "assets/old-hash.js").exists()
    assert (static / "assets/new-hash.js").exists()
    assert "new-hash" in (static / "index.html").read_text()
    controller.release("old", "web", rollback=True)
    assert "old-hash" in (static / "index.html").read_text()
    assert (static / "assets/new-hash.js").exists()
    assert not any("up" in call for call in docker.calls)


def test_web_interruption_restores_old_index(setup, monkeypatch):
    controller, _docker, root = setup
    original = controller._postflight

    def interrupt():
        assert "new-hash" in (root / "static/index.html").read_text()
        raise KeyboardInterrupt

    monkeypatch.setattr(controller, "_postflight", interrupt)
    with pytest.raises(KeyboardInterrupt):
        controller.release("new", "web")
    assert "old-hash" in (root / "static/index.html").read_text()
    assert controller.current()["web"]["release_id"] == "old"
    monkeypatch.setattr(controller, "_postflight", original)
    controller.release("new", "web")


def test_web_collision_and_symlink_refused_before_index(setup):
    controller, _docker, root = setup
    private(root / "static/assets/new-hash.js", "different bytes")
    with pytest.raises(rc.ReleaseError, match="collision"):
        controller.release("new", "web")
    assert "old-hash" in (root / "static/index.html").read_text()
    (root / "static/assets/new-hash.js").unlink()
    (root / "static/assets/new-hash.js").symlink_to(root / "backend.env")
    with pytest.raises((rc.ReleaseError, OSError)):
        controller.release("new", "web")
    assert "old-hash" in (root / "static/index.html").read_text()


def test_schema_downgrade_and_unsafe_grants_refused(setup):
    controller, docker, _root = setup
    docker.activity["schema_versions"].append(14)
    with pytest.raises(rc.ReleaseError, match="schema"):
        controller.release("new", "api")
    docker.activity["schema_versions"].pop()
    docker.activity["auth_grants_safe"] = False
    with pytest.raises(rc.ReleaseError, match="auth"):
        controller.release("new", "api")
    assert not any("up" in call for call in docker.calls)


def test_readonly_commands_never_write_or_restart(setup):
    controller, docker, root = setup
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    controller.doctor()
    controller.status()
    controller.logs("backend")
    assert {
        path: path.read_bytes() for path in root.rglob("*") if path.is_file()
    } == before
    assert not any("up" in call or "down" in call for call in docker.calls)


def test_startup_failure_restores_old_application(setup):
    controller, docker, root = setup
    before = (root / "cloud.env").read_bytes()
    docker.fail_up = True
    with pytest.raises(rc.ReleaseError, match="rolled back"):
        controller.release("new", "api")
    assert docker.image.endswith("a" * 64)
    assert (root / "cloud.env").read_bytes() == before
    assert not (root / "state/pending.json").exists()


def test_critical_smoke_failure_is_not_a_warning(setup):
    controller, docker, _root = setup
    original = controller.runner

    def fail_new(command, **kwargs):
        if command[0] == "synthetic-critical-smoke" and docker.image.endswith("b" * 64):
            raise rc.ReleaseError("Critical Pi/SSE/Artifact smoke failed")
        return original(command, **kwargs)

    controller.runner = fail_new
    with pytest.raises(rc.ReleaseError, match="rolled back"):
        controller.release("new", "api")
    assert docker.image.endswith("a" * 64)


def test_unknown_count_missing_is_not_zero(setup):
    controller, docker, _root = setup
    del docker.activity["counts"]["attempts_unknown"]
    with pytest.raises(rc.ReleaseError, match="activity"):
        controller.release("new", "api")
    assert not any("up" in call for call in docker.calls)


def test_receiver_evidence_stale_missing_or_unknown_blocks_api(setup):
    controller, docker, _root = setup
    for body in (
        {},
        {
            "schema_version": "pskit.receiver-activity.v1",
            "observed_at": time.time() - 60,
            "counts": {
                "active": 0,
                "unknown": 0,
                "journal_pending": 0,
                "outbox_pending": 0,
            },
        },
        {
            "schema_version": "pskit.receiver-activity.v1",
            "observed_at": time.time(),
            "counts": {
                "active": 0,
                "unknown": 1,
                "journal_pending": 0,
                "outbox_pending": 0,
            },
        },
    ):
        original = controller.http
        controller.http = lambda url, body=body, original=original: (
            (200, json.dumps(body).encode())
            if url.endswith("/activity")
            else original(url)
        )
        with pytest.raises(rc.ReleaseError, match="Receiver|Receiver activity"):
            controller.release("new", "api")
    assert not any("up" in call for call in docker.calls)


def test_changed_source_bind_refused_even_if_candidate_hash_updated(setup):
    controller, docker, root = setup

    def mutate(model):
        if model["services"]["backend"]["image"].endswith("b" * 64):
            model["services"]["backend"]["volumes"][0]["source"] = "/unexpected/source"

    docker.render_mutation = mutate
    path = root / "releases/new/manifest.json"
    data = json.loads(path.read_text())
    data["compose"]["resolved_sha256"] = rc.fingerprint(controller.render(data))
    path.write_text(json.dumps(data))
    with pytest.raises(rc.ReleaseError, match="source binds"):
        controller.release("new", "api")
    assert not any("up" in call for call in docker.calls)


def test_unqualified_release_never_updates(setup):
    controller, docker, root = setup
    path = root / "releases/new/manifest.json"
    data = json.loads(path.read_text())
    data["checks"]["smoke"] = {"status": "not_tested", "evidence_sha256": None}
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="passing evidence"):
        controller.release("new", "web")
    assert not any("up" in call for call in docker.calls)


def test_artifact_snapshot_mutation_does_not_publish(setup):
    controller, _docker, root = setup
    path = root / "releases/new/dist/assets/new-hash.js"
    path.write_text("tampered artifact")
    with pytest.raises(rc.ReleaseError, match="fingerprint"):
        controller.release("new", "web")
    assert "old-hash" in (root / "static/index.html").read_text()


def test_parent_symlink_cannot_redirect_static_write(setup):
    controller, _docker, root = setup
    (root / "static/assets/old-hash.js").unlink()
    (root / "static/assets").rmdir()
    outside = root / "outside"
    outside.mkdir()
    (root / "static/assets").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        controller.release("new", "web")
    assert not list(outside.iterdir())
    assert "old-hash" in (root / "static/index.html").read_text()


def test_api_explicit_rollback_retains_state_and_full_overlays(setup):
    controller, docker, root = setup
    controller.release("new", "api")
    controller.release("old", "api", rollback=True)
    assert controller.current()["api"]["release_id"] == "old"
    ups = [call for call in docker.calls if "up" in call]
    for call in ups:
        assert call.count("-f") == 3
        assert call[call.index("-p") + 1] == "pskit-agent-cloud"
        assert call[-1] == "backend"
        assert "--no-deps" in call
    assert docker.image.endswith("a" * 64)
    assert (root / "cloud.env").stat().st_mode & 0o077 == 0


def test_cli_refuses_executor_bootstrap_and_missing_explicit_component(setup, capsys):
    controller, _docker, root = setup
    for extra in (
        ["bootstrap"],
        ["migrate"],
        ["release", "--version", "new"],
        ["release", "--version", "new", "--component", "executor"],
    ):
        assert (
            rc.main([*extra, "--env", "prod", "--config", str(root / "control.json")])
            == 2
        )
    assert "synthetic-private-only" not in capsys.readouterr().err
    assert controller.current()["api"]["release_id"] == "old"


def test_api_interruption_restores_old_image(setup):
    controller, docker, root = setup
    before = (root / "cloud.env").read_bytes()
    original = controller.runner

    def interrupt(command, **kwargs):
        result = original(command, **kwargs)
        if "up" in command and docker.image.endswith("b" * 64):
            raise KeyboardInterrupt
        return result

    controller.runner = interrupt
    with pytest.raises(KeyboardInterrupt):
        controller.release("new", "api")
    assert docker.image.endswith("a" * 64)
    assert (root / "cloud.env").read_bytes() == before


def test_rollback_failure_keeps_private_recovery_marker(setup):
    controller, _docker, root = setup
    controller._postflight = lambda: (_ for _ in ()).throw(
        rc.ReleaseError("smoke failed")
    )
    with pytest.raises(rc.ReleaseError, match="pending recovery"):
        controller.release("new", "api")
    path = root / "state/pending.json"
    assert path.exists() and path.stat().st_mode & 0o077 == 0
    with pytest.raises(rc.ReleaseError, match="Interrupted release"):
        controller.release("new", "api")


def test_killed_after_current_pointer_commit_can_recover_old_web(setup):
    controller, _docker, root = setup
    before_state = controller.current()
    _target, digest = controller.artifact("new")
    new_state = copy.deepcopy(before_state)
    new_state["web"] = {"release_id": "new", "manifest_sha256": digest}
    private(root / "state/current.json", json.dumps(new_state))
    private(
        root / "state/pending.json",
        json.dumps(
            {
                "component": "web",
                "previous": before_state["web"],
                "target": "new",
                "target_reference": new_state["web"],
                "cloud_before": (root / "cloud.env").read_text(),
                "created_at": time.time(),
            }
        ),
    )
    (root / "static/index.html").write_bytes(
        (root / "releases/new/dist/index.html").read_bytes()
    )
    controller.release("old", "web", rollback=True)
    assert controller.current()["web"] == before_state["web"]
    assert "old-hash" in (root / "static/index.html").read_text()
    assert not (root / "state/pending.json").exists()


def test_running_backend_lost_runtime_overlay_refused(setup):
    controller, docker, _root = setup
    docker.inspect_mutation = lambda info: info[0]["Config"]["Labels"].update(
        {
            "com.docker.compose.project.config_files": str(
                rc.ROOT / "deploy/agent/compose.yaml"
            )
        }
    )
    with pytest.raises(rc.ReleaseError, match="overlays"):
        controller.release("new", "api")
    assert not any("up" in call for call in docker.calls)


def test_running_source_bind_drift_refused(setup):
    controller, docker, _root = setup
    docker.inspect_mutation = lambda info: info[0]["Mounts"][0].update(
        Source="/unexpected"
    )
    with pytest.raises(rc.ReleaseError, match="mount"):
        controller.release("new", "api")
    assert not any("up" in call for call in docker.calls)


def test_logs_redact_private_configuration(setup):
    controller, _docker, _root = setup
    original = controller.runner
    controller.runner = lambda command, **kwargs: (
        "key=synthetic-private-only postgresql://pskit_app:synthetic@db/postgres"
        if "logs" in command
        else original(command, **kwargs)
    )
    output = controller.logs("backend")
    assert "synthetic-private-only" not in output
    assert "postgresql://pskit_app:synthetic@db/postgres" not in output
    assert "[REDACTED]" in output


def test_full_opensandbox_overlays_remain_on_api_release(setup):
    controller, docker, root = setup
    files = [
        "deploy/agent/compose.yaml",
        "deploy/agent/compose.cloud.yaml",
        "deploy/agent/compose.postgres.yaml",
        "deploy/agent/compose.opensandbox.yaml",
        "deploy/agent/compose.opensandbox.production.yaml",
    ]
    for name in ("old", "new"):
        path = root / "releases" / name / "manifest.json"
        data = json.loads(path.read_text())
        data["compose"].update(files=files, workspace_provider="opensandbox")
        data["opensandbox"] = {
            "image_reference": "sandbox@sha256:" + "d" * 64,
            "capability_sha256": "e" * 64,
        }
        path.write_text(json.dumps(data))
    state = controller.current()
    state["api"]["manifest_sha256"] = hashlib.sha256(
        (root / "releases/old/manifest.json").read_bytes()
    ).hexdigest()
    state["web"] = state["api"]
    private(root / "state/current.json", json.dumps(state))
    docker.inspect_mutation = lambda info: info[0]["Config"]["Labels"].update(
        {
            "com.docker.compose.project.config_files": ",".join(
                str(rc.ROOT / name) for name in files
            )
        }
    )
    controller.release("new", "api")
    ups = [call for call in docker.calls if "up" in call]
    assert len(ups) == 1 and ups[0].count("-f") == 5
    assert "opensandbox-server" not in ups[0]
    assert "mcp-receiver" not in ups[0]


def test_private_controller_and_state_modes_enforced(setup):
    _controller, _docker, root = setup
    (root / "control.json").chmod(0o644)
    with pytest.raises(rc.ReleaseError, match="private"):
        rc.Controller(root / "control.json", "prod")
    (root / "control.json").chmod(0o600)
    (root / "state").chmod(0o755)
    with pytest.raises(rc.ReleaseError, match="private"):
        rc.Controller(root / "control.json", "prod")


def test_web_502_refuses_success_and_restores_previous_index(setup):
    controller, _docker, root = setup
    original = controller.http
    controller.http = lambda url: (
        (502, b"") if url.endswith("/login") else original(url)
    )
    with pytest.raises(rc.ReleaseError, match="smoke"):
        controller.release("new", "web")
    assert "old-hash" in (root / "static/index.html").read_text()
    assert controller.current()["web"]["release_id"] == "old"
    assert (root / "static/assets/new-hash.js").exists()


def test_receiver_unavailable_is_not_reported_as_zero(setup):
    controller, docker, _root = setup
    controller.config["receiver_activity_urls"] = []
    with pytest.raises(rc.ReleaseError, match="journal/outbox"):
        controller.release("new", "api")
    assert not any("up" in call for call in docker.calls)


def test_probe_disables_proxy_and_http_errors_become_status(monkeypatch):
    from urllib.error import HTTPError

    captured = []

    class Opener:
        def open(self, request, timeout):
            captured.append((request.full_url, request.get_method(), timeout))
            raise HTTPError(request.full_url, 502, "synthetic", {}, None)

    def builder(handler):
        assert handler.proxies == {}
        return Opener()

    monkeypatch.setattr(rc, "build_opener", builder)
    assert rc.get_http("http://127.0.0.1/health/ready") == (502, b"")
    assert captured == [("http://127.0.0.1/health/ready", "GET", 5)]


def test_staging_release_keeps_isolated_project_and_overlays(setup):
    old_controller, docker, root = setup
    config = json.loads((root / "control.json").read_text())
    config.update(
        environment="staging",
        backend_url="http://127.0.0.1:18090",
        public_url="http://127.0.0.1:18132",
        receiver_activity_urls=[],
        receiver_activity_required=False,
        admission_barrier=admission_config(),
    )
    private(root / "control.json", json.dumps(config))
    ctl = rc.Controller(
        root / "control.json", "staging", runner=docker, http=old_controller.http
    )
    for name in ("old", "new"):
        path = root / "releases" / name / "manifest.json"
        data = json.loads(path.read_text())
        data["compose"]["project"] = "pskit-agent-staging"
        data["compose"]["files"].append("deploy/agent/compose.staging.yaml")
        data["compose"]["resolved_sha256"] = rc.fingerprint(ctl.render(data))
        path.write_text(json.dumps(data))
    state = ctl.current()
    state["api"]["manifest_sha256"] = hashlib.sha256(
        (root / "releases/old/manifest.json").read_bytes()
    ).hexdigest()
    state["web"] = state["api"]
    private(root / "state/current.json", json.dumps(state))
    ctl.release("new", "api")
    ups = [call for call in docker.calls if "up" in call]
    assert len(ups) == 1 and ups[0].count("-f") == 4
    assert ups[0][ups[0].index("-p") + 1] == "pskit-agent-staging"
    assert ups[0][-1] == "backend"


def test_interrupted_recovery_rechecks_rendered_fingerprint(setup):
    controller, docker, root = setup
    state = controller.current()
    _, digest = controller.artifact("new")
    private(
        root / "state/pending.json",
        json.dumps(
            {
                "component": "api",
                "previous": state["api"],
                "target": "new",
                "target_reference": {"release_id": "new", "manifest_sha256": digest},
                "cloud_before": (root / "cloud.env").read_text(),
                "created_at": time.time(),
            }
        ),
    )
    pending_path = root / "state/pending.json"
    pending = json.loads(pending_path.read_text())
    pending["admission"] = controller._admission_identity(
        pending, controller._current_manifest("api"), new=True
    )
    pending["admission_phase"] = "prepared"
    private(pending_path, json.dumps(pending))
    docker.render_mutation = lambda model: model["services"]["backend"]["volumes"][
        0
    ].update(source="/drifted")
    with pytest.raises(rc.ReleaseError, match="fingerprint"):
        controller.release("old", "api", rollback=True)
    assert not any("up" in call for call in docker.calls)


def interrupted_web(controller, root):
    state = controller.current()
    _, digest = controller.artifact("new")
    private(
        root / "state/pending.json",
        json.dumps(
            {
                "component": "web",
                "previous": state["web"],
                "target": "new",
                "target_reference": {"release_id": "new", "manifest_sha256": digest},
                "cloud_before": (root / "cloud.env").read_text(),
                "created_at": time.time(),
            }
        ),
    )
    (root / "static/index.html").write_bytes(
        (root / "releases/new/dist/index.html").read_bytes()
    )


def test_interrupted_web_republishes_missing_immutable_old_asset(setup):
    controller, docker, root = setup
    interrupted_web(controller, root)
    asset = root / "static/assets/old-hash.js"
    asset.unlink()
    controller.release("old", "web", rollback=True)
    assert (
        asset.read_bytes()
        == (root / "releases/old/dist/assets/old-hash.js").read_bytes()
    )
    assert "old-hash" in (root / "static/index.html").read_text()
    assert not (root / "state/pending.json").exists()
    assert not any("up" in call for call in docker.calls)


def test_interrupted_web_collision_retains_new_index_and_pending(setup):
    controller, _docker, root = setup
    interrupted_web(controller, root)
    (root / "static/assets/old-hash.js").write_text("untrusted collision")
    with pytest.raises(rc.ReleaseError, match="collision"):
        controller.release("old", "web", rollback=True)
    assert "new-hash" in (root / "static/index.html").read_text()
    assert (root / "state/pending.json").exists()


def test_api_refuses_missing_admission_adapter_before_backend_change(setup):
    controller, docker, root = setup
    controller.config.pop("admission_barrier", None)
    before = (root / "cloud.env").read_bytes()
    with pytest.raises(rc.ReleaseError, match="admission"):
        controller.release("new", "api")
    assert not any("up" in call for call in docker.calls)
    assert (root / "cloud.env").read_bytes() == before


def test_new_activity_between_image_inspection_and_up_is_refused(setup):
    controller, docker, root = setup
    original = controller.runner

    def race(command, **kwargs):
        result = original(command, **kwargs)
        if command[:4] == ["docker", "image", "inspect", "sha256:" + "b" * 64]:
            docker.activity["counts"]["runs_active"] = 1
        return result

    controller.runner = race
    before = (root / "cloud.env").read_bytes()
    with pytest.raises(rc.ReleaseError, match="activity|recovery"):
        controller.release("new", "api")
    assert not any("up" in call for call in docker.calls)
    assert (root / "cloud.env").read_bytes() == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("owner_nonce", "0" * 64),
        ("environment", "staging"),
        ("project", "other-stack"),
        ("adapter_id", "other-adapter"),
        ("qualification_sha256", "0" * 64),
        ("state", "revoked"),
        ("hold_mode", "expires_and_opens"),
        ("scopes", []),
        ("valid_until", 0),
        ("observed_at", 0),
        ("previous_manifest_sha256", "0" * 64),
        ("target_manifest_sha256", "0" * 64),
    ],
)
def test_admission_receipt_must_match_fresh_owner_scope_and_artifacts(
    setup, field, value
):
    controller, docker, root = setup
    docker.admission_mutation = lambda _operation, receipt: receipt.update(
        {field: value}
    )
    with pytest.raises(rc.ReleaseError, match="admission|recovery"):
        controller.release("new", "api")
    assert not any("up" in call for call in docker.calls)
    assert (root / "state/pending.json").exists()
    assert docker.admission_closed


def test_partial_admission_acquisition_keeps_owner_marker_for_recovery(setup):
    controller, docker, root = setup
    docker.admission_fail.add("enter")
    with pytest.raises(rc.ReleaseError, match="admission|recovery"):
        controller.release("new", "api")
    assert not any("up" in call for call in docker.calls)
    pending = json.loads((root / "state/pending.json").read_text())
    assert pending["admission"]["owner_nonce"] == docker.admission_owner
    assert pending["admission_phase"] == "prepared"
    assert docker.admission_closed


def test_barrier_loss_after_candidate_up_prevents_rollback_and_retains_owner(setup):
    controller, docker, root = setup

    def revoke(operation, receipt):
        if operation == "verify" and docker.image.endswith("b" * 64):
            receipt["state"] = "revoked"

    docker.admission_mutation = revoke
    with pytest.raises(rc.ReleaseError, match="recovery"):
        controller.release("new", "api")
    assert len([call for call in docker.calls if "up" in call]) == 1
    assert (root / "state/pending.json").exists()
    assert docker.admission_closed


def test_release_ack_failure_does_not_recreate_backend_after_possible_reopening(setup):
    controller, docker, root = setup

    def corrupt_release(operation, receipt):
        if operation == "release":
            receipt["owner_nonce"] = "0" * 64

    docker.admission_mutation = corrupt_release
    with pytest.raises(rc.ReleaseError, match="recovery"):
        controller.release("new", "api")
    assert len([call for call in docker.calls if "up" in call]) == 1
    assert (
        not docker.admission_closed
    )  # the command could have succeeded despite invalid ack
    pending = json.loads((root / "state/pending.json").read_text())
    assert pending["admission_phase"] == "releasing"
    owner = pending["admission"]["owner_nonce"]
    docker.admission_mutation = lambda _operation, _receipt: None
    controller.release("old", "api", rollback=True)
    assert docker.admission_calls[-1][0] == "release"
    assert all(request["owner_nonce"] == owner for _, request in docker.admission_calls)
    assert controller.current()["api"]["release_id"] == "old"
    assert not (root / "state/pending.json").exists()


def test_web_and_readonly_do_not_require_or_enter_admission_barrier(setup):
    controller, docker, _root = setup
    controller.config.pop("admission_barrier")
    controller.doctor()
    controller.status()
    controller.release("new", "web")
    assert docker.admission_calls == []


def test_admission_hold_remains_closed_through_smoke_and_final_verification(setup):
    controller, docker, _root = setup
    original = controller.runner

    def observe(command, **kwargs):
        if "up" in command or command[0] == "synthetic-critical-smoke":
            assert docker.admission_closed
        return original(command, **kwargs)

    controller.runner = observe
    controller.release("new", "api")
    operations = [operation for operation, _ in docker.admission_calls]
    assert operations[0] == "enter" and operations[-1] == "release"
    assert operations.count("verify") >= 4
    assert not docker.admission_closed


def test_admission_owner_is_durable_before_enter_and_bound_to_manifest_on_recovery(
    setup,
):
    controller, docker, root = setup
    original = controller.runner

    def interrupted_enter(command, **kwargs):
        if command == admission_config()["enter_command"]:
            request = json.loads(kwargs["input_text"])
            pending = json.loads((root / "state/pending.json").read_text())
            assert request["owner_nonce"] == pending["admission"]["owner_nonce"]
            assert pending["admission_phase"] == "prepared"
        return original(command, **kwargs)

    controller.runner = interrupted_enter
    docker.admission_fail.add("enter")
    with pytest.raises(rc.ReleaseError, match="recovery"):
        controller.release("new", "api")
    docker.admission_fail.clear()
    controller.config["admission_barrier"]["qualification_sha256"] = "e" * 64
    with pytest.raises(rc.ReleaseError, match="admission owner"):
        controller.release("old", "api", rollback=True)
    assert len(docker.admission_calls) == 1
    assert not any("up" in call for call in docker.calls)
    assert docker.admission_closed


def test_activity_appearing_during_candidate_smoke_retains_hold_without_rollback(setup):
    controller, docker, root = setup
    original = controller.runner

    def new_job(command, **kwargs):
        result = original(command, **kwargs)
        if command[0] == "synthetic-critical-smoke":
            docker.activity["counts"]["jobs_active"] = 1
        return result

    controller.runner = new_job
    with pytest.raises(rc.ReleaseError, match="recovery"):
        controller.release("new", "api")
    assert len([call for call in docker.calls if "up" in call]) == 1
    assert docker.admission_closed
    assert not any(operation == "release" for operation, _ in docker.admission_calls)
    assert (root / "state/pending.json").exists()


def test_observation_expiry_does_not_release_or_retry_an_unknown_hold(setup):
    controller, docker, root = setup

    def expire_verify(operation, receipt):
        if operation == "verify":
            receipt["valid_until"] = time.time() - 1

    docker.admission_mutation = expire_verify
    with pytest.raises(rc.ReleaseError, match="recovery"):
        controller.release("new", "api")
    assert docker.admission_closed
    assert not any("up" in call for call in docker.calls)
    assert not any(operation == "release" for operation, _ in docker.admission_calls)
    assert (root / "state/pending.json").exists()


def test_api_recovery_with_revoked_owner_never_recreates_or_removes_marker(setup):
    controller, docker, root = setup
    docker.admission_fail.add("enter")
    with pytest.raises(rc.ReleaseError, match="recovery"):
        controller.release("new", "api")
    docker.admission_fail.clear()
    docker.admission_mutation = lambda _operation, receipt: receipt.update(
        state="revoked"
    )
    with pytest.raises(rc.ReleaseError, match="admission"):
        controller.release("old", "api", rollback=True)
    assert not any("up" in call for call in docker.calls)
    assert (root / "state/pending.json").exists()


@pytest.mark.parametrize("output", ["true", "{}", "not-json", "x" * (65536 + 1)])
def test_admission_receipt_is_bounded_typed_json_not_a_boolean_flag(setup, output):
    controller, docker, root = setup
    original = controller.runner
    controller.runner = lambda command, **kwargs: (
        output if command[0] == "/synthetic/admission" else original(command, **kwargs)
    )
    with pytest.raises(rc.ReleaseError, match="admission|recovery"):
        controller.release("new", "api")
    assert not any("up" in call for call in docker.calls)
    assert (root / "state/pending.json").exists()


def test_immediate_web_rollback_also_restores_missing_old_assets(setup):
    controller, _docker, root = setup
    original = controller._postflight

    def fail_after_asset_loss():
        (root / "static/assets/old-hash.js").unlink()
        raise rc.ReleaseError("Synthetic critical smoke failed")

    controller._postflight = fail_after_asset_loss
    with pytest.raises(rc.ReleaseError, match="smoke"):
        controller.release("new", "web")
    assert (root / "static/assets/old-hash.js").read_text() == "old script"
    assert "old-hash" in (root / "static/index.html").read_text()
    controller._postflight = original


@pytest.mark.parametrize(
    "mutation",
    [
        lambda config: config.update(adapter_id="invalid/identity"),
        lambda config: config.update(qualification_sha256="not-a-digest"),
        lambda config: config.update(enter_command="/bin/sh -c something"),
        lambda config: config.update(verify_command=["relative-script"]),
        lambda config: config.update(
            release_command=["/absolute/command", "bad\0argument"]
        ),
        lambda config: config.update(enabled=True),
    ],
)
def test_admission_adapter_configuration_requires_exact_identity_and_structured_commands(
    setup, mutation
):
    _controller, docker, root = setup
    config = json.loads((root / "control.json").read_text())
    config["admission_barrier"] = admission_config()
    mutation(config["admission_barrier"])
    private(root / "control.json", json.dumps(config))
    with pytest.raises(rc.ReleaseError, match="admission|Admission"):
        rc.Controller(root / "control.json", "prod", runner=docker)
    assert docker.calls == []


def test_each_completed_api_transaction_uses_a_fresh_owner_nonce(setup):
    controller, docker, _root = setup
    controller.release("new", "api")
    controller.release("old", "api", rollback=True)
    owners = [
        request["owner_nonce"]
        for operation, request in docker.admission_calls
        if operation == "enter"
    ]
    assert len(owners) == 2 and owners[0] != owners[1]
    assert all(len(owner) == 64 for owner in owners)


def test_interrupted_web_symlink_asset_keeps_index_and_recovery_marker(setup):
    controller, _docker, root = setup
    interrupted_web(controller, root)
    asset = root / "static/assets/old-hash.js"
    asset.unlink()
    asset.symlink_to(root / "backend.env")
    with pytest.raises((rc.ReleaseError, OSError)):
        controller.release("old", "web", rollback=True)
    assert "new-hash" in (root / "static/index.html").read_text()
    assert (root / "state/pending.json").exists()


def test_cloud_edit_during_admission_checks_is_never_overwritten_by_pin(setup):
    controller, docker, root = setup
    cloud = root / "cloud.env"
    edited = cloud.read_bytes() + b"NEW_OPERATOR_SETTING=keep-me\n"

    def edit_during_verify(operation, _receipt):
        if operation == "verify":
            cloud.write_bytes(edited)

    docker.admission_mutation = edit_during_verify
    with pytest.raises(rc.ReleaseError, match="recovery"):
        controller.release("new", "api")
    assert cloud.read_bytes() == edited
    assert not any("up" in command for command in docker.calls)
    assert docker.admission_closed
    assert (root / "state/pending.json").exists()
