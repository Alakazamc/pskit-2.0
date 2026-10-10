"""Pure synthetic checks for first-delta timing, dropped commits and optional spans."""

import asyncio
import functools
import subprocess
import sys
import time
from types import SimpleNamespace

import benchmark_new_stack as benchmark
import pytest


def test_freeze_source_accepts_nested_apps_api_git_archive_paths(tmp_path, monkeypatch):
    repository = tmp_path / "repository"
    app = repository / "apps/api/app"
    app.mkdir(parents=True)
    (app / "main.py").write_text("app = True\n")
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(["git", "-C", str(repository), "add", "apps/api/app/main.py"], check=True)
    subprocess.run(["git", "-C", str(repository), "-c", "user.name=Test", "-c",
                    "user.email=test@example.invalid", "commit", "-qm", "fixture"], check=True)
    revision = subprocess.run(["git", "-C", str(repository), "rev-parse", "HEAD"],
                              check=True, capture_output=True, text=True).stdout.strip()
    monkeypatch.setattr(benchmark, "ROOT", repository)

    destination = tmp_path / "snapshot"
    assert benchmark.freeze_source(destination, revision) == revision
    assert (destination / "app/main.py").read_text() == "app = True\n"


def test_raw_samples_preserve_all_commit_order_and_exact_intervals():
    counters = benchmark.SqlCounters()
    counters.started = {"run-b": 1, "run-a": 2}
    counters.first_emit = {"run-b": 1.1, "run-a": 2.1}
    counters.first_delta = {"run-a": 2.4, "run-b": 1.5}
    samples = benchmark.first_delta_samples(counters)
    assert [sample["sample"] for sample in samples] == [0, 1]
    assert [sample["admission_to_first_durable_delta"] for sample in samples] == pytest.approx([400, 500])
    assert [sample["emission_to_first_durable_delta"] for sample in samples] == pytest.approx([300, 400])
    assert "run-a" not in str(samples) and "run-b" not in str(samples)


def facade(batch_result):
    methods = {name: lambda *args, **kwargs: None for name in (
        "claim_queued_runs", "claim_wakeups", "recover_wakeups", "renew_leases",
        "expire_queued_compute_jobs", "expire_running_compute_jobs",
    )}
    return SimpleNamespace(**methods, append_events=lambda *args, **kwargs: batch_result)


def test_failed_or_dropped_batch_is_never_a_durable_delta_sample():
    counters = benchmark.SqlCounters()
    counters.enabled = True
    store = facade(False)
    benchmark.instrument_store(store, counters)
    event = SimpleNamespace(type="message.delta")
    assert store.append_events("synthetic-user", "synthetic-run", [event], lease_owner="synthetic-worker") is False
    assert counters.first_delta == {}


def test_successful_batch_counts_only_the_first_committed_delta(monkeypatch):
    counters = benchmark.SqlCounters()
    counters.enabled = True
    store = facade(True)
    benchmark.instrument_store(store, counters)
    frame = SimpleNamespace(type="message.delta")
    store.append_events("user", "run", [SimpleNamespace(type="message.start")])
    assert counters.first_delta == {}
    monkeypatch.setattr(benchmark.time, "perf_counter", lambda: 17)
    store.append_events("user", "run", [frame])
    monkeypatch.setattr(benchmark.time, "perf_counter", lambda: 19)
    store.append_events("user", "run", [frame])
    assert counters.first_delta == {"run": 17}


def test_profile_is_absent_when_disabled(monkeypatch):
    monkeypatch.setattr(benchmark, "PROFILE_FIRST_DELTA", False)
    counters = benchmark.SqlCounters()
    token = benchmark.PROFILE_SAMPLE.set(0)
    try:
        counters.span("synthetic", time.perf_counter())
    finally:
        benchmark.PROFILE_SAMPLE.reset(token)
    assert counters.profile_spans == {}
    assert "first_delta_profile_spans" not in counters.snapshot()


@pytest.mark.asyncio
async def test_optional_profile_follows_worker_context_without_changing_result(monkeypatch):
    monkeypatch.setattr(benchmark, "PROFILE_FIRST_DELTA", True)
    counters = benchmark.SqlCounters()
    counters.profile_started[0] = time.perf_counter()
    counters.profile_runs["run"] = 0
    calls = []

    def append_events(*args, **kwargs):
        calls.append((args, kwargs))
        return False

    store = SimpleNamespace(append_events=append_events)

    async def db_call(method, *args, **kwargs):
        return await asyncio.to_thread(functools.partial(method, *args, **kwargs))

    service = SimpleNamespace(_db_call=db_call)

    async def execute(user_id, session_id, run_id, content):
        return await service._db_call(store.append_events, user_id, run_id, [content], lease_owner="worker")

    service._execute = execute
    benchmark.instrument_pipeline(service, store, counters)
    assert await service._execute("user", "session", "run", "synthetic") is False
    assert calls == [(("user", "run", ["synthetic"]), {"lease_owner": "worker"})]
    assert benchmark.PROFILE_SAMPLE.get() is None
    assert {span["name"] for span in counters.profile_spans[0]} == {
        "execute.enter", "execute.total", "store.append_events", "worker.append_events", "db_call.append_events",
    }


def test_optional_profile_is_bounded_and_contains_no_parameters(monkeypatch):
    monkeypatch.setattr(benchmark, "PROFILE_FIRST_DELTA", True)
    counters = benchmark.SqlCounters()
    counters.profile_started[0] = time.perf_counter()
    token = benchmark.PROFILE_SAMPLE.set(0)
    try:
        for _ in range(4000):
            counters.span("synthetic", time.perf_counter())
        with pytest.raises(RuntimeError, match="bounded trace"):
            counters.span("synthetic", time.perf_counter())
    finally:
        benchmark.PROFILE_SAMPLE.reset(token)
    assert len(counters.profile_spans[0]) == 4000
    assert set(counters.profile_spans[0][0]) == {"name", "start_ms", "duration_ms"}


@pytest.mark.parametrize("enabled", [False, True])
def test_gate_configuration_is_opt_in_and_uses_an_explicit_synthetic_binding(monkeypatch, enabled):
    calls = []
    source = object()

    class Gate:
        def __init__(self, database, **kwargs):
            assert database is source
            calls.append(kwargs)

        def configure(self):
            calls.append("configure")

    monkeypatch.setitem(sys.modules, "app.domain.release_admission", SimpleNamespace(DurableAdmissionGate=Gate))
    monkeypatch.setattr(benchmark, "ADMISSION_ENABLED", enabled)
    result = benchmark.configure_admission_gate(source)
    if enabled:
        assert isinstance(result, Gate)
        assert calls == [{"environment": "staging", "project": "pskit-agent-staging",
                          "qualification_sha256": "0" * 64}, "configure"]
    else:
        assert result is None and calls == []
