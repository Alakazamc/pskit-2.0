"""Read-only release observations use synthetic durable journals, never executors."""

import asyncio
import json
import socket
import sqlite3
import sys
import threading
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from pskit_compute.journal import Journal


@pytest.fixture
def activity_state(tmp_path):
    from pskit_compute.activity import ActivityObserver, LiveActivity

    paths = tuple(tmp_path / name for name in ("coral.sqlite3", "af3.sqlite3", "af3-product.sqlite3"))
    spool = tmp_path / "jobs"
    spool.mkdir()
    with ExitStack() as stack:
        journals = [Journal(path) for path in paths]
        for journal in journals:
            stack.callback(journal.close)
        live = LiveActivity({"generic": True, "compatibility": True, "product": True})
        for lane in ("generic", "compatibility", "product"):
            live.finish(lane, "idle")
        observer = ActivityObserver(paths, spool, live)
        yield SimpleNamespace(paths=paths, spool=spool, journals=journals, live=live, observer=observer)


def test_empty_real_wal_journals_and_idle_lanes_are_the_only_clean_state(activity_state):
    state = activity_state
    document = state.observer.observe()
    assert set(document) == {"schema_version", "observed_at", "counts"}
    assert document["schema_version"] == "pskit.receiver-activity.v1"
    assert document["counts"] == {"active": 0, "unknown": 0, "journal_pending": 0, "outbox_pending": 0}
    assert all(journal.db.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 0 for journal in state.journals)


@pytest.mark.parametrize("index", [0, 1, 2])
@pytest.mark.parametrize("phase", ["executing", "waiting", "outbox", "unrecognized"])
def test_every_unacknowledged_journal_blocks_release(activity_state, index, phase):
    state = activity_state
    with state.journals[index].db:
        state.journals[index].db.execute("INSERT INTO executions(job_id,state,grant_json,payload_json) VALUES (?,?,?,?)",
            ("synthetic-document", phase, '{"private":"must-not-leak"}', '{}' if phase == "outbox" else None))
    document = state.observer.observe()
    assert document["counts"]["journal_pending"] == 1
    if phase == "outbox":
        assert document["counts"]["outbox_pending"] == 1
    if phase in {"executing", "unrecognized"}:
        assert document["counts"]["unknown"] > 0
    assert "synthetic-document" not in json.dumps(document) and "must-not-leak" not in json.dumps(document)


@pytest.mark.parametrize("fault", ["missing", "unreadable", "symlink", "schema", "corrupt", "extra-table"])
def test_missing_or_unrecognized_journal_never_reports_clean(activity_state, tmp_path, fault):
    state = activity_state
    path = state.paths[1]
    if fault == "missing":
        path.unlink()
    elif fault == "unreadable":
        path.chmod(0)
    elif fault == "symlink":
        path.unlink()
        path.symlink_to(state.paths[0])
    elif fault == "corrupt":
        path.write_bytes(b"not sqlite")
    else:
        with state.journals[1].db:
            state.journals[1].db.execute("ALTER TABLE executions ADD COLUMN surprise TEXT" if fault == "schema" else
                "CREATE TABLE omitted_outbox (payload TEXT)")
    assert state.observer.observe()["counts"]["unknown"] > 0


@pytest.mark.parametrize("changed_default", ["seq", "progress"])
def test_unrecognized_column_default_is_not_a_clean_journal(activity_state, changed_default):
    state = activity_state
    with state.journals[1].db:
        state.journals[1].db.execute("DROP TABLE executions")
        defaults = {"seq": "0", "progress": "0", changed_default: "1"}
        state.journals[1].db.execute(
            "CREATE TABLE executions (job_id TEXT PRIMARY KEY, state TEXT NOT NULL, "
            "grant_json TEXT NOT NULL, payload_json TEXT, pending_json TEXT, "
            f"seq INTEGER NOT NULL DEFAULT {defaults['seq']}, "
            f"progress INTEGER NOT NULL DEFAULT {defaults['progress']})")
    assert state.observer.observe()["counts"]["unknown"] > 0


