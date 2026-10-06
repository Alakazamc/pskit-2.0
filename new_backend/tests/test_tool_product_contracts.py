from copy import deepcopy
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.contracts.tool_products import (
    CapabilityBinding,
    ProductAction,
    PublishedToolProduct,
    QualificationReport,
    ToolProductDraft,
    ToolProductPage,
    ToolRunEvent,
    ToolRunSnapshot,
    ToolUiSchema,
)
from app.domain.tool_products.ui_schema import validate_tool_ui


def localized(english: str, chinese: str) -> dict:
    return {"en": english, "zh-CN": chinese}


def binding(**overrides) -> dict:
    value = {
        "binding_id": "binding-coral-one-shot",
        "product_action_id": "coral-one-shot",
        "service_id": "coral",
        "service_revision": 3,
        "capability_id": "coral.generate.one-shot",
        "capability_version": "1.0.0",
        "adapter": "immediate_mcp",
        "submit_tool": "generate_rna_for_protein",
        "remote_output_schema": {
            "type": "object",
            "properties": {"result": {"type": "object"}},
            "required": ["result"],
        },
        "result_schema": {
            "type": "object",
            "properties": {"candidates": {"type": "array"}},
            "required": ["candidates"],
        },
        "result_mapping": {"kind": "json_pointer", "pointer": "/result"},
    }
    value.update(overrides)
    return value


def ui_schema(**overrides) -> dict:
    value = {
        "schema_version": "pskit.tool-ui.v1",
        "product": {
            "slug": "coral",
            "title": localized("CORAL RNA Design", "CORAL RNA 设计"),
            "description": localized(
                "Generate RNA candidates from a protein target",
                "从蛋白质目标生成 RNA 候选",
            ),
        },
        "page": {"layout": "split-workspace", "input_width": 5, "result_width": 7},
        "state": {"mode": {"initial": "one_shot"}},
        "sections": [
            {
                "id": "target",
                "title": localized("Target", "目标"),
                "fields": [
                    {
                        "id": "target-protein",
                        "component": "protein-input",
                        "label": localized("Protein", "蛋白质"),
                        "input_pointer": "/form/protein",
                        "required": True,
                    }
                ],
            }
        ],
        "actions": [
            {
                "id": "run",
                "label": localized("Run CORAL", "运行 CORAL"),
                "kind": "start_run",
                "target": {"action_id": "coral-one-shot"},
            }
        ],
        "result_views": [
            {
                "id": "candidates",
                "component": "sequence-table",
                "title": localized("Candidates", "候选序列"),
                "source": "/run/result/candidates",
                "preview_limit": 100,
                "full_data_artifact": "/run/artifacts/candidates_csv",
            }
        ],
        "handoffs": [],
    }
    value.update(overrides)
    return value


def draft() -> dict:
    return {
        "product_id": "product-coral",
        "slug": "coral",
        "revision": 1,
        "owner_user_id": "alice",
        "title": localized("CORAL", "CORAL"),
        "description": localized("RNA design", "RNA 设计"),
        "ui_schema": ui_schema(),
        "bindings": [binding()],
        "actions": [
            {
                "id": "coral-one-shot",
                "label": localized("One-shot generation", "一次生成"),
                "kind": "capability",
                "binding_ids": ["binding-coral-one-shot"],
            }
        ],
        "visibility": {"audience": "members", "allowed_user_ids": []},
        "acceptance_suite_id": "coral-suite",
        "acceptance_suite_revision": 2,
        "handoffs": [],
    }


def test_valid_ui_contract_is_versioned_bilingual_and_has_canonical_digest():
    schema = ToolUiSchema.model_validate(ui_schema())
    parsed_bindings = [CapabilityBinding.model_validate(binding())]

    first = validate_tool_ui(schema, parsed_bindings)
    second = validate_tool_ui(
        ToolUiSchema.model_validate(schema.model_dump(mode="json", by_alias=True)),
        parsed_bindings,
    )

    assert schema.schema_version == "pskit.tool-ui.v1"
    assert schema.product.title.en == "CORAL RNA Design"
    assert schema.product.title.zh_cn == "CORAL RNA 设计"
    assert first == second
    assert len(first) == 64


def test_result_mapping_accepts_the_rfc6901_empty_root_pointer():
    root_binding = CapabilityBinding.model_validate(binding(
        remote_output_schema={"type": "object", "properties": {"candidates": {"type": "array"}}},
        result_schema={"type": "object", "properties": {"candidates": {"type": "array"}}},
        result_mapping={"kind": "json_pointer", "pointer": ""},
    ))

    assert validate_tool_ui(ToolUiSchema.model_validate(ui_schema()), [root_binding])


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("schema_version",), "pskit.tool-ui.v2"),
        (("page", "layout"), "provider-html"),
        (("sections", 0, "fields", 0, "component"), "javascript-widget"),
        (("result_views", 0, "component"), "iframe"),
    ],
)
def test_schema_version_layout_and_components_are_allowlisted(path, value):
    payload = ui_schema()
    cursor = payload
    for part in path[:-1]:
        cursor = cursor[part]
    cursor[path[-1]] = value

    with pytest.raises(ValidationError):
        ToolUiSchema.model_validate(payload)


def test_localized_copy_requires_both_languages():
    payload = ui_schema()
    payload["product"]["title"] = {"en": "CORAL"}

    with pytest.raises(ValidationError):
        ToolUiSchema.model_validate(payload)


