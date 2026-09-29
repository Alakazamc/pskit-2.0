from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator


class MCPAdapterError(RuntimeError):
    pass


def safe_mcp_exception_message(source_label: str, exc: BaseException) -> str:
    """返回不包含第三方异常正文、URL 或凭证的稳定 MCP 错误。"""

    return f"{source_label} request failed ({exc.__class__.__name__})"


PEPCCD_MCP_INPUT_FIELDS = frozenset(
    {
        "protein_sequence",
        "num_peptides",
        "peptide_length",
        "sample_batch_size",
        "temperature",
        "seed",
        "device",
    }
)


class PepCCDParameters(BaseModel):
    """One validation contract for queue admission and the remote MCP request."""

    protein_sequence: str = Field(min_length=1, max_length=4096, pattern=r"^[ACDEFGHIKLMNPQRSTVWY]+$")
    num_peptides: int = Field(default=10, ge=1, le=1000)
    peptide_length: int = Field(default=15, ge=1, le=40)
    sample_batch_size: int = Field(default=100, ge=1, le=500)
    temperature: float = Field(default=1.0, gt=0, le=5)
    seed: int | None = Field(default=None, ge=0, le=4294967295)
    device: Literal["cuda:0", "cuda:1", "cpu"] = "cuda:0"

    @field_validator("protein_sequence", mode="before")
    @classmethod
    def normalize_protein(cls, value):
        if isinstance(value, str):
            return "".join(value.split()).replace("-", "").upper()
        return value


def build_pepccd_mcp_payload(args: dict) -> dict:
    try:
        return PepCCDParameters.model_validate(args).model_dump(mode="json")
    except ValidationError as exc:
        fields = sorted({str(error["loc"][0]) for error in exc.errors()})
        raise MCPAdapterError(f"Invalid PepCCD input fields: {', '.join(fields)}") from exc


def _tool_name(tool: object) -> str:
    if isinstance(tool, dict):
        return str(tool.get("name") or "")
    return str(getattr(tool, "name", "") or "")


def _tool_input_schema(tool: object) -> dict:
    if isinstance(tool, dict):
        value = tool.get("inputSchema") or tool.get("input_schema") or {}
    else:
        value = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None) or {}
    return value if isinstance(value, dict) else {}


def select_pepccd_mcp_tool_name(
    tools: Iterable[object],
    *,
    configured_name: str | None,
) -> str:
    tool_rows = list(tools)
    available_names = [name for item in tool_rows if (name := _tool_name(item))]
    if configured_name:
        configured_tool = next(
            (item for item in tool_rows if _tool_name(item) == configured_name),
            None,
        )
        if configured_tool is None:
            raise MCPAdapterError(
                f"Configured PepCCD MCP tool '{configured_name}' is unavailable; "
                f"available tools: {', '.join(available_names) or '(none)'}"
            )
        properties = _tool_input_schema(configured_tool).get("properties")
        property_names = set(properties) if isinstance(properties, dict) else set()
        missing = sorted(PEPCCD_MCP_INPUT_FIELDS - property_names)
        if missing:
            raise MCPAdapterError(
                f"Configured PepCCD MCP tool '{configured_name}' has an incompatible schema; "
                f"missing fields: {', '.join(missing)}"
            )
        return configured_name

    matches = []
    for tool in tool_rows:
        properties = _tool_input_schema(tool).get("properties")
        property_names = set(properties) if isinstance(properties, dict) else set()
        if PEPCCD_MCP_INPUT_FIELDS.issubset(property_names):
            matches.append(_tool_name(tool))
    matches = [name for name in matches if name]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise MCPAdapterError(
            "PepCCD MCP tool could not be discovered from its input schema; "
            "set PEPCCD_MCP_TOOL_NAME explicitly"
        )
    raise MCPAdapterError(
        "PepCCD MCP input schema matched multiple tools "
        f"({', '.join(matches)}); set PEPCCD_MCP_TOOL_NAME explicitly"
    )


def extract_mcp_result(result: object, *, source_label: str) -> object:
    if getattr(result, "isError", False):
        raise MCPAdapterError(f"{source_label} returned a tool-level error")
    structured = getattr(result, "structuredContent", None)
    if structured is None:
        structured = getattr(result, "structured_content", None)
    if isinstance(structured, (dict, list)):
        return structured

    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise MCPAdapterError(f"{source_label} returned non-JSON text") from exc
        if isinstance(parsed, (dict, list)):
            return parsed
    raise MCPAdapterError(f"{source_label} returned no structured JSON result")
