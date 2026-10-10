#!/usr/bin/env python3
"""Measure real AgentService/PG17 paths using disposable schemas and a synthetic Pi.

Use TEST_POSTGRES_DSN for the already provisioned loopback remediation database.
The harness creates/drops only schemas bearing its own random ID. It neither
reads .env files nor starts containers, servers, suppliers, or GPU tasks.
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import functools
import hashlib
import io
import json
import os
import platform
import re
import resource
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT = Path(__file__).resolve().parents[1]
OBSERVER = contextvars.ContextVar("benchmark_observer", default=False)
STAGE_DEADLINE = contextvars.ContextVar("benchmark_stage_deadline", default=None)
PROFILE_SAMPLE = contextvars.ContextVar("benchmark_profile_sample", default=None)
PROFILE_FIRST_DELTA = False
ADMISSION_ENABLED = False
COUNTERS: SqlCounters | None = None
SOURCE_FILES = (
    "app/services/agent.py",
    "app/db/postgres.py",
    "app/domain/persistent_conversation/store.py",
    "app/domain/persistent_conversation/runs.py",
    "app/domain/persistent_conversation/recovery.py",
    "app/domain/persistent_conversation/admission.py",
    "app/services/session_locks.py",
    "app/services/agent_scheduler.py",
    "app/services/agent_events.py",
    "app/services/agent_context.py",
    "app/services/agent_executor.py",
    "app/services/agent_resume.py",
    "app/services/agent_workspace.py",
    "app/domain/release_admission.py",
    "app/db/release_admission_migrations.py",
    "app/db/release_admission_v1.sql",
    "app/db/postgres_migrations/__init__.py",
    "app/domain/compute/jobs.py",
    "app/domain/compute/leases.py",
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def percentile(values: list[float], quantile: float) -> float | None:
    """Use a documented linear interpolation, including small samples."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower), 3)


def latency_summary(values: list[float]) -> dict:
    return {"samples": len(values), "p50_ms": percentile(values, 0.5),
            "p95_ms": percentile(values, 0.95), "max_ms": round(max(values), 3) if values else None}


