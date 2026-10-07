"""Run the generic MCP compute receiver without importing provider-owned code."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from app.contracts.compute import ComputeClaimRequest, WorkerResources
from app.domain.errors import ErrorCode
from app.services.structured_logging import compute_logger
from pskit_compute.dynamic_mcp import DynamicMcpExecutor
from pskit_compute.journal import Journal
from pskit_compute.receiver import ControlClient, Receiver
from pskit_compute.service import ProtocolError

LOG = logging.getLogger("mcp_compute_receiver")
SAFE_ERROR_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,99}\Z")


def _safe_error_code(error: Exception) -> str:
    if isinstance(error, ProtocolError):
        try:
            value = str(error)
        except Exception:  # noqa: BLE001 - Logging must not fail on exception rendering.
            return "ProtocolError"
        if SAFE_ERROR_CODE.fullmatch(value):
            return value
        return "ProtocolError"
    return type(error).__name__


def _string_map(raw: str, name: str) -> dict[str, str]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name}_INVALID") from exc
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not key
        or not isinstance(item, str) or not item
        for key, item in value.items()
    ):
        raise ValueError(f"{name}_INVALID")
    return value


def configured_resolvers(environ=None):
    """Build exact local routing and secret-reference resolvers from environment data."""
    environ = os.environ if environ is None else environ
    endpoints = _string_map(
        environ.get("PSKIT_MCP_ENDPOINT_OVERRIDES_JSON", "{}"),
        "ENDPOINT_OVERRIDES",
    )
    credential_names = _string_map(
        environ.get("PSKIT_MCP_CREDENTIAL_REFS_JSON", "{}"),
        "CREDENTIAL_REFS",
    )

    def endpoint(value: str) -> str:
        return endpoints.get(value, value)

    def credential(reference: str) -> str:
        name = credential_names.get(reference)
        if not name:
            raise ValueError("CREDENTIAL_REF_NOT_CONFIGURED")
        value = environ.get(name, "")
        if not value:
            raise ValueError("CREDENTIAL_VALUE_UNAVAILABLE")
        return value

    return endpoint, credential


async def receive(receiver, poll_seconds, *, cleanup=None, isolate_failures=False):
    """Run one shared receiver without removing unacknowledged model outcomes."""
    last_status = None
    while True:
        try:
            outcome = await receiver.run_once()
        except (httpx.HTTPError, OSError) as error:
            LOG.warning("%s: control unavailable (%s); journal retained",
                        receiver.identity.service_id, type(error).__name__)
            compute_logger.warning(
                "Control server unavailable, retaining journal",
                service_id=receiver.identity.service_id,
                error_type=type(error).__name__
            )
            await asyncio.sleep(max(1, poll_seconds))
            continue
        except Exception as error:
            # Isolate services while retaining the journal and the original failure state.
            if not isolate_failures:
                raise
            error_code = _safe_error_code(error)
            LOG.error("%s: requires reconciliation (%s); other services continue",
                      receiver.identity.service_id, error_code)
            compute_logger.error(
                "Service requires reconciliation, other services continue",
                service_id=receiver.identity.service_id,
                error_code=error_code,
                exc_info=True
            )
            await asyncio.sleep(30)
            continue
        if outcome.status == "unknown":
            if not isolate_failures:
                raise RuntimeError("Execution outcome unknown; retain journal for operator reconciliation")
            if last_status != "unknown":
                LOG.error("%s: execution unknown; journal retained, other services continue",
                          receiver.identity.service_id)
            last_status = "unknown"
            await asyncio.sleep(max(1, poll_seconds))
            continue
        if outcome.status == "acknowledged" and cleanup is not None:
            # Receiver.run_once has already verified and committed the journal ACK.
            try:
                cleanup(outcome.job_id)
            except OSError:
                LOG.warning("%s: acknowledged spool retained after cleanup failure", outcome.job_id)
        if outcome.status != last_status:
            LOG.info("%s: %s", receiver.identity.service_id, outcome.status)
            last_status = outcome.status
        await asyncio.sleep(poll_seconds)


@asynccontextmanager
async def local_af3_mcp(spool, port, *, host="127.0.0.1", token=""):
    """Host the AF3 protocol in this receiver process, bound only to loopback."""
    import uvicorn

    from pskit_compute.af3_entry import McpBearerAuth
    from pskit_compute.af3_spool import create_mcp

    app = create_mcp(spool, host=host, port=port).streamable_http_app()
    if token:
        app = McpBearerAuth(app, token)
    elif host != "127.0.0.1":
        raise ValueError("AF3_MCP_TOKEN_REQUIRED")

    server = uvicorn.Server(uvicorn.Config(
        app, host=host, port=port, log_level="warning", access_log=False,
    ))
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("AF3_MCP_START_FAILED")
                await asyncio.sleep(0.01)
        yield
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)


async def run(args) -> None:
    endpoint, credential = configured_resolvers()
    executor = DynamicMcpExecutor(endpoint, credential)
    if args.af3_backend_url and not args.af3_spool_dir:
        raise ValueError("AF3_SPOOL_DIR_REQUIRED")
    with ExitStack() as stack:
        journal = Journal(args.journal)
        stack.callback(journal.close)
        async with httpx.AsyncClient(timeout=30) as client:
            control = ControlClient(
                client,
                base_url=args.backend_url,
                service_id=args.service_id,
                service_key=os.environ["PSKIT_COMPUTE_SERVICE_KEY"],
            )
            receiver = Receiver(
                executor,
                control,
                journal,
                ComputeClaimRequest(
                    service_id=args.service_id,
                    worker_id=args.worker_id,
                    resources=WorkerResources(gpu_uuids=args.gpu_uuid),
                ),
            )
            if not args.af3_backend_url:
                await receive(receiver, args.poll_seconds)
                return
            from pskit_compute.af3_compat import Af3ControlClient, Af3McpExecutor
            from pskit_compute.af3_entry import Af3ReceiverLane
            from pskit_compute.af3_spool import Af3Spool

            spool = Af3Spool(args.af3_spool_dir)
            af3_journal = Journal(args.af3_journal or str(Path(args.journal).with_name("af3.sqlite3")))
            stack.callback(af3_journal.close)
            af3_control = Af3ControlClient(
                client, base_url=args.af3_backend_url,
                key=os.environ["RESEARCH_AGENT_COMPUTE_CALLBACK_KEY"], spool=spool,
                endpoint_url=f"http://{args.af3_mcp_host}:{args.af3_mcp_port}/mcp",
                gpu_memory_mb=args.af3_gpu_memory_mb,
                mcp_credential_ref="af3-mcp-key" if os.environ.get("PSKIT_AF3_MCP_TOKEN") else None,
            )
            af3_receiver = Receiver(
                Af3McpExecutor(endpoint, credential, spool=spool), af3_control, af3_journal,
                ComputeClaimRequest(service_id="af3-mcp", worker_id=args.af3_worker_id),
            )
            af3_lane = af3_receiver
            cleanup = af3_control.cleanup
            if generic_key := os.environ.get("PSKIT_AF3_COMPUTE_SERVICE_KEY"):
                if not args.af3_gpu_uuid:
                    raise ValueError("AF3_GPU_UUID_REQUIRED")
                product_journal = Journal(str(Path(args.journal).with_name("af3-product.sqlite3")))
                stack.callback(product_journal.close)
                product_control = ControlClient(client, base_url=args.backend_url,
                    service_id="af3-mcp", service_key=generic_key)
                product_receiver = Receiver(Af3McpExecutor(endpoint, credential, spool=spool),
                    product_control, product_journal,
                    ComputeClaimRequest(service_id="af3-mcp", worker_id="a6000-af3-product-1",
                                        resources=WorkerResources(gpu_uuids=args.af3_gpu_uuid)))

                def product_cleanup(job_id):
                    import shutil
                    directory = spool.directory(job_id)
                    if directory.exists():
                        shutil.rmtree(directory)

                af3_lane = Af3ReceiverLane(product_receiver, af3_receiver,
                    generic_cleanup=product_cleanup, compatibility_cleanup=cleanup)
                cleanup = None
            async with local_af3_mcp(spool, args.af3_mcp_port, host=args.af3_mcp_host,
                                    token=os.environ.get("PSKIT_AF3_MCP_TOKEN", "")), asyncio.TaskGroup() as group:
                group.create_task(receive(receiver, args.poll_seconds, isolate_failures=True))
                group.create_task(receive(af3_lane, args.poll_seconds,
                                          cleanup=cleanup, isolate_failures=True))


def argument_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("backend-url", "service-id", "worker-id", "journal"):
        parser.add_argument(f"--{option}", required=True)
    parser.add_argument("--gpu-uuid", action="append", default=[])
    parser.add_argument("--poll-seconds", type=float, default=2)
    parser.add_argument("--af3-backend-url")
    parser.add_argument("--af3-worker-id", default="a6000-af3-cloud-1")
    parser.add_argument("--af3-spool-dir")
    parser.add_argument("--af3-journal")
    parser.add_argument("--af3-mcp-port", type=int, default=18187)
    parser.add_argument("--af3-mcp-host", default="127.0.0.1")
    parser.add_argument("--af3-gpu-uuid", action="append", default=[])
    parser.add_argument("--af3-gpu-memory-mb", type=int, default=49140)
    return parser


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(run(argument_parser().parse_args()))
