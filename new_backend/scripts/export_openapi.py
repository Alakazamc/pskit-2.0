import json
from pathlib import Path

from pydantic import TypeAdapter

from app.contracts.conversation import RunEvent
from app.main import create_app


def _typescript_type(schema: dict, *, event: bool = False) -> str:
    if "$ref" in schema:
        reference = schema["$ref"]
        if not reference.startswith("#/$defs/"):
            raise ValueError(f"Unsupported event schema reference: {reference}")
        return reference.removeprefix("#/$defs/")
    if "const" in schema:
        return json.dumps(schema["const"], ensure_ascii=False)
    if "enum" in schema:
        return " | ".join(json.dumps(value, ensure_ascii=False) for value in schema["enum"])
    if "anyOf" in schema:
        return " | ".join(_typescript_type(part) for part in schema["anyOf"])
    kind = schema.get("type")
    if kind == "string":
        return "string"
    if kind in {"integer", "number"}:
        return "number"
    if kind == "boolean":
        return "boolean"
    if kind == "null":
        return "null"
    if kind == "array":
        return f"({_typescript_type(schema['items'])})[]"
    if kind == "object":
        properties = schema.get("properties", {})
        if not properties:
            raise ValueError("Event schema object has no properties")
        required = set(schema.get("required", []))
        if event:
            # SSE serializes the whole Pydantic model, including these defaulted fields.
            required.update({"id", "run_id", "type", "data"})
        members = [
            f"  {json.dumps(name)}{'?' if name not in required else ''}: "
            f"{_typescript_type(value)};"
            for name, value in properties.items()
        ]
        return "{\n" + "\n".join(members) + "\n}"
    raise ValueError(f"Unsupported event schema type: {kind}")


def render_event_types(schema: dict) -> str:
    definitions = schema["$defs"]
    variants = [part["$ref"].removeprefix("#/$defs/") for part in schema["anyOf"]]
    if not variants or any(name not in definitions for name in variants):
        raise ValueError("RunEvent schema is missing event variants")
    lines = ["// Generated from contracts/run-event.schema.json. Do not edit by hand.", ""]
    for name, definition in definitions.items():
        lines.append(
            f"export type {name} = {_typescript_type(definition, event=name in variants)};"
        )
        lines.append("")
    lines.append("export type RunEvent =\n  " + "\n  | ".join(variants) + ";")
    return "\n".join(lines) + "\n"


def main() -> None:
    directory = Path(__file__).resolve().parents[2] / "contracts"
    event_schema = TypeAdapter(RunEvent).json_schema()
    for name, schema in (
        ("openapi.json", create_app().openapi()),
        ("run-event.schema.json", event_schema),
    ):
        (directory / name).write_text(
            json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    frontend_types = directory.parent / "new_frontend" / "src" / "api" / "generated-events.ts"
    frontend_types.write_text(render_event_types(event_schema), encoding="utf-8")


if __name__ == "__main__":
    main()
