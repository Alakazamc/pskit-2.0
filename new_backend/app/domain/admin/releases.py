"""Immutable service drafts, real discovery, and atomic computation releases."""

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from starlette.concurrency import run_in_threadpool

from app.contracts.admin import AdminService, ConfigRelease, ServiceCheck
from app.contracts.compute import ComputeServiceManifest
from app.domain.admin.roles import RevisionConflict
from app.domain.compute.common import payload_hash
from app.ports.providers import ProviderUnavailable


class ConfigReleaseService:
    def __init__(self, store, *, endpoints=None, credentials=None, transport=None):
        self.store = store
        self.endpoints = endpoints or {}
        self.credentials = credentials or {}
        self.transport = transport

    def get(self, service_id):
        row = self.store.db.execute(
            "SELECT revision,state,data_json,published_revision,schema_digest FROM admin_services WHERE service_id=?",
            (service_id,),
        ).fetchone()
        if not row:
            return None
        data = json.loads(row[2])
        for key in ("expected_revision", "reason"):
            data.pop(key, None)
        return AdminService(
            service_id=service_id,
            revision=row[0],
            state=row[1],
            published_revision=row[3],
            schema_digest=row[4],
            **data,
        )

    def scoped(self, actor, service_id, *, owner_user_id=None):
        """A maintainer cannot transfer ownership or escape its persisted scopes."""
        if "platform_admin" in actor.roles:
            return
        if service_id not in actor.service_ids:
            raise PermissionError("ADMIN_FORBIDDEN")
        current = self.get(service_id)
        owner = current.owner_user_id if current else owner_user_id
        if owner != actor.user_id or owner_user_id and owner_user_id != actor.user_id:
            raise PermissionError("ADMIN_FORBIDDEN")

    def list(self, actor, *, limit=50, cursor=None):
        ids = [
            r[0]
            for r in self.store.db.execute(
                "SELECT service_id FROM admin_services WHERE service_id>? ORDER BY service_id",
                (cursor or "",),
            )
        ]
        if "platform_admin" not in actor.roles and "auditor" not in actor.roles:
            ids = [i for i in ids if i in actor.service_ids]
        selected = ids[: limit + 1]
        return {
            "items": [self.get(i) for i in selected[:limit]],
            "next_cursor": selected[limit - 1] if len(selected) > limit else None,
        }

    def endpoint(self, payload):
        endpoint = self.endpoints.get(payload.endpoint_ref)
        if isinstance(endpoint, str):
            endpoint = {"url": endpoint}
        if not isinstance(endpoint, dict):
            raise ValueError("ENDPOINT_REF_NOT_APPROVED")  # noqa: TRY004
        url = endpoint.get("url", "")
        parsed = urlparse(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise ValueError("ENDPOINT_REF_NOT_APPROVED")
        if endpoint.get("transport") and endpoint["transport"] != payload.transport:
            raise ValueError("TRANSPORT_REF_MISMATCH")
        if payload.credential_ref and payload.credential_ref not in self.credentials:
            raise ValueError("CREDENTIAL_REF_NOT_APPROVED")
        return endpoint

    def save(self, actor, service_id, payload, *, request_id=None):
        self.endpoint(payload)
        if len({(cap.id, cap.version) for cap in payload.capabilities}) != len(
            payload.capabilities
        ):
            raise ValueError("DUPLICATE_CAPABILITY_VERSION")
        for capability in payload.capabilities:
            if capability.visibility != "draft":
                raise ValueError("CAPABILITY_MUST_BE_DRAFT")
            try:
                Draft202012Validator.check_schema(capability.input_schema)
                Draft202012Validator.check_schema(capability.output_schema)
            except SchemaError as exc:
                raise ValueError("INVALID_CAPABILITY_SCHEMA") from exc
            from app.adapters.live.remote_mcp import _has_external_ref

            if _has_external_ref(capability.input_schema) or _has_external_ref(
                capability.output_schema
            ):
                raise ValueError("EXTERNAL_SCHEMA_REF_FORBIDDEN")
        with self.store.transaction():
            self.scoped(actor, service_id, owner_user_id=payload.owner_user_id)
            before = self.get(service_id)
            revision = before.revision if before else 0
            if revision != payload.expected_revision:
                raise RevisionConflict("REVISION_CONFLICT")
            data = payload.model_dump_json()
            self.store.db.execute(
                "INSERT INTO admin_services VALUES (?,?,'draft',?,NULL,NULL) ON CONFLICT(service_id) DO UPDATE SET revision=excluded.revision,state='draft',data_json=excluded.data_json,schema_digest=NULL",
                (service_id, revision + 1, data),
            )
            self.store.db.execute(
                "INSERT INTO admin_service_versions VALUES (?,?,?)",
                (service_id, revision + 1, data),
            )
            after = self.get(service_id)
            self.store.audit(
                actor.user_id,
                "services:draft",
                service_id,
                payload.reason,
                before.model_dump(mode="json") if before else {},
                after.model_dump(mode="json"),
                request_id,
            )
            return after

    async def fetch(self, service, kind):
        """Use an approved address, hard timeout, bounded response and no redirects."""
        endpoint = self.endpoint(service)
        secret = ""
        if service.credential_ref:
            env_name = self.credentials[service.credential_ref]
            if not isinstance(env_name, str) or not env_name:
                raise ValueError("CREDENTIAL_REF_NOT_APPROVED")
            secret = os.environ.get(env_name, "")
            if not secret:
                raise ValueError("SERVICE_CREDENTIAL_UNAVAILABLE")
        headers = {"Authorization": f"Bearer {secret}"} if secret else {}
        if service.transport == "mcp":
            from app.adapters.live.remote_mcp import RemoteMcp

            allowed = set(endpoint.get("allowed_tools", []))
            if not allowed:
                raise ValueError("MCP_TOOL_ALLOWLIST_REQUIRED")
            async with httpx.AsyncClient(
                transport=self.transport,
                headers=headers,
                timeout=5,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                remote = RemoteMcp(
                    endpoint["url"], allowed_tools=allowed, timeout_seconds=5, http_client=client
                )
                schemas = await remote.discover_schemas()
            return {"capabilities": schemas}
        path = (
            endpoint.get("schema_path", "/openapi.json")
            if kind == "schema"
            else endpoint.get("health_path", "/health")
        )
        if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
            raise ValueError("INVALID_PROBE_PATH")
        async with (
            asyncio.timeout(5),
            httpx.AsyncClient(
                transport=self.transport,
                headers=headers,
                timeout=5,
                follow_redirects=False,
                trust_env=False,
            ) as client,
            client.stream("GET", endpoint["url"].rstrip("/") + path) as response,
        ):
            response.raise_for_status()
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > 1048576:
                    raise ValueError("SERVICE_SCHEMA_TOO_LARGE")
        return json.loads(data) if kind == "schema" else {}

    @staticmethod
    def discovered(data):
        """Extract only actual schemas returned by the server's discovery endpoint."""
        if isinstance(data, dict) and isinstance(data.get("capabilities"), list):
            return data["capabilities"]
        candidates = []
        if isinstance(data, dict) and isinstance(data.get("paths"), dict):
            for path, operations in data["paths"].items():
                for method, operation in operations.items():
                    if not isinstance(operation, dict) or method not in {
                        "get",
                        "post",
                        "put",
                        "patch",
                        "delete",
                    }:
                        continue
                    schema = (
                        operation.get("requestBody", {})
                        .get("content", {})
                        .get("application/json", {})
                        .get("schema")
                    )
                    candidate = {"id": operation.get("operationId", path)}
                    if schema is not None:
                        candidate["input_schema"] = schema
                    outputs = [
                        response.get("content", {}).get("application/json", {}).get("schema")
                        for code, response in operation.get("responses", {}).items()
                        if str(code).startswith("2") and isinstance(response, dict)
                    ]
                    if (
                        outputs
                        and outputs[0] is not None
                        and all(output == outputs[0] for output in outputs)
                    ):
                        candidate["output_schema"] = outputs[0]
                    candidates.append(candidate)
        return candidates

    async def check(self, actor, service_id, payload, *, discover=False, request_id=None):
        def snapshot():
            self.scoped(actor, service_id)
            return self.get(service_id)

        service = await run_in_threadpool(snapshot)
        if not service:
            raise LookupError("SERVICE_NOT_FOUND")
        if service.revision != payload.expected_revision:
            raise RevisionConflict("REVISION_CONFLICT")
        kind = "schema" if discover else payload.kind
        status = "passed"
        message = "Connectivity confirmed"
        digest = None
        candidates = []
        matched = False
        try:
            data = await self.fetch(service, kind)
            if kind == "schema":
                candidates = self.discovered(data)
                if not candidates:
                    raise ValueError("SERVICE_SCHEMA_EMPTY")
                digest = payload_hash(candidates)
                by_id = {str(c.get("id")): c for c in candidates}
                matched = all(
                    (found := by_id.get(c.id) or by_id.get(c.id.split(".")[-1])) is not None
                    and found.get("input_schema") == c.input_schema
                    and found.get("output_schema") == c.output_schema
                    for c in service.capabilities
                )
                message = (
                    "Schema discovered"
                    if discover
                    else "Schema matches draft"
                    if matched
                    else "Discovered schema differs from draft"
                )
                if not discover and not matched:
                    status = "failed"
        except (httpx.HTTPError, TimeoutError, ValueError, ProviderUnavailable):
            status = "failed"
            message = "Service check failed; verify approved reference, connectivity and schema"
        result = ServiceCheck(
            service_id=service_id,
            revision=service.revision,
            kind=kind,
            status=status,
            checked_at=datetime.now(UTC).isoformat(),
            message=message,
            schema_digest=digest,
            discovered_capabilities=candidates,
        )
        await run_in_threadpool(
            self.record_check,
            actor,
            service_id,
            payload,
            result,
            kind,
            matched,
            status,
            discover,
            digest,
            request_id,
        )
        return result

    def record_check(
        self,
        actor,
        service_id,
        payload,
        result,
        kind,
        matched,
        status,
        discover,
        digest,
        request_id,
    ):
        with self.store.transaction():
            current = self.get(service_id)
            if current.revision != payload.expected_revision:
                raise RevisionConflict("REVISION_CONFLICT")
            self.store.db.execute(
                "INSERT INTO admin_service_checks VALUES (?,?,?,?)",
                (uuid.uuid4().hex, service_id, current.revision, result.model_dump_json()),
            )
            if kind == "schema" and not discover:
                validated = matched and status == "passed"
                self.store.db.execute(
                    "UPDATE admin_services SET state=?,schema_digest=? WHERE service_id=?",
                    (
                        "validated" if validated else "draft",
                        digest if validated else None,
                        service_id,
                    ),
                )
            self.store.audit(
                actor.user_id,
                "services:discovery" if discover else "services:check",
                service_id,
                payload.reason,
                {},
                result.model_dump(mode="json"),
                request_id,
            )
        return result

    def create(self, actor, payload, *, request_id=None):
        if payload.expected_revision != 0:
            raise RevisionConflict("REVISION_CONFLICT")
        with self.store.transaction():
            for version in payload.services:
                service = self.get(version.service_id)
                if not service or service.revision != version.revision:
                    raise RevisionConflict("REVISION_CONFLICT")
                if service.state != "validated":
                    raise ValueError("SERVICE_VALIDATION_REQUIRED")
            release = ConfigRelease(
                release_id=uuid.uuid4().hex,
                revision=1,
                state="draft",
                services=payload.services,
                impact=[
                    f"{v.service_id}@{v.revision}: new jobs use released capability versions; existing jobs retain snapshots"
                    for v in payload.services
                ],
            )
            self.store.db.execute(
                "INSERT INTO admin_config_releases VALUES (?,?,?,?)",
                (release.release_id, release.revision, release.state, release.model_dump_json()),
            )
            self.store.audit(
                actor.user_id,
                "services:release-draft",
                release.release_id,
                payload.reason,
                {},
                release.model_dump(mode="json"),
                request_id,
            )
            return release

    def publish(self, actor, release_id, payload, *, request_id=None):
        if self.store.database is None:
            raise RuntimeError("COMPUTE_REGISTRY_REQUIRES_POSTGRES")
        from app.domain.compute.catalog import ComputeCatalog

        with self.store.transaction():
            row = self.store.db.execute(
                "SELECT revision,state,data_json FROM admin_config_releases WHERE release_id=?",
                (release_id,),
            ).fetchone()
            if not row:
                raise LookupError("RELEASE_NOT_FOUND")
            if row[0] != payload.expected_revision:
                raise RevisionConflict("REVISION_CONFLICT")
            if row[1] == "published":
                raise ValueError("RELEASE_ALREADY_PUBLISHED")
            before = ConfigRelease.model_validate_json(row[2])
            for version in before.services:
                service = self.get(version.service_id)
                if not service or service.revision != version.revision:
                    raise RevisionConflict("REVISION_CONFLICT")
                if service.state != "validated" or not service.schema_digest:
                    raise ValueError("SERVICE_VALIDATION_REQUIRED")
                manifest = ComputeServiceManifest(
                    service_id=service.service_id,
                    model_version=service.model_version,
                    capabilities=[
                        c.model_copy(update={"visibility": "published"})
                        for c in service.capabilities
                    ],
                )
                # The same transaction connection writes the B capability registry.
                ComputeCatalog(self.store.database).register(manifest, connection=self.store.db)
                self.store.db.execute(
                    "UPDATE admin_services SET state='published',published_revision=revision WHERE service_id=?",
                    (service.service_id,),
                )
            after = before.model_copy(update={"state": "published", "revision": row[0] + 1})
            self.store.db.execute(
                "UPDATE admin_config_releases SET revision=?,state=?,data_json=? WHERE release_id=?",
                (after.revision, after.state, after.model_dump_json(), release_id),
            )
            self.store.db.execute(
                "INSERT INTO admin_config_outbox(release_id,payload_json,created_at) VALUES (?,?,?)",
                (release_id, after.model_dump_json(), datetime.now(UTC).isoformat()),
            )
            self.store.audit(
                actor.user_id,
                "services:publish",
                release_id,
                payload.reason,
                before.model_dump(mode="json"),
                after.model_dump(mode="json"),
                request_id,
            )
            return after
