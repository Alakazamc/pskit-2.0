"""Fail-closed validation for declarative Tool Product user interfaces."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from app.contracts.tool_products import (
    CapabilityBinding,
    StateActionTarget,
    ToolUiCondition,
    ToolUiField,
    ToolUiSchema,
)
from app.domain.compute.common import payload_hash

_POINTER_ESCAPE = re.compile(r"~(?:[^01]|$)")
_UI_POINTER_ROOTS = {"form", "run", "product"}


def _validate_pointer(pointer: str, *, roots: set[str] | None = None) -> None:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError(f"invalid JSON Pointer: {pointer!r}")
    if _POINTER_ESCAPE.search(pointer):
        raise ValueError(f"invalid JSON Pointer escape: {pointer!r}")
    segments = pointer[1:].split("/") if pointer != "/" else [""]
    decoded = [segment.replace("~1", "/").replace("~0", "~") for segment in segments]
    if any(segment in {".", ".."} for segment in decoded):
        raise ValueError(f"escaping JSON Pointer is not allowed: {pointer!r}")
    if roots is not None and (not decoded or decoded[0] not in roots):
        raise ValueError(f"JSON Pointer root is not allowed: {pointer!r}")


def _walk_schema(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield key, item
            yield from _walk_schema(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_schema(item)


def _validate_schema_refs(schema: dict[str, Any], *, label: str) -> None:
    for key, value in _walk_schema(schema):
        if key not in {"$ref", "$dynamicRef"}:
            continue
        if not isinstance(value, str) or not value.startswith("#"):
            raise ValueError(f"{label} contains an external schema ref")
        if value not in {"#", ""}:
            fragment = value[1:]
            _validate_pointer(fragment)


def _conditions(condition: ToolUiCondition | None) -> Iterable[ToolUiCondition]:
    if condition is None:
        return
    yield condition
    for children in (condition.and_conditions, condition.or_conditions):
        for child in children or []:
            yield from _conditions(child)
    if condition.not_condition is not None:
        yield from _conditions(condition.not_condition)


def _fields(fields: list[ToolUiField]) -> Iterable[ToolUiField]:
    for field in fields:
        yield field
        yield from _fields(field.fields)


def _validate_ui_pointers(schema: ToolUiSchema) -> None:
    for section in schema.sections:
        for condition in _conditions(section.visible_when):
            if condition.source:
                _validate_pointer(condition.source, roots=_UI_POINTER_ROOTS)
        for field in _fields(section.fields):
            if field.input_pointer:
                _validate_pointer(field.input_pointer, roots={"form"})
            for condition in _conditions(field.visible_when):
                if condition.source:
                    _validate_pointer(condition.source, roots=_UI_POINTER_ROOTS)
    for action in schema.actions:
        if isinstance(action.target, StateActionTarget):
            _validate_pointer(action.target.by_state.source, roots={"form"})
        for condition in _conditions(action.visible_when):
            if condition.source:
                _validate_pointer(condition.source, roots=_UI_POINTER_ROOTS)
    for view in schema.result_views:
        _validate_pointer(view.source, roots=_UI_POINTER_ROOTS)
        if view.full_data_artifact:
            _validate_pointer(view.full_data_artifact, roots={"run"})
        for condition in _conditions(view.visible_when):
            if condition.source:
                _validate_pointer(condition.source, roots=_UI_POINTER_ROOTS)
    for handoff in schema.handoffs:
        _validate_pointer(handoff.summary_source, roots={"run"})
        for pointer in handoff.artifact_sources:
            _validate_pointer(pointer, roots={"run"})


def validate_tool_ui(schema: ToolUiSchema, bindings: list[CapabilityBinding]) -> str:
    """Validate all cross-object references and return the canonical UI digest."""

    _validate_ui_pointers(schema)
    published_actions = {binding.product_action_id for binding in bindings}
    for binding in bindings:
        _validate_schema_refs(binding.remote_output_schema, label="remote output schema")
        _validate_schema_refs(binding.result_schema, label="business result schema")
        _validate_pointer(binding.result_mapping.pointer)

    for action in schema.actions:
        if isinstance(action.target, StateActionTarget):
            action_ids = set(action.target.by_state.map.values())
        else:
            action_ids = {action.target.action_id}
        missing = action_ids - published_actions
        if missing:
            raise ValueError(f"UI action references unavailable product action: {min(missing)}")

    return payload_hash(schema.model_dump(mode="json", by_alias=True))
