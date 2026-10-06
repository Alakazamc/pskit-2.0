"""Bounded MCP discovery and evidence-producing Tool Product qualification."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from jsonschema import Draft202012Validator
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from psycopg.types.json import Jsonb
from pydantic import TypeAdapter

from app.adapters.live.remote_mcp import _has_external_ref
from app.contracts.compute import Completed, ExecutionReport
from app.contracts.tool_products import (
    AcceptanceSuite,
    DiscoveredTool,
    DiscoverySnapshot,
    EndpointSnapshot,
    ProbeSnapshot,
    QualificationCaseResult,
    QualificationReport,
    ToolProductDraft,
)
from app.domain.compute.common import payload_hash
from app.domain.tool_products.ui_schema import validate_tool_ui


def _pointer(document: Any, pointer: str) -> Any:
    if not pointer.startswith("/"):
        raise ValueError("INVALID_JSON_POINTER")
    value = document
    for raw in pointer[1:].split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            value = value[int(key)]
        elif isinstance(value, dict):
            value = value[key]
        else:
            raise KeyError(key)
    return value


class StreamableHttpDiscoveryTransport:
    """One short-lived no-redirect MCP session used only by the admin pipeline."""

    def __init__(self, timeout_seconds: float, transport: str = "streamable_http") -> None:
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self.stack: AsyncExitStack | None = None
        self.session: ClientSession | None = None

    async def initialize(self, endpoint: EndpointSnapshot, credential: str) -> dict[str, Any]:
        if endpoint.scheme not in {"http", "https"}:
            raise ValueError("MCP_TRANSPORT_UNSUPPORTED")
        self.stack = AsyncExitStack()
        await self.stack.__aenter__()
        headers = {"Authorization": f"Bearer {credential}"} if credential else {}
        if self.transport == "streamable_http":
            client = await self.stack.enter_async_context(
                httpx.AsyncClient(
                    headers=headers,
                    timeout=self.timeout_seconds,
                    follow_redirects=False,
                    trust_env=False,
                )
            )
            streams = await self.stack.enter_async_context(
                streamable_http_client(endpoint.uri, http_client=client)
            )
        elif self.transport == "sse":
            async def reject_redirect(response: httpx.Response) -> None:
                if response.is_redirect:
                    raise ValueError("REDIRECT_FORBIDDEN")

            @asynccontextmanager
            async def client_factory(headers=None, timeout=None, auth=None):
                async with httpx.AsyncClient(
                    headers=headers,
                    timeout=timeout,
                    auth=auth,
                    follow_redirects=False,
                    trust_env=False,
                    event_hooks={"response": [reject_redirect]},
                ) as client:
                    yield client

            streams = await self.stack.enter_async_context(
                sse_client(
                    endpoint.uri,
                    headers=headers,
                    timeout=self.timeout_seconds,
                    sse_read_timeout=self.timeout_seconds,
                    httpx_client_factory=client_factory,
                )
            )
        else:
            raise ValueError("MCP_TRANSPORT_UNSUPPORTED")
        self.session = await self.stack.enter_async_context(
            ClientSession(streams[0], streams[1])
        )
        result = await self.session.initialize()
        protocol = result.model_dump(mode="json", by_alias=True)
        protocol["final_url"] = endpoint.uri
        return protocol

    async def list_tools(
        self, _endpoint: EndpointSnapshot, cursor: str | None, _credential: str
    ) -> dict[str, Any]:
        if self.session is None:
            raise RuntimeError("MCP session is not initialized")
        response = await self.session.list_tools(cursor=cursor)
        tools = []
        for item in response.tools:
            tools.append({
                "name": item.name,
                "description": item.description or "",
                "input_schema": item.inputSchema,
                "output_schema": getattr(item, "outputSchema", None),
            })
        return {"tools": tools, "next_cursor": response.nextCursor}

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if self.session is None:
            raise RuntimeError("MCP session is not initialized")
        return await self.session.call_tool(
            name,
            arguments,
            read_timeout_seconds=timedelta(seconds=self.timeout_seconds),
        )

    async def close(self) -> None:
        if self.stack is not None:
            await self.stack.aclose()
            self.stack = None
            self.session = None


class DirectMcpQualificationExecutor:
    """Invoke one approved binding directly and cache its idempotent evidence."""

    def __init__(self, binding_resolver, *, transport_factory, timeout_seconds: float) -> None:
        self.binding_resolver = binding_resolver
        self.transport_factory = transport_factory
        self.timeout_seconds = timeout_seconds
        self._cache: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _report(response: Any) -> dict[str, Any]:
        if isinstance(response, dict):
            report = response
        else:
            report = getattr(response, "structuredContent", None)
            if not isinstance(report, dict):
                blocks = [
                    item.text
                    for item in getattr(response, "content", [])
                    if getattr(item, "type", None) == "text"
                ]
                if len(blocks) != 1:
                    raise ValueError("MCP_REPORT_REQUIRED")
                try:
                    report = json.loads(blocks[0])
                except (TypeError, ValueError) as exc:
                    raise ValueError("MCP_REPORT_REQUIRED") from exc
        if not isinstance(report, dict):
            raise ValueError(  # noqa: TRY004 — stable admin error contract
                "MCP_REPORT_REQUIRED"
            )
        if len(json.dumps(report, ensure_ascii=False).encode()) > 1024 * 1024:
            raise ValueError("MCP_REPORT_TOO_LARGE")
        return report

    async def execute(self, binding, arguments: dict[str, Any], idempotency_key: str):
        cached = self._cache.pop(idempotency_key, None)
        if cached is not None:
            return copy.deepcopy(cached)
        endpoint, transport_name, credential = self.binding_resolver(binding)
        transport = self.transport_factory(transport_name)
        try:
            async with asyncio.timeout(self.timeout_seconds):
                protocol = await transport.initialize(endpoint, credential)
                if protocol.get("final_url", endpoint.uri) != endpoint.uri:
                    raise ValueError("REDIRECT_FORBIDDEN")
                response = await transport.call_tool(binding.submit_tool, arguments)
        except TimeoutError as exc:
            raise ValueError("QUALIFICATION_EXECUTION_TIMEOUT") from exc
        finally:
            close = getattr(transport, "close", None)
            if close is not None:
                await close()
        report = self._report(response)
        evidence = {
            "report": report,
            "events": [],
            "cancellation": {"requested": False, "confirmed": False},
            "remote_execution_id": report.get("job_id")
            or f"qualification-{payload_hash({'key': idempotency_key})[:32]}",
        }
        if len(self._cache) >= 1024:
            self._cache.pop(next(iter(self._cache)))
        self._cache[idempotency_key] = copy.deepcopy(evidence)
        return evidence


class DiscoveryCollector:
    def __init__(
        self,
        *,
        timeout_seconds: float = 5,
        max_bytes: int = 1024 * 1024,
        max_pages: int = 100,
        max_tools: int = 1000,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self.max_pages = max_pages
        self.max_tools = max_tools

    async def collect(self, endpoint, transport, credential) -> DiscoverySnapshot:
        try:
            async with asyncio.timeout(self.timeout_seconds):
                protocol = await transport.initialize(endpoint, credential)
                if protocol.get("final_url", endpoint.uri) != endpoint.uri:
                    raise ValueError("REDIRECT_FORBIDDEN")
                protocol = {key: value for key, value in protocol.items() if key != "final_url"}
                cursor = None
                pages = 0
                tools: list[DiscoveredTool] = []
                seen: set[str] = set()
                while True:
                    pages += 1
                    if pages > self.max_pages:
                        raise ValueError("DISCOVERY_PAGE_LIMIT")
                    page = await transport.list_tools(endpoint, cursor, credential)
                    raw_tools = page.get("tools")
                    if not isinstance(raw_tools, list):
                        raise ValueError(  # noqa: TRY004 — stable admin error contract
                            "DISCOVERY_INVALID_RESPONSE"
                        )
                    for raw in raw_tools:
                        if not isinstance(raw, dict):
                            raise ValueError(  # noqa: TRY004 — stable admin error contract
                                "DISCOVERY_INVALID_RESPONSE"
                            )
                        name = raw.get("name")
                        if not isinstance(name, str) or not name or name in seen:
                            raise ValueError("DISCOVERY_DUPLICATE_TOOL")
                        input_schema = raw.get("input_schema", raw.get("inputSchema", {}))
                        output_schema = raw.get(
                            "output_schema", raw.get("outputSchema")
                        ) or {"type": "object"}
                        for schema in (input_schema, output_schema):
                            Draft202012Validator.check_schema(schema)
                            if _has_external_ref(schema):
                                raise ValueError("EXTERNAL_SCHEMA_REF_FORBIDDEN")
                        seen.add(name)
                        tools.append(DiscoveredTool(
                            name=name,
                            description=str(raw.get("description") or ""),
                            input_schema=input_schema,
                            remote_output_schema=output_schema,
                        ))
                    if len(tools) > self.max_tools:
                        raise ValueError("DISCOVERY_TOOL_LIMIT")
                    bounded = {
                        "protocol": protocol,
                        "tools": [tool.model_dump(mode="json") for tool in tools],
                    }
                    if len(json.dumps(bounded, ensure_ascii=False).encode()) > self.max_bytes:
                        raise ValueError("DISCOVERY_TOO_LARGE")
                    cursor = page.get("next_cursor", page.get("nextCursor"))
                    if not cursor:
                        break
                now = datetime.now(UTC)
                return DiscoverySnapshot(
                    discovery_id=f"discovery-{uuid.uuid4()}",
                    service_id="unpersisted",
                    service_revision=1,
                    protocol=protocol,
                    tools=tools,
                    digest=payload_hash(bounded),
                    discovered_at=now,
                )
        except TimeoutError as exc:
            raise ValueError("DISCOVERY_TIMEOUT") from exc
        finally:
            close = getattr(transport, "close", None)
            if close is not None:
                await close()


class QualificationEvaluator:
    def __init__(self, executor) -> None:
        self.executor = executor

    @staticmethod
    def _assert_result(result: dict[str, Any], assertion) -> bool:
        try:
            value = _pointer(result, assertion.pointer)
        except (KeyError, IndexError, TypeError, ValueError):
            return False
        if assertion.predicate == "exists":
            return value is not None
        if assertion.predicate == "equals":
            return value == assertion.value
        if assertion.predicate == "min_items":
            return isinstance(value, (list, dict, str)) and len(value) >= assertion.value
        if assertion.predicate == "maximum":
            return isinstance(value, (int, float)) and value <= assertion.value
        if assertion.predicate == "minimum":
            return isinstance(value, (int, float)) and value >= assertion.value
        return False

    async def evaluate(
        self,
        draft: ToolProductDraft,
        suite: AcceptanceSuite,
    ) -> QualificationReport:
        evaluation_id = uuid.uuid4()
        actions = {action.id: action for action in draft.actions}
        bindings = {binding.binding_id: binding for binding in draft.bindings}
        case_results: list[QualificationCaseResult] = []
        protocol_passed = True
        scientific_passed = True
        for case in suite.cases:
            action = actions.get(case.action_id)
            if action is None or not action.binding_ids:
                raise ValueError("ACCEPTANCE_ACTION_NOT_FOUND")
            binding = bindings[action.binding_ids[0]]
            input_validator = Draft202012Validator(action.input_schema)
            invalid_inputs_rejected = all(
                bool(list(input_validator.iter_errors(arguments)))
                for arguments in case.invalid_arguments
            )
            valid_input = not list(input_validator.iter_errors(case.arguments))
            execution = None
            repeated = None
            if valid_input:
                key = (
                    f"qualification:{evaluation_id}:{draft.product_id}:"
                    f"{draft.revision}:{case.case_id}"
                )
                execution = await self.executor.execute(binding, case.arguments, key)
                if case.check_idempotency:
                    repeated = await self.executor.execute(binding, case.arguments, key)

            report = None
            terminal_report_valid = False
            if execution is not None:
                try:
                    report = TypeAdapter(ExecutionReport).validate_python(execution.get("report"))
                    terminal_report_valid = isinstance(report, Completed)
                    if terminal_report_valid:
                        terminal_report_valid = not list(
                            Draft202012Validator(binding.result_schema).iter_errors(report.result)
                        )
                except (ValueError, TypeError):
                    report = None

            usage_complete = bool(
                report
                and all(getattr(report.usage, metric) is not None for metric in binding.required_usage)
            )
            artifacts_valid = bool(
                report
                and all(
                    artifact.id
                    and artifact.name
                    and artifact.kind
                    and artifact.available
                    for artifact in report.artifacts
                )
            )
            idempotent = not case.check_idempotency or bool(
                execution
                and repeated
                and execution.get("remote_execution_id")
                == repeated.get("remote_execution_id")
                and execution.get("report") == repeated.get("report")
            )
            cancellation_confirmed = not case.check_cancellation or bool(
                execution
                and execution.get("cancellation", {}).get("requested") is True
                and execution.get("cancellation", {}).get("confirmed") is True
            )
            events = execution.get("events", []) if execution else []
            sequences = [event.get("sequence") for event in events]
            event_types = [event.get("type") for event in events]
            progress_ordered = (
                all(isinstance(sequence, int) and sequence > 0 for sequence in sequences)
                and sequences == sorted(set(sequences))
                and all(required in event_types for required in case.required_progress_types)
            )
            protocol = {
                "invalid_inputs_rejected": invalid_inputs_rejected,
                "terminal_report_valid": terminal_report_valid,
                "usage_complete": usage_complete,
                "artifacts_valid": artifacts_valid,
                "idempotent": idempotent,
                "cancellation_confirmed": cancellation_confirmed,
                "progress_ordered": progress_ordered,
            }
            science = {
                f"assertion_{index}": bool(
                    report
                    and isinstance(report, Completed)
                    and self._assert_result(report.result, assertion)
                )
                for index, assertion in enumerate(case.result_assertions)
            }
            case_protocol = all(protocol.values())
            case_science = all(science.values())
            protocol_passed = protocol_passed and case_protocol
            scientific_passed = scientific_passed and case_science
            case_results.append(QualificationCaseResult(
                case_id=case.case_id,
                status="passed" if case_protocol and case_science else "failed",
                protocol_assertions=protocol,
                scientific_assertions=science,
                usage_source=report.usage.source if report else None,
                message="Qualification case passed" if case_protocol and case_science
                else "Qualification case failed",
            ))

        ui_digest = validate_tool_ui(draft.ui_schema, draft.bindings)
        binding_digest = payload_hash([
            binding.model_dump(mode="json", by_alias=True) for binding in draft.bindings
        ])
        return QualificationReport(
            report_id=f"qualification-{uuid.uuid4()}",
            product_id=draft.product_id,
            product_revision=draft.revision,
            service_revision=draft.bindings[0].service_revision,
            binding_digest=binding_digest,
            ui_digest=ui_digest,
            suite_digest=payload_hash(suite.model_dump(mode="json", by_alias=True)),
            status="passed" if protocol_passed and scientific_passed else "failed",
            protocol_passed=protocol_passed,
            scientific_passed=scientific_passed,
            cases=case_results,
            qualified_at=datetime.now(UTC),
        )


class McpQualification:
    """Persist approved probes/discoveries and exact qualification reports."""

    def __init__(
        self,
        database,
        repository,
        endpoint_policy,
        *,
        credential_refs: dict[str, str] | None = None,
        transport_factory=None,
        executor=None,
        timeout_seconds: float = 5,
        execution_timeout_seconds: float = 30,
    ) -> None:
        self.database = database
        self.repository = repository
        self.endpoint_policy = endpoint_policy
        self.credential_refs = credential_refs or {}
        self.transport_factory = transport_factory or (
            lambda transport: StreamableHttpDiscoveryTransport(timeout_seconds, transport)
        )
        self.collector = DiscoveryCollector(timeout_seconds=timeout_seconds)
        self.executor = executor or DirectMcpQualificationExecutor(
            self._resolve_binding,
            transport_factory=self.transport_factory,
            timeout_seconds=execution_timeout_seconds,
        )

    def _credential(self, reference: str | None) -> str:
        if reference is None:
            return ""
        env_name = self.credential_refs.get(reference)
        if not env_name:
            raise ValueError("CREDENTIAL_REF_NOT_APPROVED")
        secret = os.environ.get(env_name, "")
        if not secret:
            raise ValueError("SERVICE_CREDENTIAL_UNAVAILABLE")
        return secret

    def _resolve_binding(self, binding):
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT e.uri,e.transport,e.credential_ref,e.network_zone "
                "FROM mcp_service_revisions r JOIN mcp_service_endpoints e "
                "ON e.endpoint_id=r.endpoint_id WHERE r.service_id=%s AND r.revision=%s "
                "AND e.state='approved' "
                "AND EXISTS (SELECT 1 FROM mcp_discovery_snapshots d "
                "WHERE d.service_id=r.service_id AND d.service_revision=r.revision)",
                (binding.service_id, binding.service_revision),
            ).fetchone()
        if row is None:
            raise LookupError("MCP_SERVICE_REVISION_NOT_DISCOVERED")
        endpoint = self.endpoint_policy.resolve(row[0], row[3])
        return endpoint, row[1], self._credential(row[2])

    async def probe(
        self,
        *,
        service_id: str,
        uri: str,
        transport: str,
        credential_ref: str | None,
        network_zone: str,
        actor_id: str,
    ) -> ProbeSnapshot:
        endpoint = self.endpoint_policy.resolve(uri, network_zone)
        client = self.transport_factory(transport)
        credential = self._credential(credential_ref)
        try:
            async with asyncio.timeout(self.collector.timeout_seconds):
                protocol = await client.initialize(endpoint, credential)
                if not isinstance(protocol, dict):
                    raise ValueError("MCP_PROBE_INVALID_RESPONSE")  # noqa: TRY004
                if protocol.get("final_url", endpoint.uri) != endpoint.uri:
                    raise ValueError("REDIRECT_FORBIDDEN")
        except TimeoutError as exc:
            raise ValueError("MCP_PROBE_TIMEOUT") from exc
        finally:
            close = getattr(client, "close", None)
            if close is not None:
                await close()
        if len(json.dumps(protocol, ensure_ascii=False).encode()) > self.collector.max_bytes:
            raise ValueError("MCP_PROBE_TOO_LARGE")
        protocol.pop("final_url", None)
        endpoint_id = f"endpoint-{uuid.uuid4()}"
        checked_at = datetime.now(UTC)
        probe_id = f"probe-{uuid.uuid4()}"
        with self.database.transaction() as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"mcp-service:{service_id}",),
            )
            revision = connection.execute(
                "SELECT COALESCE(max(revision),0)+1 FROM mcp_service_revisions "
                "WHERE service_id=%s",
                (service_id,),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO mcp_service_endpoints "
                "(endpoint_id,uri,transport,credential_ref,network_zone,state,created_by) "
                "VALUES (%s,%s,%s,%s,%s,'approved',%s)",
                (endpoint_id, endpoint.uri, transport, credential_ref, network_zone, actor_id),
            )
            manifest = {
                "endpoint_id": endpoint_id,
                "protocol": protocol,
                "addresses": endpoint.addresses,
            }
            connection.execute(
                "INSERT INTO mcp_service_revisions "
                "(service_id,revision,endpoint_id,manifest_json,manifest_digest,created_by) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (
                    service_id,
                    revision,
                    endpoint_id,
                    Jsonb(manifest),
                    payload_hash(manifest),
                    actor_id,
                ),
            )
            snapshot = ProbeSnapshot(
                probe_id=probe_id,
                endpoint_id=endpoint_id,
                service_id=service_id,
                service_revision=revision,
                uri=endpoint.uri,
                transport=transport,
                credential_ref=credential_ref,
                network_zone=network_zone,
                protocol=protocol,
                addresses=endpoint.addresses,
                checked_at=checked_at,
            )
            connection.execute(
                "INSERT INTO mcp_probe_snapshots "
                "(probe_id,endpoint_id,service_id,service_revision,snapshot_json,checked_at) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (
                    probe_id,
                    endpoint_id,
                    service_id,
                    revision,
                    Jsonb(snapshot.model_dump(mode="json")),
                    checked_at,
                ),
            )
        return snapshot

    def get_probe(self, probe_id: str) -> ProbeSnapshot | None:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT snapshot_json FROM mcp_probe_snapshots WHERE probe_id=%s",
                (probe_id,),
            ).fetchone()
        return ProbeSnapshot.model_validate(row[0]) if row else None

    async def discover(self, service_id: str, service_revision: int) -> DiscoverySnapshot:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT e.uri,e.transport,e.credential_ref,e.network_zone,r.manifest_json "
                "FROM mcp_service_revisions r JOIN mcp_service_endpoints e "
                "ON e.endpoint_id=r.endpoint_id WHERE r.service_id=%s AND r.revision=%s",
                (service_id, service_revision),
            ).fetchone()
        if row is None:
            raise LookupError("MCP_SERVICE_REVISION_NOT_FOUND")
        endpoint = self.endpoint_policy.resolve(row[0], row[3])
        client = self.transport_factory(row[1])
        snapshot = await self.collector.collect(
            endpoint, client, self._credential(row[2])
        )
        snapshot = snapshot.model_copy(update={
            "service_id": service_id,
            "service_revision": service_revision,
        })
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO mcp_discovery_snapshots "
                "(discovery_id,service_id,service_revision,protocol_json,tools_json," 
                "discovery_digest,discovered_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    snapshot.discovery_id,
                    service_id,
                    service_revision,
                    Jsonb(snapshot.protocol),
                    Jsonb([tool.model_dump(mode="json") for tool in snapshot.tools]),
                    snapshot.digest,
                    snapshot.discovered_at,
                ),
            )
        return snapshot

    async def qualify(
        self, product_id: str, revision: int, suite_revision: int
    ) -> QualificationReport:
        draft = self.repository.get_draft_revision(product_id, revision)
        suite = self.repository.get_acceptance_suite(
            draft.acceptance_suite_id, suite_revision
        )
        if suite is None or self.executor is None:
            raise RuntimeError("QUALIFICATION_EXECUTOR_UNAVAILABLE")
        report = await QualificationEvaluator(self.executor).evaluate(draft, suite)
        self.repository.record_qualification(report)
        return report

    def get_report(self, report_id: str) -> QualificationReport | None:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT report_json FROM qualification_reports WHERE report_id=%s",
                (report_id,),
            ).fetchone()
        return QualificationReport.model_validate(row[0]) if row else None