def test_nested_contracts_forbid_unknown_fields():
    payload = ui_schema()
    payload["sections"][0]["fields"][0]["css"] = "position: fixed"

    with pytest.raises(ValidationError):
        ToolUiSchema.model_validate(payload)


@pytest.mark.parametrize("collection", ["sections", "actions", "result_views"])
def test_ui_ids_are_unique(collection):
    payload = ui_schema()
    payload[collection].append(deepcopy(payload[collection][0]))

    with pytest.raises(ValidationError, match="unique"):
        ToolUiSchema.model_validate(payload)


def test_draft_checks_product_action_binding_references():
    payload = draft()
    payload["actions"][0]["binding_ids"] = ["missing-binding"]

    with pytest.raises(ValidationError, match="missing-binding"):
        ToolProductDraft.model_validate(payload)


def test_capability_binding_keeps_remote_and_business_result_schemas_separate():
    parsed = CapabilityBinding.model_validate(binding())

    assert "result" in parsed.remote_output_schema["properties"]
    assert "candidates" in parsed.result_schema["properties"]
    assert parsed.remote_output_schema is not parsed.result_schema


@pytest.mark.parametrize("schema_field", ["remote_output_schema", "result_schema"])
def test_external_json_schema_references_are_rejected(schema_field):
    unsafe = binding(**{schema_field: {"$ref": "https://example.org/schema.json"}})

    with pytest.raises(ValueError, match="external.*ref"):
        validate_tool_ui(
            ToolUiSchema.model_validate(ui_schema()),
            [CapabilityBinding.model_validate(unsafe)],
        )


@pytest.mark.parametrize(
    "pointer",
    ["run/result/candidates", "/run/~2secret", "/run/../secret", "/other/value"],
)
def test_invalid_or_escaping_json_pointers_are_rejected(pointer):
    payload = ui_schema()
    payload["result_views"][0]["source"] = pointer

    with pytest.raises((ValidationError, ValueError), match="pointer|Pointer"):
        validate_tool_ui(
            ToolUiSchema.model_validate(payload),
            [CapabilityBinding.model_validate(binding())],
        )


@pytest.mark.parametrize(
    ("collection", "key", "value"),
    [
        ("result_views", "expression", "fetch('https://example.org')"),
        ("result_views", "url", "https://example.org/data"),
        ("result_views", "transform", "arbitrary-javascript"),
        ("actions", "script", "window.location='https://example.org'"),
    ],
)
def test_expression_network_and_unknown_transform_keys_are_rejected(collection, key, value):
    payload = ui_schema()
    payload[collection][0][key] = value

    with pytest.raises(ValidationError):
        ToolUiSchema.model_validate(payload)


def test_ui_action_must_resolve_to_a_published_binding_action():
    payload = ui_schema()
    payload["actions"][0]["target"] = {"action_id": "missing-action"}

    with pytest.raises(ValueError, match="missing-action"):
        validate_tool_ui(
            ToolUiSchema.model_validate(payload),
            [CapabilityBinding.model_validate(binding())],
        )


def test_state_selected_ui_actions_must_all_resolve_to_bindings():
    payload = ui_schema()
    payload["actions"][0]["target"] = {
        "by_state": {
            "source": "/form/mode",
            "map": {"one_shot": "coral-one-shot", "iterative": "coral-iterative"},
        }
    }

    with pytest.raises(ValueError, match="coral-iterative"):
        validate_tool_ui(
            ToolUiSchema.model_validate(payload),
            [CapabilityBinding.model_validate(binding())],
        )


def test_public_and_run_contracts_are_strict_and_serializable():
    now = datetime(2026, 10, 6, tzinfo=UTC)
    parsed_draft = ToolProductDraft.model_validate(draft())
    release = PublishedToolProduct(
        release_id="release-1",
        product_id=parsed_draft.product_id,
        slug=parsed_draft.slug,
        revision=parsed_draft.revision,
        title=parsed_draft.title,
        description=parsed_draft.description,
        ui_schema=parsed_draft.ui_schema,
        actions=parsed_draft.actions,
        state="published",
        published_at=now,
    )
    page = ToolProductPage(items=[release])
    report = QualificationReport(
        report_id="report-1",
        product_id="product-coral",
        product_revision=1,
        service_revision=3,
        binding_digest="a" * 64,
        ui_digest="b" * 64,
        suite_digest="c" * 64,
        status="passed",
        protocol_passed=True,
        scientific_passed=True,
        cases=[],
        qualified_at=now,
    )
    run = ToolRunSnapshot(
        run_id="run-1",
        product_slug="coral",
        release_id="release-1",
        action_id="coral-one-shot",
        user_id="alice",
        status="queued",
        progress=0,
        result=None,
        artifacts=[],
        usage=None,
        created_at=now,
        updated_at=now,
    )
    event = ToolRunEvent(
        event_id="event-1",
        run_id="run-1",
        sequence=1,
        type="run.queued",
        data={},
        created_at=now,
    )

    assert page.items[0].slug == "coral"
    assert report.status == "passed"
    assert run.status == "queued"
    assert event.sequence == 1
    with pytest.raises(ValidationError):
        ProductAction.model_validate(
            {
                "id": "bad",
                "label": localized("Bad", "错误"),
                "kind": "capability",
                "binding_ids": ["binding-coral-one-shot"],
                "endpoint_url": "https://secret.example.org",
            }
        )
