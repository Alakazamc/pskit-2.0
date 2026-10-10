"""Account for administrative MCP executions in the shared release boundary."""

from pydantic import TypeAdapter, ValidationError

from app.adapters.live.limited_mcp import LimitedMcp, McpCapacityExceeded
from app.contracts.capabilities import McpInvokeResult
from app.contracts.compute import ExecutionReport
from app.contracts.tool_products import CapabilityBinding
from app.domain.mcp_capacity import McpCapacityStore
from app.domain.mcp_tool_calls import McpToolCallStore
from app.domain.release_admission import AdmissionClosed


class _ExecutorProvider:
    def __init__(self, executor) -> None:
        self.executor = executor

    def tools(self):
        return []

    async def discover(self):
        return []

    async def invoke(self, name: str, payload: dict) -> McpInvokeResult:
        evidence = await self.executor.execute(
            CapabilityBinding.model_validate(payload["binding"]),
            payload["arguments"], payload["idempotency_key"],
        )
        return McpInvokeResult(tool=name, result=evidence)


class AdmissionMcpQualificationExecutor:
    """Keep qualification calls visible until an acknowledged terminal report.

    The ordinary qualification evaluator and evidence contract remain unchanged.
    A completed cached call can be read during a release hold; new calls require
    both a durable claim and the same shared capacity as ordinary MCP calls.
    """

    def __init__(
        self, executor, calls: McpToolCallStore, shared: McpCapacityStore, *,
        max_calls: int, queue_timeout_seconds: float, lease_seconds: float,
    ) -> None:
        self.calls = calls
        self.capacity = LimitedMcp(
            _ExecutorProvider(executor), shared=shared, max_calls=max_calls,
            queue_timeout_seconds=queue_timeout_seconds, lease_seconds=lease_seconds,
        )

    async def execute(self, binding: CapabilityBinding, arguments: dict,
                      idempotency_key: str) -> dict:
        scope = f"qualification:{binding.service_id}"
        payload = {"binding": binding.model_dump(mode="json"), "arguments": arguments,
                   "idempotency_key": idempotency_key}
        state, cached = self.calls.claim(scope, idempotency_key, binding.submit_tool, payload)
        if state == "completed":
            assert cached is not None
            return cached.result
        if state != "claimed":
            raise ValueError("QUALIFICATION_CALL_CONFLICT" if state == "conflict"
                             else "QUALIFICATION_OUTCOME_UNKNOWN")
        try:
            result = await self.capacity.invoke(binding.submit_tool, payload)
        except (AdmissionClosed, McpCapacityExceeded):
            self.calls.release_before_invoke(scope, idempotency_key)
            raise
        except BaseException:
            self.calls.mark_unknown(scope, idempotency_key)
            raise
        assert result is not None
        try:
            report = TypeAdapter(ExecutionReport).validate_python(result.result.get("report"))
            terminal = report.status in {"completed", "failed"}
        except ValidationError:
            terminal = False
        if terminal:
            self.calls.complete(scope, idempotency_key, result)
        else:
            self.calls.mark_unknown(scope, idempotency_key)
        return result.result