@pytest.mark.parametrize("fault", ["pending", "outbox", "unknown-name", "symlink", "missing", "unreadable"])
def test_all_retained_or_unverifiable_spool_state_blocks_release(activity_state, fault):
    state = activity_state
    if fault == "missing":
        state.spool.rmdir()
    elif fault == "unreadable":
        state.spool.chmod(0)
    elif fault == "symlink":
        (state.spool / "entry").symlink_to(state.paths[0])
    else:
        job = state.spool / ("00000000-0000-0000-0000-000000000001" if fault != "unknown-name" else "unexpected")
        job.mkdir()
        (job / ("outcome.json" if fault == "outbox" else "input.json")).write_text('{"private":"not read"}')
    counts = state.observer.observe()["counts"]
    assert any(counts.values())
    assert counts["outbox_pending"] > 0 if fault == "outbox" else counts["active"] + counts["unknown"] > 0


def test_unready_busy_or_unknown_live_lane_is_not_idle(activity_state):
    from pskit_compute.activity import LiveActivity

    state = activity_state
    state.observer.live = LiveActivity({"generic": True, "compatibility": True, "product": False})
    assert state.observer.observe()["counts"]["unknown"] > 0
    state.observer.live.finish("generic", "idle")
    state.observer.live.finish("compatibility", "idle")
    assert not any(state.observer.observe()["counts"].values())
    state.observer.live.begin("generic")
    assert state.observer.observe()["counts"]["active"] > 0
    state.observer.live.finish("generic", "unknown")
    assert state.observer.observe()["counts"]["unknown"] > 0


def test_change_during_observation_is_unknown(activity_state, monkeypatch):
    state = activity_state
    original = state.observer._journal
    def changing(path):
        result = original(path)
        state.live.begin("generic")
        state.live.finish("generic", "idle")
        return result
    monkeypatch.setattr(state.observer, "_journal", changing)
    assert state.observer.observe()["counts"]["unknown"] > 0


@pytest.mark.asyncio
async def test_private_bounded_endpoint_returns_only_strict_counts(activity_state):
    from pskit_compute.activity_http import ActivityApp

    token = "synthetic-activity-token-" + "x" * 32
    app = ActivityApp(activity_state.observer, token)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        for kwargs in ({}, {"headers": {"Authorization": "Bearer wrong"}},
                       {"headers": {"Authorization": "Bearer " + token}, "params": {"journal": "empty"}}):
            response = await client.get("/activity", **kwargs)
            assert response.status_code == 404 and str(activity_state.paths[0]) not in response.text
        response = await client.get("/activity", headers={"Authorization": "Bearer " + token})
        assert response.status_code == 200 and len(response.content) < 64 * 1024
        assert response.json()["counts"] == {"active": 0, "unknown": 0, "journal_pending": 0, "outbox_pending": 0}
        assert (await client.post("/activity", headers={"Authorization": "Bearer " + token})).status_code == 404
        assert (await client.request("GET", "/activity", content=b"x",
                                    headers={"Authorization": "Bearer " + token})).status_code == 404
        assert (await client.get("/activity", headers=[("Authorization", "Bearer " + token)] * 2)).status_code == 404


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "example.org", "8.8.8.8"])
def test_listener_default_disabled_and_only_explicit_private_ip_can_enable(host):
    from pskit_compute.activity_http import ActivitySettings

    assert not ActivitySettings.from_environment({}).enabled
    with pytest.raises(ValueError, match="ACTIVITY_CONFIG"):
        ActivitySettings.from_environment({"PSKIT_RECEIVER_ACTIVITY_ENABLED": "true",
            "PSKIT_RECEIVER_ACTIVITY_TOKEN": "x" * 40, "PSKIT_RECEIVER_ACTIVITY_HOST": host})
    settings = ActivitySettings.from_environment({"PSKIT_RECEIVER_ACTIVITY_ENABLED": "true",
        "PSKIT_RECEIVER_ACTIVITY_TOKEN": "x" * 40, "PSKIT_RECEIVER_ACTIVITY_HOST": "10.9.8.2"})
    assert settings.enabled and settings.host == "10.9.8.2"