class SqlCounters:
    """Count executed SQL and consumed rows; do not retain parameters or results."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.enabled = False
        self.statements: Counter = Counter()
        self.materialized: Counter = Counter()
        self.affected_writes: Counter = Counter()
        self.event_types: Counter = Counter()
        self.failures = 0
        self.sql_seconds = 0.0
        self.method_calls: Counter = Counter()
        self.started: dict[str, float] = {}
        self.first_delta: dict[str, float] = {}
        self.first_emit: dict[str, float] = {}
        self.profile_started: dict[int, float] = {}
        self.profile_runs: dict[str, int] = {}
        self.profile_spans: dict[int, list[dict]] = {}

    def span(self, name: str, started: float, finished: float | None = None) -> None:
        sample = PROFILE_SAMPLE.get()
        if not PROFILE_FIRST_DELTA or sample is None:
            return
        finished = time.perf_counter() if finished is None else finished
        with self.lock:
            spans = self.profile_spans.setdefault(sample, [])
            if len(spans) >= 4000:
                raise RuntimeError("Synthetic profile exceeded its bounded trace")
            spans.append({"name": name,
                          "start_ms": (started - self.profile_started[sample]) * 1000,
                          "duration_ms": (finished - started) * 1000})

    @staticmethod
    def key(statement: str) -> str:
        normalized = " ".join(statement.split())
        if normalized.startswith("SELECT id,user_id,session_id,context_json FROM agent_runs"):
            return "SELECT queued_candidates"
        if normalized.startswith("SELECT r.id,r.user_id,r.session_id,j.id FROM agent_runs"):
            return "SELECT wakeup_candidates"
        if normalized.startswith("INSERT INTO agent_events"):
            return "INSERT agent_events"
        return f"{normalized.partition(' ')[0].upper()} {hashlib.sha256(normalized.encode()).hexdigest()[:12]}"

    def statement(self, query: str, parameters, elapsed: float, rows_affected: int, *, failed: bool) -> str:
        key = self.key(query)
        if not self.enabled:
            return key
        observer = OBSERVER.get()
        recorded_key = f"observer {key}" if observer else key
        with self.lock:
            self.statements[recorded_key] += 1
            self.sql_seconds += elapsed
            self.failures += int(failed)
            if key.startswith(("INSERT ", "UPDATE ", "DELETE ")) and rows_affected > 0:
                self.affected_writes[recorded_key] += rows_affected
            if key == "INSERT agent_events" and not failed and parameters:
                payload = json.loads(parameters[-1])
                self.event_types[payload["type"]] += 1
        return recorded_key

    def rows(self, key: str | None, count: int) -> None:
        if self.enabled and key and count:
            with self.lock:
                self.materialized[key] += count

    def snapshot(self) -> dict:
        with self.lock:
            application = {key: value for key, value in self.statements.items()
                           if not key.startswith("observer ")}
            return {
                "sql_statements": sum(application.values()),
                "select_statements": sum(value for key, value in application.items() if key.startswith("SELECT ")),
                "write_statements": sum(value for key, value in application.items()
                                        if key.startswith(("INSERT ", "UPDATE ", "DELETE "))),
                "observer_statements": sum(value for key, value in self.statements.items()
                                           if key.startswith("observer ")),
                "affected_write_rows": dict(self.affected_writes),
                "fetched_rows": sum(value for key, value in self.materialized.items()
                                    if not key.startswith("observer ")),
                "fetched_by_query": dict(self.materialized),
                "statements_by_query": dict(self.statements),
                "event_inserts_by_type": dict(self.event_types),
                "sql_execute_seconds_including_observer": round(self.sql_seconds, 6),
                "sql_failures": self.failures,
                "store_method_calls": dict(self.method_calls),
                **({"first_delta_profile_spans": self.profile_spans} if PROFILE_FIRST_DELTA else {}),
            }


class CountingCursor(psycopg.Cursor):
    """Instrument the real driver cursor without changing SQL/results/transactions."""

    def execute(self, query, params=None, *, prepare=None, binary=None):
        deadline = STAGE_DEADLINE.get()
        if COUNTERS is not None and COUNTERS.enabled and deadline is not None and time.perf_counter() >= deadline:
            raise TimeoutError("Synthetic measurement stage deadline reached")
        statement = query if isinstance(query, str) else query.as_string(self.connection)
        started = time.perf_counter()
        failed = True
        self._benchmark_key = None
        try:
            result = super().execute(query, params, prepare=prepare, binary=binary)
            failed = False
            return result
        finally:
            if COUNTERS is not None:
                self._benchmark_key = COUNTERS.statement(
                    statement, params, time.perf_counter() - started, self.rowcount, failed=failed,
                )
                COUNTERS.span(f"sql.{self._benchmark_key}", started)

    def fetchone(self):
        row = super().fetchone()
        if COUNTERS is not None:
            COUNTERS.rows(getattr(self, "_benchmark_key", None), int(row is not None))
        return row

    def fetchall(self):
        rows = super().fetchall()
        if COUNTERS is not None:
            COUNTERS.rows(getattr(self, "_benchmark_key", None), len(rows))
        return rows

    def __next__(self):
        row = super().__next__()
        if COUNTERS is not None:
            COUNTERS.rows(getattr(self, "_benchmark_key", None), 1)
        return row


class CountingConnection(psycopg.Connection):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.cursor_factory = CountingCursor


def verify_database(raw: str) -> tuple[str, dict]:
    """Require an explicitly named disposable loopback PG17 database."""
    if not raw:
        raise ValueError("TEST_POSTGRES_DSN is required; no .env is read")
    values = conninfo_to_dict(raw)
    name = values.get("dbname", "")
    if (values.get("host") not in {"127.0.0.1", "localhost", "::1"}
            or values.get("hostaddr", "") not in {"", "127.0.0.1", "::1"}
            or any(key in values for key in ("service", "passfile", "sslkey", "sslcert", "sslrootcert"))
            or not (name == "pskit_remediation" or name.startswith("pskit_benchmark_"))):
        raise ValueError("Only an explicit disposable loopback remediation/benchmark database is allowed")
    # Pin the address and disregard inherited libpq service/address options.
    values["hostaddr"] = "::1" if values["host"] == "::1" else "127.0.0.1"
    values["connect_timeout"] = "5"
    values["application_name"] = "pskit-local-synthetic-benchmark"
    values["options"] = "-c statement_timeout=5000 -c lock_timeout=3000"
    dsn = make_conninfo(**values)
    with psycopg.connect(dsn) as connection:
        version = int(connection.execute("SHOW server_version_num").fetchone()[0])
        if not 170000 <= version < 180000:
            raise ValueError("PostgreSQL 17 is required")
        info = {
            "server_version_num": version,
            "server_version": connection.execute("SHOW server_version").fetchone()[0],
            "database": name,
            "address": values["hostaddr"],
            "port": int(values.get("port", "5432")),
        }
    return dsn, info


@contextmanager
def observer():
    token = OBSERVER.set(True)
    try:
        yield
    finally:
        OBSERVER.reset(token)


@contextmanager
def isolated_store(dsn: str):
    """Migrate a unique schema; cleanup is limited to exactly this generated ID."""
    from app.db.postgres import PostgresDatabase
    from app.db.postgres_migrations import migrate_postgres
    from app.domain.persistent_conversation import PersistentConversationStore

    schema = f"pskit_benchmark_{uuid.uuid4().hex}"
    database = None
    try:
        migrate_postgres(dsn, schema=schema)
        if ADMISSION_ENABLED:
            from app.db.release_admission_migrations import migrate_admission_postgres

            # Explicit DDL only in this harness's disposable schema. Runtime
            # configure binds the component and never performs migrations.
            migrate_admission_postgres(dsn, schema=schema)
        database = PostgresDatabase(dsn, schema=schema)
        gate = configure_admission_gate(database)
        yield database, PersistentConversationStore(
            database, **({"admission_gate": gate} if ADMISSION_ENABLED else {}),
        ), schema
    finally:
        if COUNTERS is not None:
            COUNTERS.enabled = False
        if database is not None:
            database.close()
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def configure_admission_gate(database):
    """Use the actual open durable gate with an explicit synthetic deployment binding."""
    if not ADMISSION_ENABLED:
        return None
    from app.domain.release_admission import DurableAdmissionGate

    gate = DurableAdmissionGate(database, environment="staging", project="pskit-agent-staging",
                                qualification_sha256="0" * 64)
    gate.configure()
    return gate


def instrument_store(store, counters: SqlCounters) -> None:
    """Observe method counts and durable delta acknowledgments on the facade."""
    for name in ("claim_queued_runs", "claim_wakeups", "recover_wakeups", "renew_leases",
                 "expire_queued_compute_jobs", "expire_running_compute_jobs"):
        original = getattr(store, name)

        def counted(*args, _original=original, _name=name, **kwargs):
            if counters.enabled:
                counters.method_calls[_name] += 1
            return _original(*args, **kwargs)

        setattr(store, name, counted)
    if hasattr(store, "append_events"):
        original_batch = store.append_events

        def appended_batch(user_id, run_id, events, **kwargs):
            result = original_batch(user_id, run_id, events, **kwargs)
            if result and counters.enabled and any(event.type == "message.delta" for event in events):
                # Observe successful transaction commit, never Pi emit or buffer entry.
                counters.first_delta.setdefault(run_id, time.perf_counter())
            return result

        store.append_events = appended_batch
        return

    original_append = store.append_event

    def appended(user_id, run_id, event):
        result = original_append(user_id, run_id, event)
        if event.type == "message.delta" and counters.enabled:
            # append_event's transaction has committed when the call returns.
            counters.first_delta.setdefault(run_id, time.perf_counter())
        return result

    store.append_event = appended


class SyntheticPi:
    """Use Pi callback shape; no provider/RPC/HTTP/transcript work is simulated as real."""

    def __init__(self, counters: SqlCounters, *, deltas: int, delay_ms: float) -> None:
        self.counters, self.deltas, self.delay_ms = counters, deltas, delay_ms
        self.order: list[tuple[str, str]] = []

    async def prompt(self, session_id, message, on_event, **kwargs):
        run_id = kwargs["environment"]["PSKIT_RUN_ID"]
        user_id = kwargs["environment"]["PSKIT_USER_ID"]
        self.order.append((run_id, user_id))
        await asyncio.sleep(self.delay_ms / 1000)
        await on_event({"type": "message_start", "message": {"role": "assistant"}})
        for index in range(self.deltas):
            self.counters.first_emit.setdefault(run_id, time.perf_counter())
            await on_event({"type": "message_update", "assistantMessageEvent": {
                "type": "text_delta", "delta": f"delta-{index} ",
            }})
        await on_event({"type": "message_end", "message": {"role": "assistant",
                        "usage": {"totalTokens": 1}}})
        return {"text": "".join(f"delta-{index} " for index in range(self.deltas)),
                "session_file": f"/tmp/pskit-benchmark-transcripts/{session_id}.jsonl"}


def make_service(database, store, counters, *, deltas: int = 1, delay_ms: float = 5):
    from app.domain.compute.jobs import ComputeJobs
    from app.domain.compute.leases import ComputeLeases
    from app.domain.compute.ledger import ComputeLedger
    from app.services.agent import AgentService

    instrument_store(store, counters)
    runner = SyntheticPi(counters, deltas=deltas, delay_ms=delay_ms)
    service = AgentService(store, runner, "http://127.0.0.1/benchmark-not-called", 1, 0.1,
                           af3_executor="callback", max_active_runs=4, max_active_runs_per_user=2)
    gate = {"admission_gate": store.admission_gate} if ADMISSION_ENABLED else {}
    service.compute_jobs = ComputeJobs(database, ComputeLedger(database), **gate)
    service.compute_leases = ComputeLeases(database, service.compute_jobs.ledger, **gate)
    if PROFILE_FIRST_DELTA:
        instrument_pipeline(service, store, counters)
    return service, runner


def instrument_pipeline(service, store, counters: SqlCounters) -> None:
    """Opt-in spans around unchanged real calls; no SQL/argument/result recording."""
    for name in ("project_for", "create_session", "accept_message", "owns_lease", "start_pi_turn",
                 "run_context", "session_file_for", "append_event", "append_events"):
        original = getattr(store, name, None)
        if original is None:
            continue

        @functools.wraps(original)
        def traced(*args, _original=original, _name=name, **kwargs):
            started = time.perf_counter()
            try:
                return _original(*args, **kwargs)
            finally:
                counters.span(f"store.{_name}", started)

        setattr(store, name, traced)
    execute = service._execute

    async def traced_execute(user_id, session_id, run_id, content):
        token = PROFILE_SAMPLE.set(counters.profile_runs.get(run_id))
        started = time.perf_counter()
        try:
            counters.span("execute.enter", started, started)
            return await execute(user_id, session_id, run_id, content)
        finally:
            counters.span("execute.total", started)
            PROFILE_SAMPLE.reset(token)

    service._execute = traced_execute
    db_call = getattr(service, "_db_call", None)
    if db_call is None:
        return

    async def traced_db_call(method, *args, **kwargs):
        name = getattr(method, "__name__", type(method).__name__)
        started = time.perf_counter()

        @functools.wraps(method)
        def worker(*worker_args, **worker_kwargs):
            worker_started = time.perf_counter()
            try:
                return method(*worker_args, **worker_kwargs)
            finally:
                counters.span(f"worker.{name}", worker_started)

        try:
            return await db_call(worker, *args, **kwargs)
        finally:
            counters.span(f"db_call.{name}", started)

    service._db_call = traced_db_call


def admit(store, user_id: str):
    """Use real Session creation and quota/idempotency admission, not direct queue inserts."""
    session = store.create_session(user_id, store.project_for(user_id).id, "Synthetic benchmark")
    if session is None:
        raise RuntimeError("Synthetic session creation failed")
    from app.contracts.conversation import MessageRequest

    accepted = store.accept_message(
        user_id, session.id, MessageRequest(content="Synthetic benchmark"), None, 1,
        None, "synthetic-benchmark", user_prompt="Synthetic benchmark",
    )
    if accepted is None:
        raise RuntimeError("Synthetic admission failed")
    return accepted[0].run_id, session.id


def scheduler_error(service) -> None:
    if service._scheduler is not None and service._scheduler.done():
        error = service._scheduler.exception()
        raise RuntimeError("Scheduler unexpectedly exited") from error


async def idle_benchmark(dsn: str, seconds: float) -> dict:
    global COUNTERS
    with isolated_store(dsn) as (database, store, schema):
        counters = SqlCounters()
        COUNTERS = counters
        service, _ = make_service(database, store, counters)
        await service.start()
        counters.enabled = True
        started = time.perf_counter()
        cpu_started = resource.getrusage(resource.RUSAGE_SELF)
        try:
            while time.perf_counter() - started < seconds:
                await asyncio.sleep(min(30, seconds - (time.perf_counter() - started)))
                scheduler_error(service)
                print(f"idle elapsed={time.perf_counter() - started:.1f}s "
                      f"sql={counters.snapshot()['sql_statements']}", flush=True)
        finally:
            counters.enabled = False
            await service.stop()
        cpu_finished = resource.getrusage(resource.RUSAGE_SELF)
        elapsed = time.perf_counter() - started
        return {
            "status": "passed", "schema": schema, "elapsed_seconds": round(elapsed, 3),
            "five_minute_gate": seconds >= 300 and elapsed >= 300,
            "process_cpu_user_seconds": round(cpu_finished.ru_utime - cpu_started.ru_utime, 6),
            "process_cpu_system_seconds": round(cpu_finished.ru_stime - cpu_started.ru_stime, 6),
            "sql_statements_per_second": round(counters.snapshot()["sql_statements"] / elapsed, 3),
            "max_rss_kib_process": cpu_finished.ru_maxrss,
            "measurement": counters.snapshot(),
        }


def first_delta_samples(counters: SqlCounters) -> list[dict]:
    """Keep every raw committed sample in completion order without Run IDs."""
    return [
        {"sample": index,
         "admission_to_first_durable_delta": (at - counters.started[run_id]) * 1000,
         "emission_to_first_durable_delta": (at - counters.first_emit[run_id]) * 1000}
        for index, (run_id, at) in enumerate(counters.first_delta.items())
    ]


async def latency_benchmark(dsn: str, *, samples: int, deltas: int, name: str) -> dict:
    global COUNTERS
    with isolated_store(dsn) as (database, store, schema):
        counters = SqlCounters()
        COUNTERS = counters
        service, _ = make_service(database, store, counters, deltas=deltas)
        counters.enabled = True
        started = time.perf_counter()
        await service.start()
        try:
            for index in range(samples):
                ingress = time.perf_counter()
                counters.profile_started[index] = ingress
                token = PROFILE_SAMPLE.set(index) if PROFILE_FIRST_DELTA else None
                run_id, session_id = admit(store, f"latency-{index}")
                counters.span("admit", ingress)
                counters.profile_runs[run_id] = index
                counters.started[run_id] = ingress
                schedule_started = time.perf_counter()
                service.schedule(f"latency-{index}", session_id, run_id, "Synthetic benchmark")
                counters.span("schedule", schedule_started)
                if token is not None:
                    PROFILE_SAMPLE.reset(token)
                task = service._run_tasks.get(run_id)
                # schedule is a nonblocking admission hint. A repository worker
                # may finish its lease claim after this call; never require
                # synchronous database I/O just to satisfy the observer.
                claim_deadline = time.perf_counter() + 10
                while task is None and time.perf_counter() < claim_deadline:
                    scheduler_error(service)
                    # The committed-delta observer and task table are in-memory;
                    # querying status every millisecond would block this loop
                    # and add observer latency to the quantity being measured.
                    if run_id in counters.first_delta:
                        break
                    await asyncio.sleep(0.001)
                    task = service._run_tasks.get(run_id)
                if task is not None:
                    await asyncio.wait_for(task, timeout=10)
                with observer():
                    if store.run_status_for(f"latency-{index}", run_id).status != "completed":
                        raise RuntimeError("Synthetic turn did not complete")
                scheduler_error(service)
        finally:
            counters.enabled = False
            await service.stop()
        if len(counters.first_delta) != samples:
            raise RuntimeError("Not every run persisted a first delta")
        return {
            "status": "passed", "name": name, "schema": schema,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "samples": samples, "synthetic_deltas_per_turn": deltas,
            "synthetic_pi_delay_ms": 5,
            "admission_to_first_durable_delta": latency_summary([
                (at - counters.started[run_id]) * 1000 for run_id, at in counters.first_delta.items()
            ]),
            "emission_to_first_durable_delta": latency_summary([
                (at - counters.first_emit[run_id]) * 1000 for run_id, at in counters.first_delta.items()
            ]),
            "latency_samples_ms": first_delta_samples(counters),
            "retained_session_locks_after_turns": len(service._locks),
            "measurement": counters.snapshot(),
        }


async def fairness_benchmark(dsn: str, size: int) -> dict:
    """Keep Alice's two slots occupied; Bob is placed after her entire queued prefix."""
    global COUNTERS
    with isolated_store(dsn) as (_database, store, schema):
        counters = SqlCounters()
        COUNTERS = counters
        blockers = [admit(store, "blocked-alice")[0] for _ in range(2)]
        for run_id in blockers:
            if not store.claim_initial_run(run_id, "blocker", lease_seconds=600):
                raise RuntimeError("Could not fill Alice's two active slots")
        for _ in range(size - 1):
            admit(store, "blocked-alice")
        bob, _ = admit(store, "tail-bob")
        counters.enabled = True
        started = time.perf_counter()
        calls = 0
        claimed = []
        # A bounded cursor implementation can yield between windows; the tail user
        # must eventually be admitted, not necessarily in its first candidate window.
        while not claimed:
            calls += 1
            claimed = store.claim_queued_runs("fairness", max_active=4, max_user_active=2)
            if time.perf_counter() - started >= 10:
                raise TimeoutError("Tail user was not admitted within the fairness deadline")
            if not claimed:
                await asyncio.sleep(0)
        elapsed = time.perf_counter() - started
        counters.enabled = False
        if [row[0] for row in claimed] != [bob]:
            raise RuntimeError("Tail user was not fairly admitted while Alice remained blocked")
        return {"status": "passed", "schema": schema, "queued_runs": size,
                "blocked_prefix": size - 1, "claimed_tail_user": True,
                "claim_elapsed_ms": round(elapsed * 1000, 3), "claim_calls": calls,
                "measurement": counters.snapshot()}


