from pathlib import Path

import pytest
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


def test_coral_acceptance_fixture_allows_publication_after_four_real_cases_pass():
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
    assert evidence["cases"]["coral-pocket"]["status"] == "passed"
    assert raw["publication"]["eligible"] is True
    assert raw["publication"]["required_case_status"] == "passed"


def test_coral_real_case_evidence_records_usage_and_artifact_digests():
    evidence = _load("coral.acceptance.yaml")["evidence"]["cases"]

    for case_id in (
        "coral-one-shot",
        "coral-iterative",
        "coral-pocket",
        "coral-two-dimensional",
    ):
        case = evidence[case_id]
        assert case["usage"]["wall_ms"] > 0
        assert case["usage"]["cpu_core_ms"] > 0
        assert case["artifacts"]
        assert all(len(item["sha256"]) == 64 for item in case["artifacts"])

    assert evidence["coral-one-shot"]["usage"]["gpu_device_ms"] > 0
    assert evidence["coral-iterative"]["usage"]["gpu_device_ms"] > 0
    assert evidence["coral-pocket"]["usage"]["gpu_device_ms"] > 0
    assert evidence["coral-two-dimensional"]["usage"]["gpu_device_ms"] is None


def test_coral_staging_suite_is_a_single_low_cost_real_case():
    raw = _load("coral.staging.acceptance.yaml")
    suite = AcceptanceSuite.model_validate(raw["suite"])

    assert suite.suite_id == "coral-provider-staging-smoke"
    assert len(suite.cases) == 1
    assert suite.cases[0].case_id == "coral-one-shot-6fxb-a"
    assert suite.cases[0].action_id == "coral-one-shot"
    assert raw["scope"]["canonical_suite"] == "coral-provider-real-cases"
    assert raw["scope"]["canonical_real_cases_passed"] == 4


def test_coral_staging_product_exposes_only_the_smoke_action():
    product = ToolProductDraft.model_validate(_load("coral.staging.product.yaml"))

    assert product.product_id == "product-coral-staging-smoke"
    assert product.slug == "coral-staging-smoke"
    assert product.acceptance_suite_id == "coral-provider-staging-smoke"
    assert [action.id for action in product.actions] == ["coral-one-shot"]
    assert [binding.capability_id for binding in product.bindings] == [
        "coral.generate.one_shot"
    ]
    assert validate_tool_ui(product.ui_schema, product.bindings)


@pytest.mark.asyncio
async def test_cpu_only_analysis_accepts_reported_cpu_without_inventing_gpu_usage():
    from app.domain.admin.qualification import QualificationEvaluator

    class CpuProvider:
        async def execute(self, binding, arguments, idempotency_key):
            return {
                'remote_execution_id': 'cpu-analysis-1',
                'report': {
                    'status': 'completed',
                    'result': {'sample_size': 100, 'molecule': 'dna',
                               'structure_skipped': True, 'elapsed_seconds': 0.5},
                    'usage': {'wall_ms': 500, 'cpu_core_ms': 200,
                              'gpu_device_ms': None, 'gpu_count': 0, 'source': 'unknown'},
                    'artifacts': [],
                },
                'events': [],
            }

    product = ToolProductDraft.model_validate(_load('coral.product.yaml'))
    raw_suite = _load('coral.acceptance.yaml')['suite']
    raw_suite['cases'] = [raw_suite['cases'][-1]]
    suite = AcceptanceSuite.model_validate(raw_suite)
    report = await QualificationEvaluator(CpuProvider()).evaluate(product, suite)
    assert report.status == 'passed'
    assert report.cases[0].usage_source == 'unknown'