def test_row_and_scan_limits_fail_closed(activity_state):
    state = activity_state
    state.observer.max_entries = 1
    with state.journals[0].db:
        state.journals[0].db.executemany("INSERT INTO executions(job_id,state,grant_json) VALUES (?,'waiting','{}')",
            [("one",), ("two",)])
    assert state.observer.observe()["counts"]["unknown"] > 0
    connection = sqlite3.connect(f"file:{state.paths[0]}?mode=ro", uri=True)
    try:
        assert connection.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 2
    finally:
        connection.close()


@pytest.mark.parametrize("sidecar", ["-wal", "-shm"])
@pytest.mark.parametrize("fault", ["missing", "symlink", "broken-symlink", "unreadable"])
def test_wal_sidecars_must_exist_and_cannot_be_followed(activity_state, tmp_path, sidecar, fault):
    path = activity_state.paths[0].with_name(activity_state.paths[0].name + sidecar)
    if fault == "unreadable":
        path.chmod(0)
    else:
        path.unlink()
        if fault != "missing":
            path.symlink_to(tmp_path / "absent" if fault == "broken-symlink" else activity_state.paths[1])
    assert activity_state.observer.observe()["counts"]["unknown"] > 0
    if fault == "missing":
        assert not path.exists(), "Read-only observation must not recreate a missing WAL sidecar"


def test_closed_wal_owner_is_unknown_without_creating_observation_files(activity_state):
    activity_state.journals[0].close()
    names = sorted(path.name for path in activity_state.paths[0].parent.iterdir())
    assert activity_state.observer.observe()["counts"]["unknown"] > 0
    assert sorted(path.name for path in activity_state.paths[0].parent.iterdir()) == names


def test_uncheckpointed_wal_rows_are_observed_and_never_modified(activity_state):
    journal = activity_state.journals[0]
    journal.db.execute("PRAGMA wal_autocheckpoint=0")
    with journal.db:
        journal.db.execute("INSERT INTO executions(job_id,state,grant_json) VALUES ('synthetic','waiting','{}')")
    paths = [activity_state.paths[0], activity_state.paths[0].with_name("coral.sqlite3-wal")]
    before = [path.read_bytes() for path in paths]
    assert activity_state.observer.observe()["counts"]["journal_pending"] == 1
    assert [path.read_bytes() for path in paths] == before


def test_spool_change_during_observation_is_unknown(activity_state, monkeypatch):
    original, calls = activity_state.observer._spool, 0
    def changing():
        nonlocal calls
        result = original()
        calls += 1
        if calls == 1:
            path = activity_state.spool / "00000000-0000-0000-0000-000000000001"
            path.mkdir()
            (path / "outcome.json").write_text("private content must not be read")
        return result
    monkeypatch.setattr(activity_state.observer, "_spool", changing)
    assert activity_state.observer.observe()["counts"]["unknown"] > 0


