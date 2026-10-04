"""Start a trusted maintainer module's receiver; configuration never comes from users."""

import argparse
import asyncio
import importlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from app.contracts.compute import ComputeClaimRequest, WorkerResources
from pskit_compute.journal import Journal
from pskit_compute.receiver import ControlClient, Receiver


async def run(args):
    module, separator, name = args.executor.partition(":")
    if not separator:
        raise ValueError("Executor must be a trusted module:object")
    executor = getattr(importlib.import_module(module), name)
    journal = Journal(args.journal)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            control = ControlClient(client, base_url=args.backend_url, service_id=args.service_id,
                                    service_key=os.environ["PSKIT_COMPUTE_SERVICE_KEY"])
            receiver = Receiver(executor, control, journal, ComputeClaimRequest(
                service_id=args.service_id, worker_id=args.worker_id,
                resources=WorkerResources(gpu_uuids=args.gpu_uuid)))
            while True:
                try:
                    outcome = await receiver.run_once()
                except (httpx.HTTPError, OSError):
                    await asyncio.sleep(max(1, args.poll_seconds))
                    continue
                if outcome.status == "unknown":
                    raise RuntimeError("Execution outcome unknown; retain journal for operator reconciliation")
                await asyncio.sleep(args.poll_seconds)
    finally:
        journal.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("backend-url", "service-id", "worker-id", "executor", "journal"):
        parser.add_argument(f"--{option}", required=True)
    parser.add_argument("--gpu-uuid", action="append", default=[])
    parser.add_argument("--poll-seconds", type=float, default=2)
    asyncio.run(run(parser.parse_args()))
