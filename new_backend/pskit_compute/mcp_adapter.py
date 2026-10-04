"""MCP 1.x transport using the installed ClientSession generation, no Tasks extension."""

import asyncio
import json

from app.adapters.live.remote_mcp import RemoteMcp
from pskit_compute.http_adapter import parse_report
from pskit_compute.service import ProtocolError


class McpAdapter:
    def __init__(self, *, url, submit_tool, status_tool=None, cancel_tool=None,
                 bearer_token="", timeout_seconds=30, http_client=None):
        self.remote = RemoteMcp(url, allowed_tools={name for name in
            (submit_tool, status_tool, cancel_tool) if name}, bearer_token=bearer_token,
            timeout_seconds=timeout_seconds, http_client=http_client)
        self.submit_tool, self.status_tool, self.cancel_tool = submit_tool, status_tool, cancel_tool

    async def _call(self, name, arguments, grant):
        if not name:
            raise ProtocolError("MCP_STATUS_TOOL_REQUIRED")
        async with asyncio.timeout(self.remote.timeout_seconds):
            async with self.remote._session() as session:
                result = await session.call_tool(name, arguments)
        data = result.structuredContent
        if not isinstance(data, dict):
            blocks = [item.text for item in result.content if item.type == "text"]
            if len(blocks) != 1:
                raise ProtocolError("MCP_REPORT_REQUIRED")
            try:
                data = json.loads(blocks[0])
            except ValueError as exc:
                raise ProtocolError("MCP_REPORT_REQUIRED") from exc
        if len(json.dumps(data).encode()) > 1024*1024:
            raise ProtocolError("MCP_REPORT_TOO_LARGE")
        if result.isError and data.get("status") != "failed":
            raise ProtocolError("MCP_REPORT_ERROR_MISMATCH")
        # A Failed business envelope retains usage even if the MCP isError flag is set.
        return parse_report(data, grant.job.capability)

    async def execute(self, grant, *, context=None):
        return await self._call(self.submit_tool, grant.job.arguments, grant)

    async def poll(self, grant, pending):
        report = await self._call(self.status_tool, {"job_id": pending.job_id}, grant)
        if report.job_id != pending.job_id:
            raise ProtocolError("EXTERNAL_JOB_CONFLICT")
        return report

    async def cancel(self, grant, pending):
        if not self.cancel_tool:
            return False
        async with self.remote._session() as session:
            await session.call_tool(self.cancel_tool, {"job_id": pending.job_id})
        return True