def test_temporary_parent_swap_cannot_redirect_sqlite_to_empty_journals(activity_state, tmp_path, monkeypatch):
    state = activity_state
    with state.journals[0].db:
        state.journals[0].db.execute("INSERT INTO executions(job_id,state,grant_json) VALUES ('synthetic','waiting','{}')")
    alternate, backup = tmp_path.with_name(tmp_path.name + "-alternate"), tmp_path.with_name(tmp_path.name + "-backup")
    alternate.mkdir()
    (alternate / "jobs").mkdir()
    original_connect, swapped = sqlite3.connect, False
    with ExitStack() as stack:
        for path in state.paths:
            stack.callback(Journal(alternate / path.name).close)
        def swapping(database, **kwargs):
            nonlocal swapped
            if swapped:
                return original_connect(database, **kwargs)
            swapped = True
            tmp_path.rename(backup)
            tmp_path.symlink_to(alternate, target_is_directory=True)
            try:
                return original_connect(database, **kwargs)
            finally:
                tmp_path.unlink()
                backup.rename(tmp_path)
        monkeypatch.setattr(sqlite3, "connect", swapping)
        counts = state.observer.observe()["counts"]
        assert counts["unknown"] > 0
        # SQLite may canonicalize its WAL path before the parent is restored.
        # That partial read cannot assert an exact row count and must stay closed.
        assert any(counts.values())
    assert state.journals[0].db.execute("SELECT COUNT(*) FROM executions").fetchone()[0] == 1


def test_settings_hide_tokens_and_validate_direct_construction():
    from pskit_compute.activity_http import ActivitySettings

    token = "private-synthetic-token-" + "x" * 40
    assert token not in repr(ActivitySettings(True, "::1", 18188, token))
    with pytest.raises(ValueError, match="ACTIVITY_CONFIG"):
        ActivitySettings(True, "0.0.0.0", 18188, token)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_first", [False, True])
async def test_observation_admission_is_bounded_even_when_request_is_cancelled(cancel_first):
    from pskit_compute.activity_http import ActivityApp

    entered, release = threading.Event(), threading.Event()
    calls = []
    def observe():
        calls.append(True)
        entered.set()
        assert release.wait(5)
        return {"synthetic": True}
    token = "x" * 40
    app = ActivityApp(SimpleNamespace(observe=observe), token)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        first = asyncio.create_task(client.get("/activity", headers={"Authorization": "Bearer " + token}))
        try:
            async with asyncio.timeout(5):
                while not entered.is_set():
                    await asyncio.sleep(0.001)
            if cancel_first:
                first.cancel()
                await asyncio.sleep(0.01)
            second = await client.get("/activity", headers={"Authorization": "Bearer " + token})
            assert second.status_code == 503 and len(calls) == 1
        finally:
            release.set()
            if cancel_first:
                with pytest.raises(asyncio.CancelledError):
                    await first
            else:
                assert (await first).status_code == 200
        assert (await client.get("/activity", headers={"Authorization": "Bearer " + token})).status_code == 200


