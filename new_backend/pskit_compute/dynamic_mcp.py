"""Execute immutable MCP bindings without importing service-owned Python code."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from jsonschema import Draft202012Validator
from mcp import types

from app.adapters.live.remote_mcp import RemoteMcp
from app.contracts.compute import Completed, Pending, UsageReport
from pskit_compute.http_adapter import parse_report
from pskit_compute.service import ProtocolError

MAX_REPORT_BYTES = 1024 * 1024


def _pointer(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise ProtocolError("MCP_RESULT_POINTER_INVALID")
    value = document
    try:
        for raw in pointer[1:].split("/"):
            key = raw.replace("~1", "/").replace("~0", "~")
            value = value[int(key)] if isinstance(value, list) else value[key]
    except (IndexError, KeyError, TypeError, ValueError) as exc:
        raise ProtocolError("MCP_RESULT_POINTER_MISSING") from exc
    return value


def _apply_mapping(value: Any, mapping) -> Any:
    if isinstance(value, dict) and mapping.renames:
        value = {mapping.renames.get(key, key): item for key, item in value.items()}
    for transform in mapping.transforms:
        name = transform.get("name")
        if name == "identity":
            continue
        if name == "limit" and isinstance(value, list):
            value = value[: int(transform.get("limit", 100))]
        elif name == "sort" and isinstance(value, list):
            field = transform.get("field")
            reverse = transform.get("direction") == "desc"
            value = sorted(value, key=lambda item: item.get(field) if field else item,
                           reverse=reverse)
        elif name == "rename-fields" and isinstance(value, dict):
            renames = transform.get("renames", {})
            value = {renames.get(key, key): item for key, item in value.items()}
        else:
            raise ProtocolError("MCP_RESULT_TRANSFORM_UNSUPPORTED")
    return value


class DynamicMcpAdapter:
    """One resolved binding with no authority to alter its transport policy."""

    def __init__(self, binding, *, endpoint_url: str, credential: str) -> None:
        self.binding = binding
        self.remote = RemoteMcp(
            endpoint_url,
            allowed_tools={
                tool for tool in (
                    binding.submit_tool,
                    binding.status_tool,
                    binding.cancel_tool,
                ) if tool
            },
            bearer_token=credential,
        )

    def _report(self, result, grant):
        if not isinstance(result, types.CallToolResult):
            payload = result.model_dump(mode="python")
            payload.pop("_meta", None)
            result = types.CallToolResult.model_validate(payload)
        data = result.structuredContent
        if not isinstance(data, dict):
            blocks = [item.text for item in result.content if item.type == "text"]
            if len(blocks) != 1:
                raise ProtocolError("MCP_REPORT_REQUIRED")
            try:
                data = json.loads(blocks[0])
            except ValueError as exc:
                raise ProtocolError("MCP_REPORT_REQUIRED") from exc
        if len(json.dumps(data, ensure_ascii=False).encode()) > MAX_REPORT_BYTES:
            raise ProtocolError("MCP_REPORT_TOO_LARGE")
        if not Draft202012Validator(self.binding.remote_output_schema).is_valid(data):
            raise ProtocolError("MCP_REMOTE_OUTPUT_INVALID")
        if result.isError and data.get("status") != "failed":
            raise ProtocolError("MCP_REPORT_ERROR_MISMATCH")
        if data.get("status") in {"completed", "pending", "failed"}:
            return parse_report(data, grant.job.capability)
        mapped = _apply_mapping(
            _pointer(data, self.binding.result_mapping.pointer),
            self.binding.result_mapping,
        )
        report = Completed(
            result=mapped,
            usage=UsageReport.model_validate(data.get("usage", {"source": "unknown"})),
            artifacts=data.get("artifacts", []),
            job_id=data.get("job_id"),
        )
        return parse_report(report.model_dump(mode="json"), grant.job.capability)

    async def _call_tool(self, name, arguments, grant):
        if not name:
            raise ProtocolError("MCP_STATUS_TOOL_REQUIRED")
        async with asyncio.timeout(self.remote.timeout_seconds):
            async with self.remote._session() as session:
                result = await session.call_tool(name, arguments)
        return self._report(result, grant)

    @staticmethod
    def _tasks(session):
        tasks = getattr(getattr(session, "_server_capabilities", None), "tasks", None)
        if tasks is None:
            raise ProtocolError("MCP_TASKS_NOT_NEGOTIATED")
        return tasks

    async def execute(self, grant, *, context=None):
        del context
        if self.binding.adapter == "mcp_tasks":
            async with asyncio.timeout(self.remote.timeout_seconds):
                async with self.remote._session() as session:
                    tasks = self._tasks(session)
                    requests = getattr(tasks, "requests", None)
                    if not getattr(getattr(requests, "tools", None), "call", None):
                        raise ProtocolError("MCP_TASKS_NOT_NEGOTIATED")
                    created = await session.send_request(
                        types.ClientRequest(types.CallToolRequest(
                            params=types.CallToolRequestParams(
                                name=self.binding.submit_tool,
                                arguments=grant.job.arguments,
                                task=types.TaskMetadata(),
                            )
                        )),
                        types.CreateTaskResult,
                    )
            return Pending(job_id=created.task.taskId)
        report = await self._call_tool(
            self.binding.submit_tool, grant.job.arguments, grant
        )
        if self.binding.adapter == "immediate_mcp" and isinstance(report, Pending):
            raise ProtocolError("MCP_IMMEDIATE_PENDING")
        return report

    async def poll(self, grant, pending):
        if self.binding.adapter != "mcp_tasks":
            report = await self._call_tool(
                self.binding.status_tool, {"job_id": pending.job_id}, grant
            )
            if report.job_id != pending.job_id:
                raise ProtocolError("EXTERNAL_JOB_CONFLICT")
            return report
        async with asyncio.timeout(self.remote.timeout_seconds):
            async with self.remote._session() as session:
                self._tasks(session)
                state = await session.send_request(
                    types.ClientRequest(types.GetTaskRequest(
                        params=types.GetTaskRequestParams(taskId=pending.job_id)
                    )),
                    types.GetTaskResult,
                )
                if state.taskId != pending.job_id:
                    raise ProtocolError("EXTERNAL_JOB_CONFLICT")
                if state.status in {"working", "input_required"}:
                    return pending
                result = await session.send_request(
                    types.ClientRequest(types.GetTaskPayloadRequest(
                        params=types.GetTaskPayloadRequestParams(taskId=pending.job_id)
                    )),
                    types.GetTaskPayloadResult,
                )
        report = self._report(result, grant)
        if report.job_id != pending.job_id:
            raise ProtocolError("EXTERNAL_JOB_CONFLICT")
        return report

    async def cancel(self, grant, pending):
        del grant
        if self.binding.adapter != "mcp_tasks":
            if not self.binding.cancel_tool:
                return False
            async with self.remote._session() as session:
                await session.call_tool(
                    self.binding.cancel_tool, {"job_id": pending.job_id}
                )
            return True
        async with asyncio.timeout(self.remote.timeout_seconds):
            async with self.remote._session() as session:
                tasks = self._tasks(session)
                if not getattr(tasks, "cancel", None):
                    return False
                await session.send_request(
                    types.ClientRequest(types.CancelTaskRequest(
                        params=types.CancelTaskRequestParams(taskId=pending.job_id)
                    )),
                    types.CancelTaskResult,
                )
        return True


class DynamicMcpExecutor:
    """Resolve local reachability and credentials for each trusted grant."""

    def __init__(self, endpoint_resolver, credential_resolver) -> None:
        self.endpoint_resolver = endpoint_resolver
        self.credential_resolver = credential_resolver

    @staticmethod
    def from_grant(grant, endpoint_resolver, credential_resolver) -> DynamicMcpAdapter:
        binding = grant.execution_binding
        if binding is None:
            raise ProtocolError("EXECUTION_BINDING_REQUIRED")
        endpoint = endpoint_resolver(binding.endpoint_url)
        credential = (
            credential_resolver(binding.credential_ref)
            if binding.credential_ref else ""
        )
        return DynamicMcpAdapter(binding, endpoint_url=endpoint, credential=credential)

    def _adapter(self, grant) -> DynamicMcpAdapter:
        return self.from_grant(
            grant, self.endpoint_resolver, self.credential_resolver
        )

    async def execute(self, grant, *, context=None):
        return await self._adapter(grant).execute(grant, context=context)

    async def poll(self, grant, pending):
        return await self._adapter(grant).poll(grant, pending)

    async def cancel(self, grant, pending):
        return await self._adapter(grant).cancel(grant, pending)