async def queue_benchmark(dsn: str, size: int, deadline: float) -> dict:
    global COUNTERS
    with isolated_store(dsn) as (database, store, schema):
        counters = SqlCounters()
        COUNTERS = counters
        service, runner = make_service(database, store, counters)
        setup_started = time.perf_counter()
        owners = {}
        # Round-robin owners make normal throughput comparable; fairness uses a blocked prefix separately.
        for index in range(size):
            owner = f"queue-user-{index % 10}"
            run_id, _ = admit(store, owner)
            owners[run_id] = owner
        setup_seconds = time.perf_counter() - setup_started
        started = time.perf_counter()
        counters.started = {run_id: started for run_id in owners}
        counters.enabled = True
        await service.start()
        try:
            while True:
                scheduler_error(service)
                with observer(), database.connection() as connection:
                    states = Counter(status for (status,) in connection.execute("SELECT status FROM agent_runs"))
                if states.get("failed"):
                    raise RuntimeError("Synthetic queue contains a failed run")
                if states.get("completed", 0) == size:
                    break
                if time.perf_counter() - started >= deadline:
                    # A large legacy queue may not drain within the allotted window.
                    # Preserve measured counters/latencies as a bounded observation.
                    break
                await asyncio.sleep(0.1)
        finally:
            counters.enabled = False
            await service.stop()
        elapsed = time.perf_counter() - started
        completed = states.get("completed", 0)
        drained = completed == size
        if drained and len(counters.first_delta) != size:
            raise RuntimeError("Queued runs completed without durable first-delta evidence")
        first_by_user = {}
        counts = Counter(owner for _, owner in runner.order)
        for run_id, at in counters.first_delta.items():
            first_by_user.setdefault(owners[run_id], round((at - started) * 1000, 3))
        return {
            "status": "passed" if drained else "bounded_observation", "schema": schema, "queued_runs": size,
            "queue_drained": drained, "completed_runs": completed, "first_delta_runs": len(counters.first_delta),
            "owners": 10, "ingress_owner_order": "round-robin",
            "setup_seconds_excluded": round(setup_seconds, 3), "elapsed_seconds": round(elapsed, 3),
            "completed_per_second": round(completed / elapsed, 3),
            "queue_start_to_first_durable_delta": latency_summary([
                (at - started) * 1000 for at in counters.first_delta.values()
            ]),
            "started_turns_by_user": dict(counts), "first_delta_ms_by_user": first_by_user,
            "retained_session_locks_after_turns": len(service._locks), "measurement": counters.snapshot(),
        }


