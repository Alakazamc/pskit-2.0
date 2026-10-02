import json
from pathlib import Path

from pydantic import TypeAdapter

from app.contracts.conversation import RunEvent
from app.main import create_app
from scripts.export_openapi import render_event_types

CONTRACTS = Path(__file__).resolve().parents[2] / "contracts"


def test_exported_contracts_match_python_models() -> None:
    assert json.loads((CONTRACTS / "openapi.json").read_text(encoding="utf-8")) == create_app().openapi()
    assert json.loads((CONTRACTS / "run-event.schema.json").read_text(encoding="utf-8")) == TypeAdapter(RunEvent).json_schema()


def test_exported_sse_data_schema_includes_task_label() -> None:
    contract = CONTRACTS / "run-event.schema.json"
    schema = json.loads(contract.read_text(encoding="utf-8"))
    task = schema["$defs"]["TaskUpdatedEvent"]
    data = schema["$defs"]["TaskUpdatedData"]

    assert task["properties"]["type"]["const"] == "task.updated"
    assert "label" in data["properties"]


def test_approval_response_always_declares_nullable_job_id() -> None:
    schema = create_app().openapi()["components"]["schemas"]["ApprovalDecisionResponse"]
    assert "job_id" in schema["required"]


def test_generated_frontend_sse_types_match_python_event_schema() -> None:
    schema = TypeAdapter(RunEvent).json_schema()
    generated = CONTRACTS.parent / "new_frontend" / "src" / "api" / "generated-events.ts"
    assert generated.read_text(encoding="utf-8") == render_event_types(schema)