@pytest.mark.asyncio
async def test_actual_private_http_reports_durable_journal_and_controller_refuses_pending(activity_state, tmp_path):
    from deploy.agent.scripts import release_control as rc

    from pskit_compute.activity_http import ActivitySettings, activity_listener

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    token = "synthetic-activity-token-" + "x" * 32
    token_file = tmp_path / "activity.token"
    token_file.write_text(token)
    token_file.chmod(0o600)
    url = f"http://127.0.0.1:{port}/activity"
    controller = object.__new__(rc.Controller)
    controller.config = {"receiver_activity_urls": [url], "receiver_activity_auth": {url: {"token_file": str(token_file)}}}
    controller.receiver_http = rc.get_receiver_http
    async with activity_listener(activity_state.observer, ActivitySettings(True, "127.0.0.1", port, token)):
        assert await asyncio.to_thread(controller._receiver_activity, required=True) is None
        _status, body = await asyncio.to_thread(rc.get_receiver_http, url, token)
        assert json.loads(body)["counts"] == {"active": 0, "unknown": 0, "journal_pending": 0, "outbox_pending": 0}
        assert (await asyncio.to_thread(rc.get_receiver_http, url, "y" * 40))[0] == 404
        with activity_state.journals[2].db:
            activity_state.journals[2].db.execute("INSERT INTO executions(job_id,state,grant_json,payload_json) VALUES ('synthetic','outbox','{}','{}')")
        with pytest.raises(rc.ReleaseError, match="Receiver evidence"):
            await asyncio.to_thread(controller._receiver_activity, required=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled,product", [(False, False), (True, False), (True, True)])
async def test_unified_startup_registers_every_enabled_lane_before_observation(tmp_path, monkeypatch, enabled, product):
    from pskit_compute.activity import ObservedReceiver
    from scripts import mcp_compute_receiver as receiver_module

    monkeypatch.setenv("PSKIT_COMPUTE_SERVICE_KEY", "synthetic-key")
    monkeypatch.setenv("RESEARCH_AGENT_COMPUTE_CALLBACK_KEY", "synthetic-compat-key")
    monkeypatch.setenv("PSKIT_RECEIVER_ACTIVITY_ENABLED", str(enabled).lower())
    monkeypatch.setenv("PSKIT_RECEIVER_ACTIVITY_TOKEN", "x" * 40)
    if product:
        monkeypatch.setenv("PSKIT_AF3_COMPUTE_SERVICE_KEY", "synthetic-product-key")
    else:
        monkeypatch.delenv("PSKIT_AF3_COMPUTE_SERVICE_KEY", raising=False)
    captured, polled, done = {}, [], asyncio.Event()
    class Worker:
        def __init__(self, _executor, _control, journal, identity):
            self.journal, self.identity = journal, identity
        async def run_once(self):
            return SimpleNamespace(status="idle")
    class Spool:
        def __init__(self, path):
            self.path = Path(path)
            self.path.mkdir(exist_ok=True)
        def directory(self, job_id):
            return self.path / job_id
    monkeypatch.setattr(receiver_module, "Receiver", Worker)
    monkeypatch.setattr(receiver_module, "ControlClient", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(receiver_module, "DynamicMcpExecutor", lambda *_args: None)
    monkeypatch.setitem(sys.modules, "pskit_compute.af3_compat", SimpleNamespace(
        Af3ControlClient=lambda *_args, **_kwargs: SimpleNamespace(cleanup=lambda _job: None),
        Af3McpExecutor=lambda *_args, **_kwargs: None))
    monkeypatch.setitem(sys.modules, "pskit_compute.af3_spool", SimpleNamespace(Af3Spool=Spool))
    @asynccontextmanager
    async def listener(observer, settings):
        captured.update(observer=observer, settings=settings)
        if enabled:
            assert observer.observe()["counts"]["unknown"] >= 2 + int(product)
        yield
    @asynccontextmanager
    async def local_mcp(*_args, **_kwargs):
        yield
    async def receive(worker, *_args, **_kwargs):
        if worker.identity.service_id == "synthetic-document":
            assert isinstance(worker, ObservedReceiver) is enabled
        await worker.run_once()
        polled.append(worker)
        if len(polled) == 2:
            done.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(receiver_module, "activity_listener", listener)
    monkeypatch.setattr(receiver_module, "local_af3_mcp", local_mcp)
    monkeypatch.setattr(receiver_module, "receive", receive)
    args = receiver_module.argument_parser().parse_args([
        "--backend-url", "http://synthetic.invalid", "--service-id", "synthetic-document", "--worker-id", "worker",
        "--journal", str(tmp_path / "coral.sqlite3"), "--af3-backend-url", "http://synthetic.invalid",
        "--af3-spool-dir", str(tmp_path / "jobs"), "--af3-gpu-uuid", "synthetic-resource"])
    # A disabled lane still contributes its configured retained journal. No lane
    # or request can select an empty subset of the observation domain.
    with ExitStack() as stack:
        if not product:
            stack.callback(Journal(tmp_path / "af3-product.sqlite3").close)
        task = asyncio.create_task(receiver_module.run(args))
        try:
            await asyncio.wait_for(done.wait(), 5)
            assert captured["settings"].enabled is enabled
            if enabled:
                assert not any(captured["observer"].observe()["counts"].values())
            else:
                assert captured["observer"] is None
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
