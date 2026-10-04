"""Register normal Python functions and validate their standardized business output."""

import asyncio
import inspect
from typing import get_type_hints

from pydantic import ConfigDict, TypeAdapter, ValidationError, create_model

from app.contracts.compute import (
    CapabilityVersion,
    ComputeBudget,
    ComputeServiceManifest,
    ExecutionError,
    ExecutionReport,
    Failed,
)
from app.domain.compute.metering import validate_report
from pskit_compute.context import ExecutionContext


class ProtocolError(ValueError):
    """Service result cannot pass the advertised schema/usage contract."""


class ComputeFailure(Exception):
    """Expected failure with the service's already consumed resource usage."""

    def __init__(self, *, code, message, usage):
        self.report = Failed(error=ExecutionError(code=code, message=message), usage=usage)
        super().__init__(code)


class ComputeService:
    def __init__(self, service_id, model_version):
        self.service_id, self.model_version = service_id, model_version
        self._tools = {}

    def compute_tool(self, *, name, version="1", required_usage=(), max_budget=None,
                     gpu_count=0, output_schema=None, **policy):
        """Return the original callable and register its input schema and policy."""
        def decorate(function):
            if name in self._tools:
                raise ValueError("DUPLICATE_TOOL")
            fields, has_context = {}, False
            hints = get_type_hints(function)
            for parameter in inspect.signature(function).parameters.values():
                if parameter.name == "ctx":
                    if hints.get("ctx") is not ExecutionContext:
                        raise ValueError("ctx must be annotated as ExecutionContext")
                    has_context = True
                    continue
                if parameter.name in {"user_id", "owner", "run_id", "tool_call_id", "grant"}:
                    raise ValueError("Trusted identity belongs in ctx, not tool arguments")
                if parameter.kind not in {inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                         inspect.Parameter.KEYWORD_ONLY}:
                    raise ValueError("Tools need named parameters")
                if parameter.name not in hints:
                    raise ValueError("Tool inputs require type annotations")
                fields[parameter.name] = (hints[parameter.name], ... if
                    parameter.default is inspect.Parameter.empty else parameter.default)
            model = create_model(f"{name}Input", __config__=ConfigDict(extra="forbid", strict=True), **fields)
            capability = CapabilityVersion(id=f"{self.service_id}.{name}", version=version,
                input_schema=model.model_json_schema(), output_schema=output_schema or {"type": "object"},
                required_usage=list(required_usage), max_budget=max_budget or ComputeBudget(),
                gpu_count=gpu_count, **policy)
            self._tools[name] = (function, model, capability, has_context)
            return function
        return decorate

    def manifest(self):
        return ComputeServiceManifest(service_id=self.service_id, model_version=self.model_version,
                                      capabilities=[tool[2] for tool in self._tools.values()])

    def validate(self, capability_id, report):
        """The same required-metric/output validation used by server-side Test and settlement."""
        tool = next((tool for tool in self._tools.values() if tool[2].id == capability_id), None)
        if tool is None:
            raise ProtocolError("CAPABILITY_NOT_REGISTERED")
        try:
            report = TypeAdapter(ExecutionReport).validate_python(report)
            validate_report(report, tool[2])
        except (ValidationError, ValueError) as exc:
            raise ProtocolError(str(exc)) from exc
        return report

    async def execute(self, grant, arguments=None, *, context=None):
        """Run once with trusted grant arguments; no CPU/GPU observation is implied."""
        if grant.job.service_id != self.service_id:
            raise ProtocolError("SERVICE_MISMATCH")
        tool = next((tool for tool in self._tools.values() if tool[2].id == grant.job.capability.id
                     and tool[2].version == grant.job.capability.version), None)
        if tool is None:
            raise ProtocolError("CAPABILITY_NOT_REGISTERED")
        function, model, capability, has_context = tool
        # Publishing is server policy; execution schemas/units must match the deployed function.
        for field in ("input_schema", "output_schema", "required_usage", "gpu_count", "max_budget"):
            if getattr(capability, field) != getattr(grant.job.capability, field):
                raise ProtocolError("CAPABILITY_SCHEMA_MISMATCH")
        if arguments is not None and arguments != grant.job.arguments:
            raise ProtocolError("ARGUMENTS_MISMATCH")
        try:
            inputs = model.model_validate(grant.job.arguments).model_dump()
        except ValidationError as exc:
            raise ProtocolError("INVALID_ARGUMENTS") from exc
        ctx = context or ExecutionContext(grant)
        if has_context:
            inputs["ctx"] = ctx
        try:
            raw = await function(**inputs) if inspect.iscoroutinefunction(function) else await asyncio.to_thread(
                function, **inputs,
            )
        except ComputeFailure as exc:
            raw = exc.report
        except Exception:  # noqa: BLE001 - Uninstrumented service errors must preserve unknown usage.
            from app.contracts.compute import UsageReport
            # Not accepted as a metered terminal result if the policy requires known usage.
            return Failed(error=ExecutionError(code="EXECUTION_USAGE_UNKNOWN", message="Execution failed; usage requires reconciliation"),
                          usage=UsageReport(source="unknown"))
        return self.validate(capability.id, raw)
