from pathlib import Path

import yaml

from app.contracts.tool_products import AcceptanceSuite, ToolProductDraft
from app.domain.tool_products.ui_schema import validate_tool_ui

FIXTURES = Path(__file__).parents[1] / "fixtures" / "tool_products"


def _load(name: str):
    return yaml.safe_load((FIXTURES / name).read_text(encoding="utf-8"))


def test_coral_product_fixture_exposes_four_stable_capabilities():
    product = ToolProductDraft.model_validate(_load("coral.product.yaml"))

    assert {binding.capability_id for binding in product.bindings} == {
        "coral.generate.one_shot",
        "coral.generate.iterative",
        "coral.analyze.pocket",
        "coral.optimize.two_dimensional",
    }
    assert {binding.submit_tool for binding in product.bindings} == {
        binding.capability_id for binding in product.bindings
    }
    assert all(binding.adapter == "immediate_mcp" for binding in product.bindings)
    assert product.visibility.audience == "members"
    assert validate_tool_ui(product.ui_schema, product.bindings)


def test_coral_acceptance_fixture_blocks_publication_until_pocket_case_runs():
    raw = _load("coral.acceptance.yaml")
    suite = AcceptanceSuite.model_validate(raw["suite"])
    cases = {case.action_id: case for case in suite.cases}
    evidence = raw["evidence"]

    assert set(cases) == {
        "coral-one-shot",
        "coral-iterative",
        "coral-pocket",
        "coral-two-dimensional",
    }
    assert evidence["cases"]["coral-one-shot"]["status"] == "passed"
    assert evidence["cases"]["coral-iterative"]["status"] == "passed"
    assert evidence["cases"]["coral-two-dimensional"]["status"] == "passed"
    assert evidence["cases"]["coral-pocket"]["status"] == "blocked"
    assert evidence["cases"]["coral-pocket"]["blocker"] == "AF3_WORKER_UNAVAILABLE"
    assert raw["publication"]["eligible"] is False
    assert raw["publication"]["required_case_status"] == "passed"


def test_coral_real_case_evidence_records_usage_and_artifact_digests():
    evidence = _load("coral.acceptance.yaml")["evidence"]["cases"]

    for case_id in ("coral-one-shot", "coral-iterative", "coral-two-dimensional"):
        case = evidence[case_id]
        assert case["usage"]["wall_ms"] > 0
        assert case["usage"]["cpu_core_ms"] > 0
        assert case["artifacts"]
        assert all(len(item["sha256"]) == 64 for item in case["artifacts"])

    assert evidence["coral-one-shot"]["usage"]["gpu_device_ms"] > 0
    assert evidence["coral-iterative"]["usage"]["gpu_device_ms"] > 0
    assert evidence["coral-two-dimensional"]["usage"]["gpu_device_ms"] is None
