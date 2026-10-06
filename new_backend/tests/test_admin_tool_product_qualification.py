import asyncio
from copy import deepcopy

import pytest
from test_tool_product_contracts import draft as draft_payload

from app.config import Settings
from app.contracts.tool_products import AcceptanceSuite, ToolProductDraft
from app.domain.admin.qualification import DiscoveryCollector, QualificationEvaluator
from app.domain.admin.service_endpoints import ApprovedEndpointPolicy
from app.main import create_app


def resolver(mapping):
    def resolve(host, *_args, **_kwargs):
        return [(2, 1, 6, "", (address, 443)) for address in mapping[host]]

    return resolve


def test_application_rejects_non_object_or_invalid_mcp_network_zones():
    with pytest.raises(ValueError, match="MCP_NETWORK_ZONES_JSON"):
        create_app(Settings(admin_mcp_network_zones_json="[]"))
    with pytest.raises(ValueError, match="INVALID_MCP_NETWORK_ZONE"):
        create_app(Settings(admin_mcp_network_zones_json='{"wireguard": ["10.0.0.1/24"]}'))


def test_public_https_and_named_private_zone_are_the_only_allowed_endpoint_classes():
    policy = ApprovedEndpointPolicy(
        {"wireguard": ["10.9.8.0/24"]},
        resolver=resolver({
            "public.example": ["93.184.216.34"],
            "private.example": ["10.9.8.2"],
            "loopback.example": ["127.0.0.1"],
            "linklocal.example": ["169.254.169.254"],
        }),
    )

    public = policy.resolve("https://public.example/mcp", "public")
    private = policy.resolve("http://private.example:8080/mcp", "wireguard")

    assert public.addresses == ["93.184.216.34"]
    assert private.addresses == ["10.9.8.2"]
    with pytest.raises(ValueError, match="HTTPS"):
        policy.resolve("http://public.example/mcp", "public")
    with pytest.raises(ValueError, match="PRIVATE_ADDRESS"):
        policy.resolve("https://private.example/mcp", "public")
    with pytest.raises(ValueError, match="ADDRESS_FORBIDDEN"):
        policy.resolve("https://loopback.example/mcp", "public")
    with pytest.raises(ValueError, match="ADDRESS_FORBIDDEN"):
        policy.resolve("https://linklocal.example/mcp", "wireguard")


@pytest.mark.parametrize(
    "uri",
    [
        "file:///etc/passwd",
        "https://user:secret@public.example/mcp",
        "https://public.example/mcp#fragment",
        "https://public.example:99999/mcp",
    ],
)
def test_endpoint_uri_rejects_non_http_credentials_fragments_and_bad_ports(uri):
    policy = ApprovedEndpointPolicy(
        {}, resolver=resolver({"public.example": ["93.184.216.34"]})
    )
    with pytest.raises(ValueError, match="ENDPOINT"):
        policy.resolve(uri, "public")


class DiscoveryTransport:
    def __init__(self, pages, *, final_url=None, delay=0):
        self.pages = pages
        self.final_url = final_url
        self.delay = delay
        self.cursors = []

    async def initialize(self, endpoint, credential):
        if self.delay:
            await asyncio.sleep(self.delay)
        return {
            "protocol_version": "2025-11-25",
            "capabilities": {"tools": {"listChanged": False}},
            "final_url": self.final_url or endpoint.uri,
        }

    async def list_tools(self, endpoint, cursor, credential):
        self.cursors.append(cursor)
        return self.pages[cursor]


def tool(name, output=None):
    value = {
        "name": name,
        "description": f"Run {name}",
        "input_schema": {"type": "object", "properties": {"protein": {"type": "string"}}},
    }
    if output is not None:
        value["output_schema"] = output
    return value


@pytest.mark.asyncio
async def test_discovery_follows_cursor_and_preserves_remote_output_schema():
    endpoint = ApprovedEndpointPolicy(
        {}, resolver=resolver({"public.example": ["93.184.216.34"]})
    ).resolve("https://public.example/mcp", "public")
    transport = DiscoveryTransport({
        None: {"tools": [tool("generate", {"type": "object", "required": ["result"]})],
               "next_cursor": "page-2"},
        "page-2": {"tools": [tool("analyze", {"type": "object", "required": ["pockets"]})],
                   "next_cursor": None},
    })

    snapshot = await DiscoveryCollector(timeout_seconds=1).collect(endpoint, transport, "")

    assert transport.cursors == [None, "page-2"]
    assert [item.name for item in snapshot.tools] == ["generate", "analyze"]
    assert snapshot.tools[0].remote_output_schema["required"] == ["result"]
    assert not hasattr(snapshot.tools[0], "result_schema")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transport", "error"),
    [
        (
            DiscoveryTransport({None: {"tools": [tool("bad", {"$ref": "https://evil/schema"})]}}),
            "EXTERNAL_SCHEMA_REF",
        ),
        (
            DiscoveryTransport({None: {"tools": [tool("large", {"description": "x" * 2000})]}}),
            "DISCOVERY_TOO_LARGE",
        ),
        (
            DiscoveryTransport({}, final_url="https://redirect.example/mcp"),
            "REDIRECT_FORBIDDEN",
        ),
        (DiscoveryTransport({}, delay=0.05), "DISCOVERY_TIMEOUT"),
    ],
)
async def test_discovery_is_bounded_and_rejects_redirects_and_external_refs(transport, error):
    endpoint = ApprovedEndpointPolicy(
        {}, resolver=resolver({"public.example": ["93.184.216.34"]})
    ).resolve("https://public.example/mcp", "public")
    collector = DiscoveryCollector(timeout_seconds=0.01, max_bytes=1024)

    with pytest.raises(ValueError, match=error):
        await collector.collect(endpoint, transport, "")


