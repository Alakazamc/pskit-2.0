from copy import deepcopy
from datetime import UTC, datetime

import psycopg
import pytest

from app.contracts.tool_products import QualificationReport, ToolProductDraft
from app.db.postgres import PostgresDatabase
from app.db.postgres_migrations import migrate_postgres
from app.domain.compute.common import payload_hash
from app.domain.tool_products.repository import (
    QualificationStale,
    ToolProductRepository,
    ToolProductRevisionConflict,
)
from app.domain.tool_products.ui_schema import validate_tool_ui


def text(value):
    return {"en": value, "zh-CN": value}


def draft_payload(*, revision=1, audience="public", allowed=None):
    return {
        "product_id": "product-coral",
        "slug": "coral",
        "revision": revision,
        "owner_user_id": "alice",
        "title": text("CORAL"),
        "description": text("RNA design"),
        "ui_schema": {
            "schema_version": "pskit.tool-ui.v1",
            "product": {
                "slug": "coral",
                "title": text("CORAL"),
                "description": text("RNA design"),
            },
            "page": {"layout": "split-workspace", "input_width": 5, "result_width": 7},
            "state": {},
            "sections": [{
                "id": "target",
                "title": text("Target"),
                "fields": [{
                    "id": "protein", "component": "protein-input", "label": text("Protein"),
                    "input_pointer": "/form/protein", "required": True,
                }],
            }],
            "actions": [{
                "id": "run", "label": text("Run"), "kind": "start_run",
                "target": {"action_id": "coral-one-shot"},
            }],
            "result_views": [{
                "id": "sequences", "component": "sequence-table",
                "source": "/run/result/candidates",
            }],
            "handoffs": [],
        },
        "bindings": [{
            "binding_id": "binding-one-shot",
            "product_action_id": "coral-one-shot",
            "service_id": "coral",
            "service_revision": 4,
            "capability_id": "coral.generate.one-shot",
            "capability_version": "1.0.0",
            "adapter": "immediate_mcp",
            "submit_tool": "generate_rna_for_protein",
            "remote_output_schema": {
                "type": "object", "properties": {"result": {"type": "object"}},
            },
            "result_schema": {
                "type": "object", "properties": {"candidates": {"type": "array"}},
            },
            "result_mapping": {"kind": "json_pointer", "pointer": "/result"},
        }],
        "actions": [{
            "id": "coral-one-shot",
            "label": text("One shot"),
            "kind": "capability",
            "binding_ids": ["binding-one-shot"],
        }],
        "visibility": {"audience": audience, "allowed_user_ids": allowed or []},
        "acceptance_suite_id": "suite-coral",
        "acceptance_suite_revision": 2,
        "handoffs": [],
    }


@pytest.fixture
def product_repository(pg_schema):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    try:
        yield ToolProductRepository(database), database
    finally:
        database.close()


def report_for(draft, *, report_id="report-1", **overrides):
    ui_digest = validate_tool_ui(draft.ui_schema, draft.bindings)
    binding_digest = payload_hash([
        item.model_dump(mode="json", by_alias=True) for item in draft.bindings
    ])
    suite_digest = payload_hash({
        "suite_id": draft.acceptance_suite_id,
        "revision": draft.acceptance_suite_revision,
    })
    values = {
        "report_id": report_id,
        "product_id": draft.product_id,
        "product_revision": draft.revision,
        "service_revision": draft.bindings[0].service_revision,
        "binding_digest": binding_digest,
        "ui_digest": ui_digest,
        "suite_digest": suite_digest,
        "status": "passed",
        "protocol_passed": True,
        "scientific_passed": True,
        "cases": [],
        "qualified_at": datetime(2026, 10, 6, tzinfo=UTC),
    }
    values.update(overrides)
    return QualificationReport.model_validate(values)


def qualified_release(repository, payload=None, *, report_id="report-1"):
    saved = repository.save_draft(
        "alice", "product-coral", ToolProductDraft.model_validate(payload or draft_payload()), 0
    )
    report = report_for(saved, report_id=report_id)
    repository.record_qualification(report)
    return repository.publish("product-coral", saved.revision, report.report_id, "publisher")


