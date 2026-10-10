"""AF3 is a configuration-only Tool Product with a recoverable job binding."""

from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from app.contracts.tool_products import AcceptanceSuite, ToolProductDraft
from app.domain.tool_products.ui_schema import validate_tool_ui


def test_af3_product_exposes_validated_json_input_and_owned_artifact_output():
    root = Path(__file__).parents[1] / "fixtures/tool_products"
    product = ToolProductDraft.model_validate(yaml.safe_load((root / "af3.product.yaml").read_text()))
    suite = AcceptanceSuite.model_validate(yaml.safe_load((root / "af3.acceptance.yaml").read_text()))
    assert product.slug == "af3"
    assert validate_tool_ui(product.ui_schema, product.bindings)
    binding = product.bindings[0]
    assert binding.adapter == "job_mcp"
    assert (binding.submit_tool, binding.status_tool, binding.artifact_tool) == (
        "af3.submit", "af3.status", "pskit.artifacts.read",
    )
    assert binding.submit_job_id_argument == "task_id"
    assert binding.cancellation == "none"
    assert binding.accepted_sources == ["estimated", "measured", "service_reported"]
    assert "cpu_core_ms" not in binding.required_usage
    schema = product.actions[0].input_schema
    assert Draft202012Validator(schema).is_valid(suite.cases[0].arguments)
    assert not Draft202012Validator(schema).is_valid({**suite.cases[0].arguments, "task_id": "other"})
    assert not Draft202012Validator(schema).is_valid({"fold_input": {"name": "empty"}})
    assert any(view.component == "artifact-list" for view in product.ui_schema.result_views)
