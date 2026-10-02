from fastapi import HTTPException
from jsonschema import Draft202012Validator, ValidationError

from app.ports.providers import McpProvider


def validate_mcp_arguments(mcp: McpProvider, name: str, arguments: dict) -> None:
    """Validate an MCP invocation against its discovered JSON Schema."""
    tool = next((item for item in mcp.tools() if item.name == name), None)
    if tool is None:
        raise HTTPException(status_code=404, detail="Tool not found")
    try:
        Draft202012Validator(tool.input_schema).validate(arguments)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422, detail={"code": "INVALID_TOOL_ARGUMENTS"},
        ) from exc