def test_save_draft_uses_optimistic_revisions_and_preserves_history(product_repository):
    repository, database = product_repository
    first = repository.save_draft(
        "alice", "product-coral", ToolProductDraft.model_validate(draft_payload()), 0
    )
    second_payload = draft_payload(revision=2)
    second_payload["description"] = text("Updated RNA design")
    second = repository.save_draft(
        "alice", "product-coral", ToolProductDraft.model_validate(second_payload), 1
    )

    with pytest.raises(ToolProductRevisionConflict, match="REVISION_CONFLICT"):
        repository.save_draft(
            "alice", "product-coral", ToolProductDraft.model_validate(second_payload), 1
        )
    with database.connection() as connection:
        revisions = connection.execute(
            "SELECT revision FROM tool_product_revisions WHERE product_id=%s ORDER BY revision",
            ("product-coral",),
        ).fetchall()
    assert first.revision == 1 and second.revision == 2
    assert revisions == [(1,), (2,)]


def test_release_pins_exact_digests_and_immutable_snapshot(product_repository):
    repository, database = product_repository
    release = qualified_release(repository)
    with database.connection() as connection:
        row = connection.execute(
            "SELECT product_revision,service_revisions_json,binding_digest,ui_digest,suite_digest "
            "FROM tool_product_releases WHERE release_id=%s",
            (release.release_id,),
        ).fetchone()
        capability = connection.execute(
            "SELECT capability_id,capability_version,service_id,service_revision,binding_digest "
            "FROM tool_product_release_capabilities WHERE release_id=%s",
            (release.release_id,),
        ).fetchone()
        with pytest.raises(psycopg.errors.RaiseException, match="IMMUTABLE_RELEASE"):
            connection.execute(
                "UPDATE tool_product_releases SET snapshot_json='{}'::jsonb WHERE release_id=%s",
                (release.release_id,),
            )
    assert row[0] == 1 and row[1] == {"coral": 4}
    assert all(len(value) == 64 for value in row[2:])
    assert capability[:4] == ("coral.generate.one-shot", "1.0.0", "coral", 4)
    assert capability[4] == row[2]


def test_visibility_suspension_and_rollback_use_active_release_pointer(product_repository):
    repository, _ = product_repository
    first = qualified_release(repository)

    restricted = draft_payload(revision=2, audience="restricted", allowed=["bob"])
    restricted["title"] = text("Private CORAL")
    saved = repository.save_draft(
        "alice", "product-coral", ToolProductDraft.model_validate(restricted), 1
    )
    repository.record_qualification(report_for(saved, report_id="report-2"))
    second = repository.publish("product-coral", 2, "report-2", "publisher")

    assert repository.resolve_release("coral").release_id == second.release_id
    assert repository.list_visible("alice") == []
    assert [item.release_id for item in repository.list_visible("bob")] == [second.release_id]

    rolled_back = repository.rollback(first.release_id, "publisher")
    assert rolled_back.release_id == first.release_id
    assert [item.release_id for item in repository.list_visible("alice")] == [first.release_id]

    repository.suspend(first.release_id, "publisher")
    assert repository.resolve_release("coral") is None
    assert repository.list_visible("bob") == []


@pytest.mark.parametrize("stale", ["service_revision", "binding_digest", "ui_digest", "suite_digest"])
def test_publish_rejects_stale_qualification_evidence(product_repository, stale):
    repository, _ = product_repository
    saved = repository.save_draft(
        "alice", "product-coral", ToolProductDraft.model_validate(draft_payload()), 0
    )
    changes = {
        "service_revision": saved.bindings[0].service_revision + 1,
        "binding_digest": "1" * 64,
        "ui_digest": "2" * 64,
        "suite_digest": "3" * 64,
    }
    report = report_for(saved, **{stale: changes[stale]})
    repository.record_qualification(report)

    with pytest.raises(QualificationStale, match="QUALIFICATION_STALE"):
        repository.publish("product-coral", 1, report.report_id, "publisher")


def test_published_snapshot_cannot_be_changed_through_mutated_input(product_repository):
    repository, _ = product_repository
    payload = draft_payload()
    release = qualified_release(repository, deepcopy(payload))
    payload["title"] = text("Mutated after publication")

    resolved = repository.resolve_release("coral")
    assert release.title.en == "CORAL"
    assert resolved.title.en == "CORAL"
