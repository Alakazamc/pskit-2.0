"""PostgreSQL repository for mutable drafts and immutable Tool Product releases."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from jsonschema import Draft202012Validator
from psycopg.types.json import Jsonb

from app.contracts.tool_products import (
    AcceptanceSuite,
    PublishedToolProduct,
    QualificationReport,
    ToolProductDraft,
)
from app.domain.compute.common import payload_hash
from app.domain.tool_products.ui_schema import validate_tool_ui


class ToolProductRevisionConflict(ValueError):
    """The caller edited a stale draft revision."""


class QualificationStale(ValueError):
    """Qualification evidence does not describe the exact publish candidate."""


class ToolProductRepository:
    def __init__(self, database):
        self.database = database

    @staticmethod
    def _digests(draft: ToolProductDraft) -> tuple[str, str, str, dict[str, int]]:
        ui_digest = validate_tool_ui(draft.ui_schema, draft.bindings)
        binding_payload = [
            item.model_dump(mode="json", by_alias=True) for item in draft.bindings
        ]
        binding_digest = payload_hash(binding_payload)
        suite_payload = {
            "suite_id": draft.acceptance_suite_id,
            "revision": draft.acceptance_suite_revision,
        }
        suite_digest = payload_hash(suite_payload)
        service_revisions: dict[str, int] = {}
        for item in draft.bindings:
            previous = service_revisions.setdefault(item.service_id, item.service_revision)
            if previous != item.service_revision:
                raise ValueError("SERVICE_REVISION_CONFLICT")
            Draft202012Validator.check_schema(item.remote_output_schema)
            Draft202012Validator.check_schema(item.result_schema)
        for action in draft.actions:
            Draft202012Validator.check_schema(action.input_schema)
        return ui_digest, binding_digest, suite_digest, service_revisions

    def save_draft(
        self,
        actor_id: str,
        product_id: str,
        draft: ToolProductDraft,
        expected_revision: int,
    ) -> ToolProductDraft:
        if draft.product_id != product_id:
            raise ValueError("PRODUCT_ID_MISMATCH")
        ui_digest, binding_digest, suite_digest, service_revisions = self._digests(draft)
        snapshot = draft.model_dump(mode="json", by_alias=True)
        with self.database.transaction() as connection:
            suite_row = connection.execute(
                "SELECT suite_digest FROM acceptance_suites "
                "WHERE suite_id=%s AND revision=%s",
                (draft.acceptance_suite_id, draft.acceptance_suite_revision),
            ).fetchone()
            if suite_row is not None:
                suite_digest = suite_row[0]
            current = connection.execute(
                "SELECT revision,slug,owner_user_id FROM tool_products "
                "WHERE product_id=%s FOR UPDATE",
                (product_id,),
            ).fetchone()
            revision = current[0] if current else 0
            if revision != expected_revision:
                raise ToolProductRevisionConflict("REVISION_CONFLICT")
            if draft.revision != expected_revision + 1:
                raise ToolProductRevisionConflict("REVISION_CONFLICT")
            if current and (current[1] != draft.slug or current[2] != draft.owner_user_id):
                raise ValueError("PRODUCT_IDENTITY_IMMUTABLE")

            connection.execute(
                "INSERT INTO tool_products "
                "(product_id,slug,owner_user_id,revision,draft_json,updated_by) "
                "VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(product_id) DO UPDATE SET "
                "revision=excluded.revision,draft_json=excluded.draft_json," 
                "updated_by=excluded.updated_by,updated_at=now()",
                (
                    product_id,
                    draft.slug,
                    draft.owner_user_id,
                    draft.revision,
                    Jsonb(snapshot),
                    actor_id,
                ),
            )
            connection.execute(
                "INSERT INTO tool_product_revisions "
                "(product_id,revision,snapshot_json,service_revisions_json,binding_digest," 
                "ui_digest,suite_digest,created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    product_id,
                    draft.revision,
                    Jsonb(snapshot),
                    Jsonb(service_revisions),
                    binding_digest,
                    ui_digest,
                    suite_digest,
                    actor_id,
                ),
            )
            for item in draft.bindings:
                connection.execute(
                    "INSERT INTO capability_bindings "
                    "(product_id,product_revision,binding_id,product_action_id,service_id," 
                    "service_revision,capability_id,capability_version,binding_json,binding_digest) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        product_id,
                        draft.revision,
                        item.binding_id,
                        item.product_action_id,
                        item.service_id,
                        item.service_revision,
                        item.capability_id,
                        item.capability_version,
                        Jsonb(item.model_dump(mode="json", by_alias=True)),
                        binding_digest,
                    ),
                )
            suite = {
                "suite_id": draft.acceptance_suite_id,
                "revision": draft.acceptance_suite_revision,
            }
            connection.execute(
                "INSERT INTO acceptance_suites "
                "(suite_id,revision,suite_json,suite_digest) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT(suite_id,revision) DO NOTHING",
                (
                    draft.acceptance_suite_id,
                    draft.acceptance_suite_revision,
                    Jsonb(suite),
                    suite_digest,
                ),
            )
        return draft

    def save_acceptance_suite(self, suite: AcceptanceSuite) -> AcceptanceSuite:
        """Persist one immutable, content-addressed scientific acceptance suite."""
        suite_json = suite.model_dump(mode="json", by_alias=True)
        suite_digest = payload_hash(suite_json)
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT suite_json,suite_digest FROM acceptance_suites "
                "WHERE suite_id=%s AND revision=%s",
                (suite.suite_id, suite.revision),
            ).fetchone()
            if existing is not None:
                placeholder = existing[0] == {
                    "suite_id": suite.suite_id,
                    "revision": suite.revision,
                }
                if placeholder:
                    connection.execute(
                        "UPDATE acceptance_suites SET suite_json=%s,suite_digest=%s "
                        "WHERE suite_id=%s AND revision=%s",
                        (Jsonb(suite_json), suite_digest, suite.suite_id, suite.revision),
                    )
                elif existing[0] != suite_json or existing[1] != suite_digest:
                    raise ValueError("IMMUTABLE_ACCEPTANCE_SUITE")
                else:
                    return suite
            else:
                connection.execute(
                    "INSERT INTO acceptance_suites "
                    "(suite_id,revision,suite_json,suite_digest) VALUES (%s,%s,%s,%s)",
                    (suite.suite_id, suite.revision, Jsonb(suite_json), suite_digest),
                )
            for case in suite.cases:
                case_json = case.model_dump(mode="json", by_alias=True)
                connection.execute(
                    "INSERT INTO acceptance_cases "
                    "(suite_id,suite_revision,case_id,case_json,case_digest) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (
                        suite.suite_id,
                        suite.revision,
                        case.case_id,
                        Jsonb(case_json),
                        payload_hash(case_json),
                    ),
                )
        return suite

    def get_acceptance_suite(
        self, suite_id: str, revision: int
    ) -> AcceptanceSuite | None:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT suite_json FROM acceptance_suites "
                "WHERE suite_id=%s AND revision=%s",
                (suite_id, revision),
            ).fetchone()
        if row is None or "cases" not in row[0]:
            return None
        return AcceptanceSuite.model_validate(row[0])

    def get_draft_revision(self, product_id: str, revision: int) -> ToolProductDraft:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT snapshot_json FROM tool_product_revisions "
                "WHERE product_id=%s AND revision=%s",
                (product_id, revision),
            ).fetchone()
        if row is None:
            raise LookupError("TOOL_PRODUCT_REVISION_NOT_FOUND")
        return ToolProductDraft.model_validate(row[0])

    def record_qualification(self, report: QualificationReport) -> QualificationReport:
        with self.database.transaction() as connection:
            revision = connection.execute(
                "SELECT snapshot_json,service_revisions_json FROM tool_product_revisions "
                "WHERE product_id=%s AND revision=%s",
                (report.product_id, report.product_revision),
            ).fetchone()
            if revision is None:
                raise LookupError("TOOL_PRODUCT_REVISION_NOT_FOUND")
            draft = ToolProductDraft.model_validate(revision[0])
            report_json = report.model_dump(mode="json", by_alias=True)
            existing = connection.execute(
                "SELECT report_json FROM qualification_reports WHERE report_id=%s",
                (report.report_id,),
            ).fetchone()
            if existing is not None:
                if existing[0] != report_json:
                    raise ValueError("IMMUTABLE_QUALIFICATION_REPORT")
                return report
            connection.execute(
                "INSERT INTO qualification_reports "
                "(report_id,product_id,product_revision,suite_id,suite_revision,report_json," 
                "service_revisions_json,binding_digest,ui_digest,suite_digest,status,qualified_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    report.report_id,
                    report.product_id,
                    report.product_revision,
                    draft.acceptance_suite_id,
                    draft.acceptance_suite_revision,
                    Jsonb(report_json),
                    Jsonb(revision[1]),
                    report.binding_digest,
                    report.ui_digest,
                    report.suite_digest,
                    report.status,
                    report.qualified_at,
                ),
            )
            for case in report.cases:
                connection.execute(
                    "INSERT INTO qualification_case_results "
                    "(report_id,case_id,result_json,status) VALUES (%s,%s,%s,%s)",
                    (
                        report.report_id,
                        case.case_id,
                        Jsonb(case.model_dump(mode="json", by_alias=True)),
                        case.status,
                    ),
                )
        return report

    def publish(
        self,
        product_id: str,
        revision: int,
        report_id: str,
        actor_id: str,
        reason: str = "Approved exact qualification evidence",
    ) -> PublishedToolProduct:
        with self.database.transaction() as connection:
            product = connection.execute(
                "SELECT revision FROM tool_products WHERE product_id=%s FOR UPDATE",
                (product_id,),
            ).fetchone()
            candidate = connection.execute(
                "SELECT snapshot_json,service_revisions_json,binding_digest,ui_digest,suite_digest "
                "FROM tool_product_revisions WHERE product_id=%s AND revision=%s",
                (product_id, revision),
            ).fetchone()
            evidence = connection.execute(
                "SELECT report_json,service_revisions_json,binding_digest,ui_digest,suite_digest "
                "FROM qualification_reports WHERE report_id=%s",
                (report_id,),
            ).fetchone()
            if product is None or candidate is None or evidence is None:
                raise QualificationStale("QUALIFICATION_STALE")
            report = QualificationReport.model_validate(evidence[0])
            service_revisions = candidate[1]
            service_values = set(service_revisions.values())
            exact = (
                product[0] == revision
                and report.product_id == product_id
                and report.product_revision == revision
                and report.status == "passed"
                and report.protocol_passed
                and report.scientific_passed
                and candidate[2:] == evidence[2:]
                and (len(service_values) != 1 or report.service_revision in service_values)
            )
            if not exact:
                raise QualificationStale("QUALIFICATION_STALE")

            release_id = f"release-{uuid.uuid4()}"
            release_snapshot = {
                "draft": candidate[0],
                "service_revisions": service_revisions,
                "binding_digest": candidate[2],
                "ui_digest": candidate[3],
                "suite_digest": candidate[4],
                "qualification_report_id": report_id,
            }
            published_at = datetime.now(UTC)
            connection.execute(
                "INSERT INTO tool_product_releases "
                "(release_id,product_id,product_revision,qualification_report_id,state," 
                "snapshot_json,service_revisions_json,binding_digest,ui_digest,suite_digest," 
                "published_by,published_at) VALUES (%s,%s,%s,%s,'published',%s,%s,%s,%s,%s,%s,%s)",
                (
                    release_id,
                    product_id,
                    revision,
                    report_id,
                    Jsonb(release_snapshot),
                    Jsonb(service_revisions),
                    candidate[2],
                    candidate[3],
                    candidate[4],
                    actor_id,
                    published_at,
                ),
            )
            connection.execute(
                "INSERT INTO review_decisions "
                "(decision_id,release_id,report_id,actor_user_id,decision,reason) "
                "VALUES (%s,%s,%s,%s,'approved',%s)",
                (f"decision-{uuid.uuid4()}", release_id, report_id, actor_id, reason),
            )
            rows = connection.execute(
                "SELECT binding_id,product_action_id,capability_id,capability_version," 
                "service_id,service_revision,binding_json,binding_digest "
                "FROM capability_bindings WHERE product_id=%s AND product_revision=%s "
                "ORDER BY binding_id",
                (product_id, revision),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "INSERT INTO tool_product_release_capabilities VALUES "
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (release_id, *row[:6], Jsonb(row[6]), row[7]),
                )
            connection.execute(
                "UPDATE tool_products SET active_release_id=%s,updated_by=%s,updated_at=now() "
                "WHERE product_id=%s",
                (release_id, actor_id, product_id),
            )
        return self._published(release_id)

    def _published(self, release_id: str, *, connection=None) -> PublishedToolProduct | None:
        if connection is None:
            with self.database.connection() as conn:
                return self._published(release_id, connection=conn)
        row = connection.execute(
            "SELECT release_id,product_id,product_revision,state,snapshot_json,published_at "
            "FROM tool_product_releases WHERE release_id=%s",
            (release_id,),
        ).fetchone()
        if row is None:
            return None
        draft = ToolProductDraft.model_validate(row[4]["draft"])
        return PublishedToolProduct(
            release_id=row[0],
            product_id=row[1],
            slug=draft.slug,
            revision=row[2],
            title=draft.title,
            description=draft.description,
            ui_schema=draft.ui_schema,
            actions=draft.actions,
            state=row[3],
            published_at=row[5],
        )

    def resolve_release(self, slug: str) -> PublishedToolProduct | None:
        with self.database.connection() as connection:
            row = connection.execute(
                "SELECT active_release_id FROM tool_products WHERE slug=%s",
                (slug,),
            ).fetchone()
            if row is None or row[0] is None:
                return None
            release = self._published(row[0], connection=connection)
            return release if release and release.state == "published" else None

    def release_snapshot(self, release_id: str, *, connection=None) -> dict | None:
        """Return the private immutable release snapshot for server-side admission only."""
        if connection is None:
            with self.database.connection() as conn:
                return self.release_snapshot(release_id, connection=conn)
        row = connection.execute(
            "SELECT state,snapshot_json FROM tool_product_releases WHERE release_id=%s",
            (release_id,),
        ).fetchone()
        if row is None:
            return None
        return {"state": row[0], **row[1]}

    def list_visible(self, user_id: str | None) -> list[PublishedToolProduct]:
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT r.release_id,r.product_id,r.product_revision,r.state,r.snapshot_json," 
                "r.published_at FROM tool_products p JOIN tool_product_releases r "
                "ON r.release_id=p.active_release_id "
                "WHERE r.state='published' ORDER BY p.slug"
            ).fetchall()
        visible: list[PublishedToolProduct] = []
        for row in rows:
            draft = ToolProductDraft.model_validate(row[4]["draft"])
            visibility = draft.visibility
            can_view = (
                visibility.audience == "public"
                or (visibility.audience == "members" and bool(user_id))
                or (
                    visibility.audience == "restricted"
                    and user_id in visibility.allowed_user_ids
                )
            )
            if can_view:
                visible.append(
                    PublishedToolProduct(
                        release_id=row[0],
                        product_id=row[1],
                        slug=draft.slug,
                        revision=row[2],
                        title=draft.title,
                        description=draft.description,
                        ui_schema=draft.ui_schema,
                        actions=draft.actions,
                        state=row[3],
                        published_at=row[5],
                    )
                )
        return visible

    def suspend(self, release_id: str, actor_id: str) -> PublishedToolProduct:
        with self.database.transaction() as connection:
            changed = connection.execute(
                "UPDATE tool_product_releases SET state='suspended',suspended_by=%s," 
                "suspended_at=now() WHERE release_id=%s",
                (actor_id, release_id),
            ).rowcount
            if not changed:
                raise LookupError("TOOL_PRODUCT_RELEASE_NOT_FOUND")
            connection.execute(
                "UPDATE tool_products SET active_release_id=NULL,updated_by=%s,updated_at=now() "
                "WHERE active_release_id=%s",
                (actor_id, release_id),
            )
        return self._published(release_id)

    def rollback(self, release_id: str, actor_id: str) -> PublishedToolProduct:
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT product_id FROM tool_product_releases WHERE release_id=%s FOR UPDATE",
                (release_id,),
            ).fetchone()
            if row is None:
                raise LookupError("TOOL_PRODUCT_RELEASE_NOT_FOUND")
            connection.execute(
                "UPDATE tool_product_releases SET state='published',suspended_by=NULL," 
                "suspended_at=NULL WHERE release_id=%s",
                (release_id,),
            )
            connection.execute(
                "UPDATE tool_products SET active_release_id=%s,updated_by=%s,updated_at=now() "
                "WHERE product_id=%s",
                (release_id, actor_id, row[0]),
            )
        return self._published(release_id)
