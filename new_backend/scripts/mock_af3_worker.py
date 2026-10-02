"""Contract-only AF3 worker. It never runs AlphaFold or produces structures."""

import argparse
import asyncio
import json
import os
from uuid import uuid4

import httpx


class MockAf3Worker:
    def __init__(
        self, client: httpx.AsyncClient, *, compute_key: str, worker_id: str,
        gpu_memory_mb: int = 49_152,
    ) -> None:
        self.client = client
        self.headers = {"X-Compute-Key": compute_key}
        self.worker_id = worker_id
        self.gpu_memory_mb = gpu_memory_mb

    async def run_once(self) -> int:
        response = await self.client.post(
            "/internal/compute/af3/jobs/claim", headers=self.headers,
            json={"worker_id": self.worker_id, "lease_seconds": 60, "max_jobs": 1,
                  "resources": {"capabilities": ["af3"], "gpu_count": 1,
                                "gpu_memory_mb": self.gpu_memory_mb}},
        )
        response.raise_for_status()
        claims = response.json()
        for claim in claims:
            await self._complete(claim)
        return len(claims)

    async def _complete(self, claim: dict) -> None:
        job_id = claim["id"]
        attempt = claim["attempt"]
        lease_token = claim["lease_token"]
        fold_input = claim["fold_input"]
        artifact_id = f"mock-{uuid4().hex}"
        result = {
            "simulation": True,
            "input_name": fold_input["name"],
            "entity_count": len(fold_input["sequences"]),
        }
        upload = await self.client.put(
            f"/internal/af3/jobs/{job_id}/artifacts/{artifact_id}",
            headers={**self.headers, "X-Compute-Lease": lease_token},
            params={"name": "mock_af3_result.json", "kind": "mock", "attempt": attempt},
            content=json.dumps(result).encode("utf-8"),
        )
        upload.raise_for_status()
        completed = await self.client.post(
            f"/internal/af3/jobs/{job_id}/result", headers=self.headers,
            json={
                "status": "completed", "actual_gpu_minutes": 1, "attempt": attempt,
                "simulation": True,
                "lease_token": lease_token,
                "artifacts": [{"id": artifact_id, "name": "mock_af3_result.json", "kind": "mock"}],
            },
        )
        completed.raise_for_status()


async def _main() -> None:
    parser = argparse.ArgumentParser(description="AF3 compute contract mock, no GPU work")
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument("--worker-id", default="mock-worker")
    parser.add_argument("--gpu-memory-mb", type=int, default=49_152)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args()
    if args.poll_seconds <= 0:
        parser.error("--poll-seconds must be positive")
    if args.gpu_memory_mb < 0:
        parser.error("--gpu-memory-mb must be non-negative")
    compute_key = os.environ.get("RESEARCH_AGENT_COMPUTE_CALLBACK_KEY")
    if not compute_key:
        parser.error("RESEARCH_AGENT_COMPUTE_CALLBACK_KEY is required")
    async with httpx.AsyncClient(base_url=args.base_url, timeout=20.0) as client:
        worker = MockAf3Worker(client, compute_key=compute_key, worker_id=args.worker_id,
                               gpu_memory_mb=args.gpu_memory_mb)
        while True:
            await worker.run_once()
            await asyncio.sleep(args.poll_seconds)


if __name__ == "__main__":
    asyncio.run(_main())
