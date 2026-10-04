"""Backend-only typed port to the private Docker sandbox manager."""

import httpx

from app.contracts.sandbox import SandboxMetrics, SandboxOperation, SandboxSummary
from app.domain.sandboxes import SandboxConflict


class SandboxOperations:
    def __init__(self, manager_url: str, manager_token: str, *, transport=None):
        self.manager_url = manager_url.rstrip("/")
        self.manager_token = manager_token
        self.transport = transport

    async def _request(self, method, path, **kwargs):
        async with httpx.AsyncClient(
            base_url=self.manager_url, transport=self.transport, timeout=30, trust_env=False
        ) as client:
            response = await client.request(
                method, path, headers={"Authorization": f"Bearer {self.manager_token}"}, **kwargs
            )
            if response.status_code == 409:
                raise SandboxConflict(response.json().get("detail", "Sandbox conflict"))
            response.raise_for_status()
            return response.json()

    async def list(self) -> list[SandboxSummary]:
        return [
            SandboxSummary.model_validate(item)
            for item in await self._request("GET", "/v1/sandboxes")
        ]

    async def drain(self, owner_id: str, expected_revision: int) -> SandboxOperation:
        return SandboxOperation.model_validate(
            await self._request(
                "POST",
                f"/v1/sandboxes/{owner_id}/drain",
                json={"expected_revision": expected_revision},
            )
        )

    async def replace_keep_volume(self, owner_id: str, image_digest: str) -> SandboxOperation:
        return SandboxOperation.model_validate(
            await self._request(
                "POST", f"/v1/sandboxes/{owner_id}/replace", json={"image_digest": image_digest}
            )
        )

    async def read_usage(self, owner_id: str) -> SandboxMetrics:
        return SandboxMetrics.model_validate(
            await self._request("GET", f"/v1/sandboxes/{owner_id}/usage")
        )
