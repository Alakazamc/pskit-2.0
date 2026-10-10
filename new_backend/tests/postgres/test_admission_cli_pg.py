"""Real private HTTP + CLI process + disposable PG admission ownership."""

import asyncio
import json
import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.db.postgres_migrations import migrate_postgres
from app.db.release_admission_migrations import migrate_admission_postgres
from app.main import create_app

ROOT = Path(__file__).resolve().parents[3]
QUALIFICATION = "a" * 64
KEY = "synthetic-admission-key-never-log-" + "k" * 32


def command(operation, **changes):
    return {
        "schema_version": "pskit.admission-command.v1", "operation": operation,
        "adapter_id": "synthetic-adapter", "qualification_sha256": QUALIFICATION,
        "owner_nonce": "b" * 64, "environment": "staging", "project": "pskit-agent-staging",
        "previous_manifest_sha256": "c" * 64, "target_manifest_sha256": "d" * 64,
        "scopes": ["agent_runs", "compute_jobs", "workspace_attempts", "scheduler_claims"],
        **changes,
    }


@contextmanager
def private_http(app, stall=None):
    """Expose the actual app handler through a loopback-only test bridge."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            async def dispatch():
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://private.test",
                ) as client:
                    return await client.get(self.path, headers={
                        "Authorization": self.headers.get("Authorization", ""),
                    })

            response = asyncio.run(dispatch())
            if stall is not None and response.status_code == 200 and response.json().get("state") == "closed":
                stall[0].set()
                stall[1].wait(timeout=10)
            self.send_response(response.status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response.content)))
            self.end_headers()
            try:
                self.wfile.write(response.content)
            except BrokenPipeError:
                pass  # A killed controller must not reopen the durable hold.

        def log_message(self, _format, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def invoke(config, operation, **changes):
    return subprocess.run(
        [sys.executable, str(ROOT / "deploy/agent/scripts/admission_adapter.py"),
         operation, "--config", str(config)],
        input=json.dumps(command(operation, **changes)), capture_output=True,
        text=True, timeout=15, check=False,
        env={key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ},
    )


def make_config(tmp_path, dsn, schema, backend_url, **changes):
    private = tmp_path / "private"
    private.mkdir(mode=0o700, exist_ok=True)
    path = private / "admission.json"
    path.write_text(json.dumps({
        "schema_version": "pskit.admission-adapter.v1", "database_url": dsn,
        "database_schema": schema, "backend_url": backend_url, "backend_token": KEY,
        "adapter_id": "synthetic-adapter", "qualification_sha256": QUALIFICATION,
        "environment": "staging", "project": "pskit-agent-staging", **changes,
    }))
    path.chmod(0o600)
    return path


class NoModelCalls:
    async def prompt(self, *_args, **_kwargs):
        raise AssertionError("The private observation cannot invoke a model")

    async def resume(self, *_args, **_kwargs):
        raise AssertionError("The private observation cannot invoke a model")


def make_app(dsn, schema):
    migrate_admission_postgres(dsn, schema=schema)
    return create_app(Settings(
        mode="live", agent_runtime="pi", database_url=dsn, database_schema=schema,
        auth_csrf_secret="synthetic-csrf-secret-" + "s" * 32,
        supabase_url="https://identity.example", supabase_publishable_key="synthetic-public",
        model_gateway_base_url="https://models.example/v1", model_gateway_model="synthetic",
        mcp_executor="disabled", af3_executor="disabled", release_admission_enabled=True,
        release_admission_environment="staging", release_admission_project="pskit-agent-staging",
        release_admission_key=KEY, release_admission_qualification_sha256=QUALIFICATION,
    ), pi_runner=NoModelCalls())


def test_real_cli_hold_survives_backend_recreation_and_owner_mismatch(pg_schema, tmp_path):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    first = make_app(dsn, schema)
    try:
        with private_http(first) as url:
            path = make_config(tmp_path, dsn, schema, url)
            entered = invoke(path, "enter")
            assert entered.returncode == 0, entered.stderr
            assert json.loads(entered.stdout)["state"] == "closed"
            wrong = invoke(path, "release", owner_nonce="f" * 64)
            assert wrong.returncode != 0
            assert KEY not in wrong.stderr + wrong.stdout
            assert invoke(path, "verify").returncode == 0
    finally:
        first.state.database.close()

    second = make_app(dsn, schema)
    try:
        with private_http(second) as url:
            path = make_config(tmp_path, dsn, schema, url)
            assert invoke(path, "verify").returncode == 0
            released = invoke(path, "release")
            assert released.returncode == 0, released.stderr
            assert json.loads(released.stdout)["state"] == "released"
            assert invoke(path, "enter").returncode == 0
    finally:
        second.state.database.close()


@pytest.mark.parametrize("changes", [{"backend_token": "incorrect"}, {"database_schema": "public"}])
def test_real_cli_refuses_unverified_backend_without_changing_gate(pg_schema, tmp_path, changes):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    app = make_app(dsn, schema)
    try:
        with private_http(app) as url:
            path = make_config(tmp_path, dsn, schema, url, **changes)
            result = invoke(path, "enter")
            assert result.returncode != 0
            assert KEY not in result.stderr + result.stdout
            proof = app.state.admission_gate.observe("e" * 64)
            assert proof["state"] == "open"
    finally:
        app.state.database.close()


def test_cli_killed_after_db_close_before_ack_keeps_durable_hold(pg_schema, tmp_path):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    app = make_app(dsn, schema)
    observed, resume = threading.Event(), threading.Event()
    process = None
    try:
        with private_http(app, (observed, resume)) as url:
            path = make_config(tmp_path, dsn, schema, url)
            process = subprocess.Popen(
                [sys.executable, str(ROOT / "deploy/agent/scripts/admission_adapter.py"),
                 "enter", "--config", str(path)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env={key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ},
            )
            process.stdin.write(json.dumps(command("enter")))
            process.stdin.close()
            assert observed.wait(timeout=8), "CLI did not reach the post-close observation"
            assert process.poll() is None
            process.kill()
            process.wait(timeout=5)
            assert app.state.admission_gate.observe("e" * 64)["state"] == "closed"
            resume.set()
        with private_http(app) as url:
            path = make_config(tmp_path, dsn, schema, url)
            assert invoke(path, "verify").returncode == 0
            assert invoke(path, "enter").returncode == 0
            assert invoke(path, "release").returncode == 0
    finally:
        resume.set()
        if process is not None:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        app.state.database.close()