def write_report(path: Path, report: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def freeze_source(destination: Path, source_ref: str | None) -> str:
    """Freeze the working app or an exact local Git commit without checking out files."""
    if source_ref is None:
        shutil.copytree(ROOT / "apps/api/app", destination / "app",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".env", ".env.*"))
        return subprocess.run(["/usr/bin/git", "-C", str(ROOT), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=True, timeout=10).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{7,40}", source_ref):
        raise ValueError("source-ref must be a local commit SHA, not a branch or executable option")
    revision = subprocess.run(["/usr/bin/git", "-C", str(ROOT), "rev-parse", "--verify", f"{source_ref}^{{commit}}"],
                              capture_output=True, text=True, check=True, timeout=10).stdout.strip()
    archive = subprocess.run(["/usr/bin/git", "-C", str(ROOT), "archive", revision, "apps/api/app"],
                             capture_output=True, check=True, timeout=30).stdout
    if len(archive) > 20 * 1024 * 1024:
        raise ValueError("App snapshot unexpectedly exceeds the 20MiB source limit")
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        for member in bundle:
            if member.isdir():
                continue
            parts = Path(member.name).parts
            if (not member.isfile() or parts[:3] != ("apps", "api", "app")
                    or len(parts) <= 3 or ".." in parts):
                raise ValueError("App Git archive contains unsupported entries")
            if member.name.endswith((".pyc", "/.env")) or "/.env." in member.name:
                raise ValueError("App Git archive contains private/generated files")
            path = destination.joinpath("app", *parts[3:])
            path.parent.mkdir(parents=True, exist_ok=True)
            with bundle.extractfile(member) as incoming, path.open("wb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
    return revision


async def run(args, dsn: str, report: dict, output: Path) -> None:
    stages = []
    if "idle" in args.stages:
        stages.append(("idle", lambda: idle_benchmark(dsn, args.idle_seconds), args.idle_seconds + 30))
    if "fairness" in args.stages:
        stages.extend((f"fairness_{size}", lambda size=size: fairness_benchmark(dsn, size), args.stage_timeout)
                      for size in args.queue_sizes)
    if "queue" in args.stages:
        stages.extend((f"queue_{size}", lambda size=size: queue_benchmark(dsn, size, args.stage_timeout - 10),
                       args.stage_timeout) for size in args.queue_sizes)
    if "first_delta" in args.stages:
        stages.append(("first_delta", lambda: latency_benchmark(dsn, samples=args.samples, deltas=8, name="first_delta"), args.stage_timeout))
    if "delta_writes" in args.stages:
        stages.append(("delta_writes", lambda: latency_benchmark(dsn, samples=1, deltas=256, name="delta_writes"), args.stage_timeout))
    for name, execute, deadline in stages:
        print(f"start stage={name} deadline={deadline:.0f}s at={utc_now()}", flush=True)
        token = STAGE_DEADLINE.set(time.perf_counter() + deadline)
        try:
            result = await asyncio.wait_for(execute(), timeout=deadline)
            report["stages"][name] = result
        except Exception as error:
            # Error type is enough for the persisted report; never emit raw DSN/provider data.
            report["stages"][name] = {"status": "failed", "error_type": type(error).__name__}
            report["status"] = "failed"
            write_report(output, report)
            raise
        finally:
            STAGE_DEADLINE.reset(token)
        write_report(output, report)
        print(f"finish stage={name} sql={result['measurement']['sql_statements']}", flush=True)
    report["status"] = "partial" if any(result["status"] != "passed" for result in report["stages"].values()) else "passed"
    report["finished_at"] = utc_now()
    write_report(output, report)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-ref", help="Exact local Git commit SHA for reproducible pre-optimization measurements")
    parser.add_argument("--idle-seconds", type=float, default=300,
                        help="300 required for full idle gate; shorter runs are smoke only")
    parser.add_argument("--queue-sizes", type=int, nargs="+", default=[100, 1000])
    parser.add_argument("--samples", type=int, default=50)
    parser.add_argument("--stage-timeout", type=float, default=180)
    parser.add_argument("--profile-first-delta", action="store_true",
                        help="Opt-in method/worker/SQL spans; profile overhead is not an uninstrumented baseline")
    parser.add_argument("--admission-enabled", action="store_true",
                        help="Explicitly install/configure the real durable gate in this disposable schema")
    parser.add_argument("--stages", nargs="+", choices=("idle", "fairness", "queue", "first_delta", "delta_writes"),
                        default=["idle", "fairness", "queue", "first_delta", "delta_writes"],
                        help="Repeat selected stages without fabricating or re-running completed evidence")
    args = parser.parse_args()
    global PROFILE_FIRST_DELTA, ADMISSION_ENABLED
    PROFILE_FIRST_DELTA = args.profile_first_delta
    ADMISSION_ENABLED = args.admission_enabled
    if not 0 <= args.idle_seconds <= 600 or any(not 1 <= size <= 1000 for size in args.queue_sizes):
        parser.error("Idle must be 0..600 seconds and queue sizes 1..1000")
    if not 1 <= args.samples <= 100 or not 15 <= args.stage_timeout <= 600:
        parser.error("Samples must be 1..100 and stage timeout 15..600 seconds")
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    raw_dsn = os.environ.get("TEST_POSTGRES_DSN", "")
    # No inherited application credentials, HOME configuration, or HTTP/libpq proxies.
    with tempfile.TemporaryDirectory(prefix="pskit-new-stack-benchmark-") as temporary:
        isolated = Path(temporary)
        os.environ.clear()
        os.environ.update(HOME=str(isolated), TMPDIR=str(isolated), XDG_CACHE_HOME=str(isolated / "cache"),
                          RESEARCH_AGENT_MODE="mock", RESEARCH_AGENT_RUNTIME="mock")
        dsn, pg_info = verify_database(raw_dsn)
        source = isolated / "source"
        revision = freeze_source(source, args.source_ref)
        source_hashes = {
            name: hashlib.sha256((source / name).read_bytes()).hexdigest()
            for name in SOURCE_FILES if (source / name).is_file()
        }
        tree_hashes = {
            str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(source.rglob("*")) if path.is_file()
        }
        sys.path.insert(0, str(source))
        os.chdir(isolated)
        import app.db.postgres as pg_module

        pg_module.ConnectionPool = functools.partial(pg_module.ConnectionPool, connection_class=CountingConnection)
        report = {
            "format": "pskit.new-stack-benchmark.v1", "status": "running", "started_at": utc_now(),
            "harness_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "source_revision": revision, "runtime_source_sha256": source_hashes,
            "app_tree_sha256": hashlib.sha256(json.dumps(tree_hashes, sort_keys=True).encode()).hexdigest(),
            "source_kind": "local-git-commit" if args.source_ref else "working-tree-snapshot",
            "source_snapshot": "only apps/api/app, frozen before measurements; no .env/data/credentials",
            "machine": {"platform": platform.platform(), "python": platform.python_version(),
                        "cpu_count": os.cpu_count(), "psycopg": psycopg.__version__},
            "postgres": pg_info, "parameters": {"idle_seconds": args.idle_seconds,
                "queue_sizes": args.queue_sizes, "first_delta_samples": args.samples,
                "stages": args.stages,
                "profile_first_delta": args.profile_first_delta,
                "admission_enabled": args.admission_enabled,
                "stage_timeout_seconds": args.stage_timeout, "global_active": 4,
                "per_user_active": 2, "pool_max_size": 10, "statement_timeout_ms": 5000,
                "lock_timeout_ms": 3000},
            "counter_definition": "real driver Cursor.execute calls; explicit SQL only, not implicit BEGIN/COMMIT; "
                "SELECT includes admission advisory locks; write statements may affect zero rows; "
                "observer queries separately counted; no SQL parameters/results retained",
            "latency_definition": "local synthetic Pi delay 5ms + actual admission/scheduler/event commits; "
                "not provider latency, HTTP TTFB, network SSE, browser paint, or transcript write throughput",
            "concurrent_activity": "shared local PG17 may run other synthetic test schemas; host CPU/network noise uncontrolled",
            "cleanup": "each stage closes its pool and drops only its generated pskit_benchmark_<uuid> schema",
            "stages": {},
        }
        write_report(output, report)
        asyncio.run(run(args, dsn, report, output))
        print(f"report={output}", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001 - CLI boundary redacts connection errors.
        # libpq parsing/authentication exceptions can echo supplied DSN fragments.
        # Stage reports preserve the error type without copying credentials to logs.
        print(f"Benchmark failed: {type(error).__name__}; inspect the stage report.", file=sys.stderr)
        raise SystemExit(1) from None
