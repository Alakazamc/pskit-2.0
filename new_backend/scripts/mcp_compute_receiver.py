"""Run the generic MCP compute receiver without importing provider-owned code."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from app.contracts.compute import ComputeClaimRequest, WorkerResources
from pskit_compute.dynamic_mcp import DynamicMcpExecutor
from pskit_compute.journal import Journal
from pskit_compute.receiver import ControlClient, Receiver


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


async def run(args) -> None:
    endpoint, credential = configured_resolvers()
    executor = DynamicMcpExecutor(endpoint, credential)
    journal = Journal(args.journal)
    try:
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
            while True:
                try:
                    outcome = await receiver.run_once()
                except (httpx.HTTPError, OSError):
                    await asyncio.sleep(max(1, args.poll_seconds))
                    continue
                if outcome.status == "unknown":
                    raise RuntimeError(
                        "Execution outcome unknown; retain journal for operator reconciliation"
                    )
                await asyncio.sleep(args.poll_seconds)
    finally:
        journal.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("backend-url", "service-id", "worker-id", "journal"):
        parser.add_argument(f"--{option}", required=True)
    parser.add_argument("--gpu-uuid", action="append", default=[])
    parser.add_argument("--poll-seconds", type=float, default=2)
    asyncio.run(run(parser.parse_args()))
