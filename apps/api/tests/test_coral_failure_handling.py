"""Tests for CORAL reports at the generic MCP accounting boundary."""

import pytest
from mcp import types

from app.contracts.compute import Completed, Failed
from pskit_compute.service import ProtocolError
from tests.test_dynamic_mcp_receiver import binding, dynamic_grant


@pytest.mark.asyncio
async def test_coral_pdb_download_failure_without_usage_requires_reconciliation():
    """A receiver must not invent zero usage for a provider failure."""
    from pskit_compute.dynamic_mcp import DynamicMcpAdapter

    # Create test binding and grant
    test_binding = binding(
        adapter="immediate_mcp",
        endpoint_url="https://coral.example.org/mcp",
        credential_ref="coral-key"
    )
    grant = dynamic_grant(
        required_usage=("gpu_device_ms",),
        execution_binding=test_binding
    )

    # Mock adapter
    adapter = DynamicMcpAdapter(
        test_binding,
        endpoint_url="https://coral.example.org/mcp",
        credential="test-key"
    )

    # Mock CORAL service returning failure without usage
    # This simulates PDB download failure before GPU computation
    mock_response = types.CallToolResult(
        content=[],
        structuredContent={
            "status": "failed",
            "error": {
                "code": "PDB_DOWNLOAD_FAILED",
                "message": "Failed to download PDB structure 1ABC from RCSB"
            }
            # Note: no "usage" field - this is the key scenario
        }
    )

    with pytest.raises(ProtocolError, match="MCP_FAILED_USAGE_MISSING"):
        adapter._report(mock_response, grant)


@pytest.mark.asyncio
async def test_coral_failure_with_partial_usage():
    """Test that failures with partial usage are properly accepted."""
    from pskit_compute.dynamic_mcp import DynamicMcpAdapter

    test_binding = binding(adapter="immediate_mcp")
    grant = dynamic_grant(
        required_usage=("gpu_device_ms",), execution_binding=test_binding
    )

    adapter = DynamicMcpAdapter(
        test_binding,
        endpoint_url="https://coral.example.org/mcp",
        credential="test-key"
    )

    # Task that started GPU computation but failed mid-way
    mock_response = types.CallToolResult(
        content=[],
        structuredContent={
            "status": "failed",
            "error": {
                "code": "CORAL_COMPUTATION_FAILED",
                "message": "Structure optimization failed to converge"
            },
            "usage": {
                "source": "measured",
                "gpu_device_ms": 45_200
            }
        }
    )

    result = adapter._report(mock_response, grant)

    assert isinstance(result, Failed)
    assert result.usage.gpu_device_ms == 45_200
    assert result.error.code == "CORAL_COMPUTATION_FAILED"


@pytest.mark.asyncio
async def test_coral_timeout_without_usage_requires_reconciliation():
    """Preparation timeouts also retain the quota hold until reconciled."""
    from pskit_compute.dynamic_mcp import DynamicMcpAdapter

    test_binding = binding(adapter="immediate_mcp")
    grant = dynamic_grant(
        required_usage=("gpu_device_ms",), execution_binding=test_binding
    )

    adapter = DynamicMcpAdapter(
        test_binding,
        endpoint_url="https://coral.example.org/mcp",
        credential="test-key"
    )

    # Timeout before any computation started
    mock_response = types.CallToolResult(
        content=[],
        structuredContent={
            "status": "failed",
            "error": {
                "code": "TIMEOUT",
                "message": "Task exceeded preparation timeout"
            }
            # No usage - timed out during preparation
        }
    )

    with pytest.raises(ProtocolError, match="MCP_FAILED_USAGE_MISSING"):
        adapter._report(mock_response, grant)


@pytest.mark.asyncio
async def test_successful_coral_task_with_usage():
    """Test successful CORAL task with proper usage reporting."""
    from pskit_compute.dynamic_mcp import DynamicMcpAdapter

    test_binding = binding(
        adapter="immediate_mcp",
        result_mapping={"kind": "json_pointer", "pointer": "/result"}
    )
    grant = dynamic_grant(
        required_usage=("gpu_device_ms",),
        execution_binding=test_binding,
        output_schema={"type": "object"}
    )

    adapter = DynamicMcpAdapter(
        test_binding,
        endpoint_url="https://coral.example.org/mcp",
        credential="test-key"
    )

    mock_response = types.CallToolResult(
        content=[],
        structuredContent={
            "status": "completed",
            "result": {
                "structure_url": "https://artifacts.example.org/result.pdb",
                "confidence": 0.95
            },
            "usage": {
                "source": "measured",
                "gpu_device_ms": 120_500
            },
            "artifacts": [
                {
                    "id": "structure-1",
                    "name": "optimized_structure.pdb",
                    "kind": "chemical/x-pdb",
                    "size": 102400,
                    "available": True
                }
            ]
        }
    )

    result = adapter._report(mock_response, grant)

    assert isinstance(result, Completed)
    assert result.usage.gpu_device_ms == 120_500
    assert len(result.artifacts) == 1
    assert result.artifacts[0].id == "structure-1"


@pytest.mark.asyncio
async def test_mcp_report_size_limit():
    """Test that oversized reports are rejected."""
    from pskit_compute.dynamic_mcp import DynamicMcpAdapter

    test_binding = binding(adapter="immediate_mcp")
    adapter = DynamicMcpAdapter(
        test_binding,
        endpoint_url="https://example.org/mcp",
        credential="test-key"
    )

    # Create a response that exceeds MAX_REPORT_BYTES (1MB)
    large_data = "x" * (2 * 1024 * 1024)  # 2MB
    mock_response = types.CallToolResult(
        content=[],
        structuredContent={
            "status": "completed",
            "result": {"data": large_data},
            "usage": {"source": "test"}
        }
    )

    with pytest.raises(ProtocolError) as exc_info:
        adapter._data(mock_response)

    assert str(exc_info.value) == "MCP_REPORT_TOO_LARGE"


@pytest.mark.asyncio
async def test_invalid_output_schema():
    """Test that invalid output schemas are rejected."""
    from pskit_compute.dynamic_mcp import DynamicMcpAdapter

    test_binding = binding(
        adapter="immediate_mcp",
        remote_output_schema={
            "type": "object",
            "required": ["status", "result"],
            "properties": {
                "status": {"type": "string"},
                "result": {"type": "object"}
            }
        }
    )
    grant = dynamic_grant(execution_binding=test_binding)

    adapter = DynamicMcpAdapter(
        test_binding,
        endpoint_url="https://example.org/mcp",
        credential="test-key"
    )

    # Response missing required "result" field
    mock_response = types.CallToolResult(
        content=[],
        structuredContent={
            "status": "completed"
            # Missing "result" field
        }
    )

    with pytest.raises(ProtocolError) as exc_info:
        adapter._report(mock_response, grant)

    assert str(exc_info.value) == "MCP_REMOTE_OUTPUT_INVALID"
