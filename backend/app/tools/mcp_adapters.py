from __future__ import annotations

import json
from collections.abc import Iterable


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


def build_pepccd_mcp_payload(args: dict) -> dict:
    protein_sequence = "".join(
        character
        for character in str(args.get("protein_sequence") or "")
        if not character.isspace() and character != "-"
    ).upper()
    if not protein_sequence:
        raise MCPAdapterError("protein_sequence is required")
    if len(protein_sequence) > 4096:
        raise MCPAdapterError("protein_sequence must contain at most 4096 residues")

    payload = {
        "protein_sequence": protein_sequence,
        "num_peptides": int(args.get("num_peptides", 10)),
        "peptide_length": int(args.get("peptide_length", 15)),
        "sample_batch_size": int(args.get("sample_batch_size", 100)),
        "temperature": float(args.get("temperature", 1.0)),
        "seed": int(args["seed"]) if args.get("seed") is not None else None,
        "device": str(args.get("device", "cuda:0")),
    }
    if not 1 <= payload["num_peptides"] <= 1000:
        raise MCPAdapterError("num_peptides must be between 1 and 1000")
    if not 1 <= payload["peptide_length"] <= 40:
        raise MCPAdapterError("peptide_length must be between 1 and 40")
    if not 1 <= payload["sample_batch_size"] <= 500:
        raise MCPAdapterError("sample_batch_size must be between 1 and 500")
    if not 0 < payload["temperature"] <= 5:
        raise MCPAdapterError("temperature must be greater than 0 and at most 5")
    if payload["seed"] is not None and not 0 <= payload["seed"] <= 4294967295:
        raise MCPAdapterError("seed must be between 0 and 4294967295")
    if payload["device"] not in {"cuda:0", "cuda:1", "cpu"}:
        raise MCPAdapterError("device must be one of cuda:0, cuda:1, or cpu")
    return payload


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