def qualification_draft():
    payload = draft_payload()
    payload["actions"][0]["input_schema"] = {
        "type": "object",
        "properties": {"protein": {"type": "string", "minLength": 3}},
        "required": ["protein"],
        "additionalProperties": False,
    }
    payload["bindings"][0]["required_usage"] = ["wall_ms", "gpu_device_ms"]
    payload["bindings"][0]["cancellation"] = "confirmed_stop"
    return ToolProductDraft.model_validate(payload)


def acceptance_suite():
    return AcceptanceSuite.model_validate({
        "suite_id": "coral-suite",
        "revision": 2,
        "cases": [{
            "case_id": "one-shot-smoke",
            "action_id": "coral-one-shot",
            "arguments": {"protein": "MKT"},
            "invalid_arguments": [{}],
            "result_assertions": [{
                "pointer": "/candidates", "predicate": "min_items", "value": 1,
            }],
            "required_progress_types": ["stage.started", "stage.completed"],
            "check_idempotency": True,
            "check_cancellation": True,
        }],
    })


def completed_execution(**overrides):
    value = {
        "report": {
            "status": "completed",
            "result": {"candidates": ["AUGC"]},
            "usage": {
                "wall_ms": 100,
                "cpu_core_ms": 20,
                "gpu_device_ms": 80,
                "gpu_count": 1,
                "source": "service_reported",
            },
            "artifacts": [{
                "id": "artifact-1", "name": "candidates.csv", "kind": "csv",
                "available": True, "size": 10, "sha256": "a" * 64,
            }],
        },
        "events": [
            {"sequence": 1, "type": "stage.started"},
            {"sequence": 2, "type": "stage.completed"},
        ],
        "cancellation": {"requested": True, "confirmed": True},
        "remote_execution_id": "remote-1",
    }
    value.update(overrides)
    return value


class QualificationExecutor:
    def __init__(self, execution):
        self.execution = execution
        self.calls = []

    async def execute(self, binding, arguments, idempotency_key):
        self.calls.append((binding.binding_id, arguments, idempotency_key))
        return deepcopy(self.execution)


@pytest.mark.asyncio
async def test_qualification_proves_protocol_science_idempotency_and_cancellation():
    executor = QualificationExecutor(completed_execution())
    report = await QualificationEvaluator(executor).evaluate(
        qualification_draft(), acceptance_suite()
    )

    assert report.status == "passed"
    assert report.protocol_passed is True
    assert report.scientific_passed is True
    assert report.cases[0].protocol_assertions == {
        "invalid_inputs_rejected": True,
        "terminal_report_valid": True,
        "usage_complete": True,
        "artifacts_valid": True,
        "idempotent": True,
        "cancellation_confirmed": True,
        "progress_ordered": True,
    }
    assert report.cases[0].scientific_assertions == {"assertion_0": True}
    assert report.cases[0].usage_source == "service_reported"
    assert len(executor.calls) == 2
    assert executor.calls[0][2] == executor.calls[1][2]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutate", "assertion"),
    [
        (lambda execution: execution["report"]["usage"].pop("gpu_device_ms"), "usage_complete"),
        (lambda execution: execution["report"].update(result={"wrong": []}), "terminal_report_valid"),
        (lambda execution: execution["report"]["artifacts"][0].update(available=False), "artifacts_valid"),
        (lambda execution: execution.update(events=[{"sequence": 2, "type": "stage.started"},
                                                     {"sequence": 1, "type": "stage.completed"}]),
         "progress_ordered"),
        (lambda execution: execution.update(cancellation={"requested": True, "confirmed": False}),
         "cancellation_confirmed"),
    ],
)
async def test_qualification_reports_each_protocol_failure_without_claiming_science(mutate, assertion):
    execution = completed_execution()
    mutate(execution)
    report = await QualificationEvaluator(QualificationExecutor(execution)).evaluate(
        qualification_draft(), acceptance_suite()
    )

    assert report.status == "failed"
    assert report.protocol_passed is False
    assert report.cases[0].protocol_assertions[assertion] is False
