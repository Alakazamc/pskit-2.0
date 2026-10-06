# Config-Driven MCP Tool Products Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reviewed, versioned MCP onboarding path where one configuration publishes a multi-Capability scientific product at `/tools/{slug}` without new React routes or Nginx upstreams.

**Architecture:** Keep the existing PostgreSQL Compute Job, lease, quota and receipt system as the execution source of truth. Add an immutable Tool Product release above it, a generic MCP execution binding below it, and one React Tool UI interpreter in front of it; CORAL becomes the first real product using the same path. Nginx continues serving the SPA and `/api/v1`, while the Python registry resolves product, action, Capability and endpoint versions.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, PostgreSQL 17, psycopg, MCP 1.26.x, existing `pskit_compute`, React 19, TypeScript 6, TanStack Query, Vitest, pytest.

**Spec:** [配置驱动的 MCP Tool Product 设计](../specs/2026-10-06-config-driven-mcp-tool-ui-design.md)

## Global Constraints

- Do not execute provider-supplied JavaScript, CSS, HTML, expressions or network requests in UI configuration.
- Browsers never receive MCP endpoint credentials and never call model services directly.
- `/tools/*` remains one React SPA route and `/api/v1/*` remains one Python upstream; publishing a product does not change or reload Nginx.
- One immutable Tool Product Release pins exact service revisions, Capability versions, execution bindings, UI schema digest, acceptance evidence, visibility and Agent handoff definitions.
- Keep remote MCP output schema, PSKit `ExecutionReport`, and `Completed.result` schema separate.
- Reuse `ComputeJobs`, `ComputeLeases`, quota reservations, receipts and outbox; do not create a second scientific task queue.
- Usage units remain integer `wall_ms`, `cpu_core_ms`, `gpu_device_ms`, bytes and `gpu_count`; unknown is `null`, known zero is `0`, and the source remains explicit.
- User-facing progress is derived only from persisted worker events; do not fabricate scientific stages or percentages.
- UI data paths use JSON Pointer and allowlisted transforms/predicates only.
- Configuration changes invalidate qualification evidence; a running Job retains its submission snapshot.
- Keep `mcp>=1.26,<2`; native MCP Tasks require negotiated capability support and remain a separate Adapter.
- Follow [frontend UI rules](../../../new_frontend/AGENTS.md), including bilingual copy, theme support, keyboard access and full Tools-panel detail routes.
- Use the current PostgreSQL migration sequence; this feature owns migration `009_tool_products.sql` and raises the PostgreSQL schema version from 8 to 9.
- Do not treat service-reported GPU usage as independently measured usage; both API and UI expose its source.
- Each task follows red → green → focused refactor and ends with its own commit.

## Review Focus

1. **Stale schema/config evidence:** changing a service revision, binding, UI schema or acceptance suite must invalidate qualification and block publication; Tasks 2, 4 and 7 pin this.
2. **Duplicate starts and uncertain outcomes:** repeated idempotency keys must return one Tool Run/Compute Job, while an unknown remote outcome must not be blindly retried; Tasks 5 and 7 pin this.
3. **Permission changes after page load:** revoked product visibility or quota must be checked again by the start endpoint rather than trusted from cached UI state; Tasks 3 and 5 pin this.
4. **Cancellation and missing usage:** cancelling is only requested until worker confirmation, required metrics cannot silently become zero, and reservations stay pending when outcome is unknown; Tasks 6, 7 and 9 pin this.
5. **Unsafe endpoint/config input:** redirects, loopback/link-local/cloud-metadata escape, external schema references, unknown UI components and invalid JSON Pointers must fail closed; Tasks 1, 4 and 7 pin this.

---

## File Structure

### Backend contracts and domain modules

- Create `new_backend/app/contracts/tool_products.py`: versioned Tool Product, UI schema, execution binding, qualification and public run contracts.
- Create `new_backend/app/domain/tool_products/ui_schema.py`: canonicalization, digest, JSON Pointer and allowlist validation.
- Create `new_backend/app/domain/tool_products/repository.py`: draft, immutable release, visibility and acceptance persistence.
- Create `new_backend/app/domain/tool_products/registry.py`: the small `resolve()`/`list_visible()` product interface.
- Create `new_backend/app/domain/tool_products/runs.py`: action admission and projection onto existing Compute Jobs.
- Create `new_backend/app/domain/compute/events.py`: durable ordered Compute Job events reused by product runs.
- Create `new_backend/app/domain/admin/qualification.py`: discovery snapshot and acceptance suite execution.
- Create `new_backend/app/api/tool_products.py`: owned public product/run endpoints.
- Create `new_backend/app/api/admin_tool_products.py`: draft, qualification, preview, publish, suspend and rollback endpoints.
- Create `new_backend/app/db/postgres_migrations/009_tool_products.sql`: product, binding, qualification, run and event persistence.

### Generic MCP execution

- Modify `new_backend/app/contracts/compute.py`: add private `ExecutionBindingSnapshot` to `ExecutionGrant`, not public `ComputeJob`.
- Modify `new_backend/app/domain/compute/jobs.py`: snapshot a published binding at submission.
- Modify `new_backend/app/domain/compute/leases.py`: emit persisted progress and terminal events.
- Create `new_backend/pskit_compute/dynamic_mcp.py`: construct an immediate/job MCP Adapter from a trusted grant binding and local secret resolver.
- Create `new_backend/scripts/mcp_compute_receiver.py`: generic durable receiver process; no service-specific Python imports.

### React application

- Create `new_frontend/src/features/tool-products/ToolProductPage.tsx`: published product route and run lifecycle.
- Create `new_frontend/src/features/tool-products/ToolUiRenderer.tsx`: root declarative interpreter.
- Create `new_frontend/src/features/tool-products/ToolInputRenderer.tsx`: allowlisted schema-driven fields.
- Create `new_frontend/src/features/tool-products/ToolResultRenderer.tsx`: allowlisted scientific results.
- Create `new_frontend/src/features/tool-products/ToolStageFlow.tsx`: persisted progress projection.
- Create `new_frontend/src/features/tool-products/toolUiSchema.ts`: trusted generated-contract narrowing and JSON Pointer lookup.
- Create `new_frontend/src/features/admin/AdminToolProductsPage.tsx`: management workflow.
- Create `new_frontend/src/features/admin/ToolProductBuilder.tsx`: template/component/data binding editor and preview.
- Modify `new_frontend/src/features/mono/ToolPages.tsx`: directory uses Tool Products and dynamic slug route.
- Modify `new_frontend/src/features/admin/AdminShell.tsx`: add the authorized Tool Products section.
- Modify `new_frontend/src/api/{types.ts,http.ts,admin.ts}` and generated API files: typed product/run/admin calls.
- Modify `new_frontend/src/i18n/translations.ts` and `new_frontend/src/styles/mono.css`: bilingual states and shared workspace styling.

### CORAL provider and deployment

- Create isolated remote worktree `/data/jhli/project/CORAL-pskit-mcp` from the current CORAL HEAD; modify its `mcp_server.py` and add `pskit_protocol.py` plus contract tests without touching the dirty source checkout.
- Create isolated remote worktree `/data/jhli/project/annoy-coral-pskit-mcp` from the current annoy-coral HEAD; add a `pskit_mcp/` package over the existing iterative, pocket-distance and analysis workflows without duplicating scientific algorithms.
- Modify `deploy/agent/compose.cloud.yaml`, `compose.staging.yaml` and env examples: add the generic MCP receiver process and secret references.
- Add `deploy/agent/scripts/tool_product_smoke.py`: staging discovery, qualification, publish, run, usage and Artifact smoke.

---

### Task 1: Tool Product contracts and safe UI schema validation

**Files:**
- Create: `new_backend/app/contracts/tool_products.py`
- Create: `new_backend/app/domain/tool_products/__init__.py`
- Create: `new_backend/app/domain/tool_products/ui_schema.py`
- Test: `new_backend/tests/test_tool_product_contracts.py`

**Interfaces:**
- Produces: `ToolUiSchema`, `CapabilityBinding`, `ProductAction`, `ToolProductDraft`, `PublishedToolProduct`, `ToolProductPage`, `QualificationReport`, `ToolRunSnapshot`, `ToolRunEvent`.
- Produces: `validate_tool_ui(schema: ToolUiSchema, bindings: list[CapabilityBinding]) -> str`, returning the canonical SHA-256 digest.

- [ ] **Step 1: Write failing contract tests** asserting `extra="forbid"`, `schema_version == "pskit.tool-ui.v1"`, bilingual localized text, allowlisted layout/field/result kinds, unique IDs, valid action references and distinct remote/result schemas.
- [ ] **Step 2: Write failing safety tests** for unknown component names, external `$ref`, invalid/escaping JSON Pointer, arbitrary transform/expression/network keys, duplicate action IDs and a UI action that is absent from product bindings.
- [ ] **Step 3: Run** `cd new_backend && .venv/bin/python -m pytest tests/test_tool_product_contracts.py -q`; expect collection/import failure.
- [ ] **Step 4: Implement the contracts and `validate_tool_ui()`** with canonical JSON hashing and explicit allowlists; do not add a generic executable expression language.
- [ ] **Step 5: Re-run the focused test**, then commit `feat(tool-products): define safe product contracts`.

### Task 2: PostgreSQL v9 persistence and immutable releases

**Files:**
- Create: `new_backend/app/db/postgres_migrations/009_tool_products.sql`
- Modify: `new_backend/app/db/postgres_migrations/__init__.py`
- Create: `new_backend/app/domain/tool_products/repository.py`
- Test: `new_backend/tests/postgres/test_tool_product_repository.py`
- Modify test: `new_backend/tests/postgres/test_schema.py`

**Interfaces:**
- Consumes: contracts and digest from Task 1.
- Produces: `ToolProductRepository.save_draft(actor_id, product_id, draft, expected_revision)`, `record_qualification(report)`, `publish(product_id, revision, report_id, actor_id)`, `suspend(release_id, actor_id)`, `resolve_release(slug)`, `list_visible(user_id)`.

- [ ] **Step 1: Write the migration test** expecting schema versions `1..9`, all product/binding/acceptance/qualification/run/event tables and foreign keys, with no startup auto-migration.
- [ ] **Step 2: Write repository tests** proving optimistic revision conflicts, immutable releases, exact capability/binding/UI digests, visibility filtering, suspension and rollback by active release pointer.
- [ ] **Step 3: Add stale-evidence tests**: changing any service revision, binding digest, UI digest or suite digest after qualification makes `publish()` raise `QUALIFICATION_STALE`.
- [ ] **Step 4: Run** `cd new_backend && TEST_POSTGRES_DSN=... .venv/bin/python -m pytest tests/postgres/test_schema.py tests/postgres/test_tool_product_repository.py -q`; expect version/table failures.
- [ ] **Step 5: Implement migration v9 and repository**, rerun against isolated PostgreSQL, then commit `feat(tool-products): persist immutable product releases`.

### Task 3: Registry and public read interface

**Files:**
- Create: `new_backend/app/domain/tool_products/registry.py`
- Create: `new_backend/app/api/tool_products.py`
- Modify: `new_backend/app/main.py`
- Test: `new_backend/tests/postgres/test_tool_product_api.py`

**Interfaces:**
- Consumes: `ToolProductRepository.resolve_release()` and `list_visible()`.
- Produces: `ToolProductRegistry.resolve(slug: str, user_id: str) -> PublishedToolProduct` and `list_visible(user_id: str, cursor: str | None, limit: int) -> ToolProductPage`.
- Produces: `GET /api/v1/tool-products` and `GET /api/v1/tool-products/{slug}`.

- [ ] **Step 1: Write API tests** for published visibility, cursor bounds, suspended/unknown products, guests, explicitly allowed users and server-side permission revocation after an earlier successful read.
- [ ] **Step 2: Run** `cd new_backend && TEST_POSTGRES_DSN=... .venv/bin/python -m pytest tests/postgres/test_tool_product_api.py -q`; expect 404/import failures.
- [ ] **Step 3: Implement Registry and routes** so unpublished/unauthorized products are indistinguishable as 404 and returned contracts contain no endpoint or credential reference.
- [ ] **Step 4: Re-run the focused test**, then commit `feat(tool-products): expose published product registry`.

### Task 4: Admin product drafts, qualification evidence and atomic publication

**Files:**
- Create: `new_backend/app/domain/admin/qualification.py`
- Create: `new_backend/app/domain/admin/service_endpoints.py`
- Create: `new_backend/app/api/admin_tool_products.py`
- Modify: `new_backend/app/config.py`
- Modify: `new_backend/app/main.py`
- Test: `new_backend/tests/test_admin_tool_product_qualification.py`
- Test: `new_backend/tests/postgres/test_admin_tool_product_release.py`

**Interfaces:**
- Consumes: existing credential references, `RemoteMcp`, Task 1 contracts and Task 2 repository; successful restricted probes create versioned approved service endpoints in PostgreSQL.
- Produces: `ApprovedEndpointPolicy.resolve(uri: str, network_zone: str) -> EndpointSnapshot`; public HTTPS is allowed after DNS/IP checks, while HTTP and private addresses require an explicitly configured CIDR for that named zone.
- Produces: `McpQualification.probe(uri, transport, credential_ref) -> ProbeSnapshot`, `discover(service_revision) -> DiscoverySnapshot` and `qualify(product_revision, suite_revision) -> QualificationReport`.
- Produces: `/api/v1/admin/tool-products/*` endpoints from the spec using existing `services:read/write/publish` permissions.

- [ ] **Step 1: Write probe/discovery tests** for platform-admin-only URI entry, persisted approved service revisions, paginated `tools/list`, separate remote/result schemas, response size/time limits, no redirects, rejected external refs and failed SSRF address classes. DNS resolution must reject loopback, link-local and cloud metadata; named WireGuard/private CIDRs are allowed only when present in `RESEARCH_AGENT_ADMIN_MCP_NETWORK_ZONES_JSON`.
- [ ] **Step 2: Write qualification tests** for valid/invalid input cases, terminal result schema, required usage, Artifact metadata, idempotency, cancellation semantics and ordered progress evidence.
- [ ] **Step 3: Write publication tests** proving only a currently qualified exact revision can enter `review_pending/published`, and service maintainers cannot self-grant publish permission or publish another owner's product.
- [ ] **Step 4: Run** `cd new_backend && .venv/bin/python -m pytest tests/test_admin_tool_product_qualification.py tests/postgres/test_admin_tool_product_release.py -q`; expect missing qualification/release interfaces.
- [ ] **Step 5: Implement probe, discovery, optional `/.well-known/pskit-tool-product.json`/MCP Resource import as an untrusted draft, qualification persistence and admin routes**; protocol qualification and scientific assertions remain separate fields in the report.
- [ ] **Step 6: Re-run focused tests**, then commit `feat(admin): qualify and publish MCP tool products`.

### Task 5: Tool Run Gateway over existing Compute Jobs

**Files:**
- Create: `new_backend/app/domain/tool_products/runs.py`
- Modify: `new_backend/app/api/tool_products.py`
- Modify: `new_backend/app/domain/compute/jobs.py`
- Test: `new_backend/tests/postgres/test_tool_product_runs.py`

**Interfaces:**
- Consumes: `ToolProductRegistry.resolve()`, published `ProductAction`, `ComputeJobs.submit/get/cancel/history`.
- Produces: `ToolRunGateway.start(product, action_id, arguments, user_id, idempotency_key) -> ToolRunSnapshot`, `get`, `cancel`, `history` and `handoff`.
- Produces: public start/get/cancel/history/handoff endpoints from the spec.

- [ ] **Step 1: Write tests** proving action input validation, exact Capability version resolution, immutable release/action/Capability snapshots and one Tool Run/Compute Job for repeated identical idempotency keys. The private execution binding snapshot is added in Task 7 before a real worker is enabled.
- [ ] **Step 2: Add conflict and authorization tests** for a reused key with changed input, revoked visibility after page load, insufficient quota, cross-user Run access and action IDs not present in the release.
- [ ] **Step 3: Add multi-Capability product tests** showing different actions on one `/tools/{slug}` resolve different capabilities without endpoint information from the browser.
- [ ] **Step 4: Run** `cd new_backend && TEST_POSTGRES_DSN=... .venv/bin/python -m pytest tests/postgres/test_tool_product_runs.py -q`; expect missing gateway failures.
- [ ] **Step 5: Implement Gateway and routes** by delegating admission/accounting to `ComputeJobs`; do not duplicate quota or job state.
- [ ] **Step 6: Re-run focused tests**, then commit `feat(tool-products): start owned runs through compute jobs`.

### Task 6: Durable events, workflow steps and cancellation projection

**Files:**
- Create: `new_backend/app/domain/compute/events.py`
- Modify: `new_backend/app/domain/compute/jobs.py`
- Modify: `new_backend/app/domain/compute/leases.py`
- Modify: `new_backend/app/domain/tool_products/runs.py`
- Modify: `new_backend/app/api/tool_products.py`
- Test: `new_backend/tests/postgres/test_tool_product_events.py`
- Test: `new_backend/tests/postgres/test_tool_product_workflows.py`

**Interfaces:**
- Produces: `ComputeEvents.append(job_id, type, data, *, connection) -> ToolRunEvent` and `after(run_id, user_id, cursor, limit) -> list[ToolRunEvent]`.
- Produces: `ToolRunGateway.advance_completed_steps(run_id)` for persisted workflow definitions; it submits each next step exactly once through a deterministic idempotency key.

- [ ] **Step 1: Write event tests** for monotonic sequence, snapshot-then-cursor recovery, bounded SSE pagination/reconnect, queued/started/progress/artifact/usage/terminal events and cross-user isolation.
- [ ] **Step 2: Write workflow tests** for sequential Capability input/result mapping, restart recovery, one next-step submission after duplicate completion notifications and a failed step preventing downstream execution.
- [ ] **Step 3: Write cancellation tests** asserting `cancelling` emits requested state, keeps reservation/device ownership, and only a confirmed terminal receipt emits `run.cancelled`; unknown outcomes remain reconcilable.
- [ ] **Step 4: Run** `cd new_backend && TEST_POSTGRES_DSN=... .venv/bin/python -m pytest tests/postgres/test_tool_product_events.py tests/postgres/test_tool_product_workflows.py -q`; expect missing events/workflow behavior.
- [ ] **Step 5: Implement events and workflow advancement** within the existing lease/result transactions and outbox consumption path.
- [ ] **Step 6: Re-run focused tests**, then commit `feat(tool-products): persist progress and workflow events`.

### Task 7: Dynamic MCP execution binding and generic receiver

**Files:**
- Modify: `new_backend/app/contracts/compute.py`
- Modify: `new_backend/app/domain/compute/jobs.py`
- Modify: `new_backend/app/domain/compute/leases.py`
- Create: `new_backend/pskit_compute/dynamic_mcp.py`
- Create: `new_backend/scripts/mcp_compute_receiver.py`
- Modify: `new_backend/scripts/export_openapi.py`
- Test: `new_backend/tests/test_dynamic_mcp_receiver.py`
- Modify test: `new_backend/tests/postgres/test_compute_jobs.py`

**Interfaces:**
- Produces: private `ExecutionBindingSnapshot(adapter, endpoint_url, credential_ref, submit_tool, status_tool, cancel_tool, remote_output_schema, result_mapping)` on `ExecutionGrant` only. `endpoint_url` is resolved and snapshotted at submission; the secret value is never included.
- Produces: `DynamicMcpExecutor.from_grant(grant, endpoint_resolver, credential_resolver) -> McpAdapter`.

- [ ] **Step 1: Write snapshot tests** proving the public `ComputeJob` omits execution endpoint/credential while a worker grant contains the exact immutable binding used at submission.
- [ ] **Step 2: Write Adapter tests** for immediate envelope, bare-result wrapping through JSON Pointer, job-style pending/status/cancel, negotiated MCP Tasks, result size bounds, required usage, `isError` mismatch and remote job ID conflict.
- [ ] **Step 3: Write receiver recovery tests** proving an executing journal entry after crash becomes `unknown` and is never blindly re-executed; a recorded outbox result is resent idempotently.
- [ ] **Step 4: Run** `cd new_backend && .venv/bin/python -m pytest tests/test_dynamic_mcp_receiver.py tests/postgres/test_compute_jobs.py -q`; expect missing binding/receiver behavior.
- [ ] **Step 5: Implement private grant snapshot and generic receiver** with immediate, job-style and negotiated MCP Tasks Adapters plus local secret resolution; no user-controlled module import and no secret in logs or public objects.
- [ ] **Step 6: Regenerate `contracts/compute.schema.json` and generated compute types**, rerun focused tests, then commit `feat(compute): execute released MCP bindings generically`.

### Task 8: Config-driven React input and result interpreter

**Files:**
- Create: `new_frontend/src/features/tool-products/toolUiSchema.ts`
- Create: `new_frontend/src/features/tool-products/ToolUiRenderer.tsx`
- Create: `new_frontend/src/features/tool-products/ToolInputRenderer.tsx`
- Create: `new_frontend/src/features/tool-products/ToolResultRenderer.tsx`
- Create: `new_frontend/src/features/tool-products/ToolStageFlow.tsx`
- Create: `new_frontend/src/features/tool-products/ToolUiRenderer.test.tsx`
- Modify generated: `contracts/openapi.json`
- Modify generated: `new_frontend/src/api/generated/types.gen.ts`
- Modify: `new_frontend/src/styles/mono.css`

**Interfaces:**
- Consumes: generated `ToolUiSchema`, `ToolRunSnapshot`, `ToolRunEvent`.
- Produces: `<ToolUiRenderer schema form run events onChange onAction />` and `resolvePointer(document, pointer) -> unknown`.

- [ ] **Step 1: Regenerate the approved backend/client contract** with `cd new_backend && .venv/bin/python scripts/export_openapi.py`, then `cd ../new_frontend && npm run generate:api`; review the generated diff before adding handwritten frontend code.
- [ ] **Step 2: Write renderer tests** for text/number/select/segmented/file/protein/sequence fields, required/range/default behavior and conditional sections.
- [ ] **Step 3: Write result tests** for metric grid, stage flow, sequence/data table, chart, structure viewer, Artifact list and bounded JSON fallback using one synthetic product fixture.
- [ ] **Step 4: Add safety/accessibility tests** for invalid pointers, missing data, unsupported components, no raw HTML/network execution, bilingual labels, keyboard action and error/loading/empty states.
- [ ] **Step 5: Run** `cd new_frontend && npm test -- src/features/tool-products/ToolUiRenderer.test.tsx`; expect missing module failures.
- [ ] **Step 6: Implement the allowlisted interpreter and shared styles**; reuse existing Molstar and file controls rather than forking them.
- [ ] **Step 7: Re-run focused tests**, then commit `feat(frontend): render scientific tools from configuration`.

### Task 9: Dynamic Tool Product page, history and Agent handoff

**Files:**
- Create: `new_frontend/src/features/tool-products/ToolProductPage.tsx`
- Create: `new_frontend/src/features/tool-products/ToolProductPage.test.tsx`
- Modify: `new_frontend/src/features/mono/ToolPages.tsx`
- Modify: `new_frontend/src/api/types.ts`
- Modify: `new_frontend/src/api/http.ts`
- Modify: `new_frontend/src/i18n/translations.ts`

**Interfaces:**
- Consumes: public Task 3/5/6 endpoints and Task 8 renderer.
- Produces: `/tools/:slug` route, product directory from published products, run subscription/recovery, exact-product history and `run_id` Agent handoff.

- [ ] **Step 1: Write directory/route tests** proving a newly returned product becomes a card and full Tools-panel page without a code branch, with Back to tools and exact-product history.
- [ ] **Step 2: Write lifecycle tests** for immediate and pending runs, real stage updates, refresh recovery from cursor, stop remaining active until terminal state, usage source labels and bounded Artifact previews.
- [ ] **Step 3: Write handoff tests** asserting only immutable `run_id`, bounded summary and owned Artifact references enter the new Session context.
- [ ] **Step 4: Run** `cd new_frontend && npm test -- src/features/tool-products/ToolProductPage.test.tsx`; expect route/API failures.
- [ ] **Step 5: Implement generated API types/calls and `ToolProductPage`**, then regenerate OpenAPI TypeScript types rather than maintaining duplicate handwritten response shapes.
- [ ] **Step 6: Re-run focused tests**, then commit `feat(frontend): open published tool products dynamically`.

### Task 10: Admin discovery, UI Builder, qualification and publication

**Files:**
- Create: `new_frontend/src/features/admin/AdminToolProductsPage.tsx`
- Create: `new_frontend/src/features/admin/ToolProductBuilder.tsx`
- Create: `new_frontend/src/features/admin/AdminToolProductsPage.test.tsx`
- Modify: `new_frontend/src/features/admin/AdminShell.tsx`
- Modify: `new_frontend/src/api/admin.ts`
- Modify: `new_frontend/src/i18n/translations.ts`
- Modify: `new_frontend/src/styles/admin.css`

**Interfaces:**
- Consumes: Task 4 admin endpoints, Task 8 renderer for preview, existing `services:read/write/publish` RBAC.
- Produces: connect → discover → map → build → cases → qualify → preview → review/publish flow.

- [ ] **Step 1: Write role tests**: auditors read evidence, maintainers edit only owned drafts/run qualification, and only publishers approve/publish/suspend/rollback.
- [ ] **Step 2: Write workflow tests** for endpoint reference selection, paginated discovery import, Capability mapping, template/component selection, JSON Pointer source picker, acceptance cases and preview.
- [ ] **Step 3: Write evidence tests** showing protocol/scientific results separately, usage source prominently, changed drafts invalidating qualification, failed cases blocking publish and release diff before approval.
- [ ] **Step 4: Run** `cd new_frontend && npm test -- src/features/admin/AdminToolProductsPage.test.tsx`; expect missing admin UI/API behavior.
- [ ] **Step 5: Implement the admin page and Builder** using existing compact panels and controls; include an advanced YAML/JSON import/export view but no executable snippets.
- [ ] **Step 6: Re-run focused tests**, then commit `feat(admin-ui): configure and review MCP tool products`.

### Task 11: CORAL provider contracts and four Capability mappings

**Files:**
- Remote modify: `/data/jhli/project/CORAL-pskit-mcp/mcp_server.py`
- Remote create: `/data/jhli/project/CORAL-pskit-mcp/pskit_protocol.py`
- Remote create: `/data/jhli/project/CORAL-pskit-mcp/tests/test_pskit_mcp_contract.py`
- Remote create: `/data/jhli/project/annoy-coral-pskit-mcp/pskit_mcp/__init__.py`
- Remote create: `/data/jhli/project/annoy-coral-pskit-mcp/pskit_mcp/server.py`
- Remote create: `/data/jhli/project/annoy-coral-pskit-mcp/pskit_mcp/drivers.py`
- Remote create: `/data/jhli/project/annoy-coral-pskit-mcp/pskit_mcp/smoke.py`
- Remote create: `/data/jhli/project/annoy-coral-pskit-mcp/tests/test_pskit_mcp_contract.py`
- Create: `new_backend/fixtures/tool_products/coral.product.yaml`
- Create: `new_backend/fixtures/tool_products/coral.acceptance.yaml`

**Interfaces:**
- Produces canonical capabilities `coral.generate.one_shot`, `coral.generate.iterative`, `coral.analyze.pocket`, `coral.optimize.two_dimensional`.
- Provider drivers wrap existing CORAL `RNAExpert.generate`, iterative samplers, `run_evolutionary_sample_ipocket_dis` scoring/workflow and analysis outputs; scientific implementations stay in their current modules.

- [ ] **Step 1: Record both remote HEADs and dirty-file lists, then create dedicated `pskit-mcp-integration` branches in `/data/jhli/project/CORAL-pskit-mcp` and `/data/jhli/project/annoy-coral-pskit-mcp` with `git worktree add`**; leave both source checkouts and their uncommitted research files untouched.
- [ ] **Step 2: Write provider contract tests with fake drivers** asserting typed inputs, bounded summaries, full CSV/plot Artifact metadata, failure usage, non-negative timers and no model initialization before input validation.
- [ ] **Step 3: Run** `cd /data/jhli/project/CORAL-pskit-mcp && pytest -q tests/test_pskit_mcp_contract.py` and `cd /data/jhli/project/annoy-coral-pskit-mcp && pytest -q tests/test_pskit_mcp_contract.py`; expect missing protocol/server modules and do not load production weights.
- [ ] **Step 4: Implement `pskit_protocol.py` and MCP wrappers** with monotonic wall timing, process CPU timing, explicit GPU source, per-run workspace, cancellation checks and Streamable HTTP transport.
- [ ] **Step 5: Map the existing source-backed workflows**: one-shot to `CORAL/mcp_server.py::RNAExpert.generate`; iterative generation to `CORAL/run_evolutionary_sample_rnafea.py::LightningRunner.main`; pocket optimization to `annoy-coral/run_evolutionary_sample_ipocket_dis.py::LightningRunner.main`; two-dimensional sequence/secondary-structure analysis to `annoy-coral/src/c5_analysis` plus `scripts/secondary_structure_similarity.py`. Record provider-facing names separately from the stable Capability IDs, and keep Redis/AF3 dependencies behind the existing workflow drivers.
- [ ] **Step 6: Run** `cd /data/jhli/project/annoy-coral-pskit-mcp && python -m pskit_mcp.smoke --driver fake --all`; then run `python -m pskit_mcp.smoke --driver real --capability <canonical-id> --case <approved-case-id>` once for each of the four canonical IDs. Record wall/GPU source and Artifact SHA-256 for each real case.
- [ ] **Step 7: Import `coral.product.yaml`, run the PSKit acceptance suite and publish only if all four mappings pass; commit remote integration changes separately in each repository and local fixtures as `feat(coral): expose reviewed MCP capabilities`**.

### Task 12: Remove hard-coded CORAL path after parity

**Files:**
- Delete after parity: `new_frontend/src/features/coral/CoralWorkspace.tsx`
- Delete after parity: `new_frontend/src/features/coral/CoralProgress.tsx`
- Delete after parity: `new_frontend/src/features/coral/CoralResults.tsx`
- Delete after parity: `new_frontend/src/features/coral/CoralHistory.tsx`
- Modify: `new_frontend/src/features/mono/ToolPages.tsx`
- Modify tests: `new_frontend/src/features/coral/CoralWorkspace.test.tsx`
- Create: `new_frontend/src/features/tool-products/CoralProductParity.test.tsx`

**Interfaces:**
- Consumes: published CORAL Tool Product from Tasks 8–11.
- Produces: one dynamic rendering path with no `if coral` tool-name branch.

- [ ] **Step 1: Port existing CORAL UI assertions** to the configuration fixture: limits, true progress, cancellation, result completeness, history and Agent handoff.
- [ ] **Step 2: Add a source-level test** asserting `ToolPages.tsx` does not import CORAL-specific page code or branch on a CORAL ID.
- [ ] **Step 3: Run the parity tests** and verify they pass through the new Product page before deleting old files.
- [ ] **Step 4: Remove hard-coded components and duplicate CSS/translations**, rerun tool directory and parity suites.
- [ ] **Step 5: Commit `refactor(frontend): replace hard-coded CORAL workspace`**.

### Task 13: Compose receiver, staging smoke and release documentation

**Files:**
- Modify: `deploy/agent/compose.cloud.yaml`
- Modify: `deploy/agent/compose.staging.yaml`
- Modify: `deploy/agent/cloud.backend.env.example`
- Modify: `deploy/agent/local.env.example`
- Create: `deploy/agent/scripts/tool_product_smoke.py`
- Create: `deploy/agent/tests/test_mcp_receiver_compose.py`
- Create: `deploy/agent/tests/test_tool_product_smoke.py`
- Modify: `deploy/agent/OPERATIONS.md`

**Interfaces:**
- Consumes: generic receiver from Task 7 and reviewed CORAL release from Task 11.
- Produces: one configuration-only receiver process per approved MCP service, with persistent journal and no new Nginx location.

- [ ] **Step 1: Write deployment tests** for pinned image, private backend URL, service ID/key, endpoint/credential refs, persistent receiver journal, restart policy and no public MCP credential exposure.
- [ ] **Step 2: Write smoke tests** for admin discovery/qualification/preview/publish, ordinary-user start, pending recovery, result/Artifact/usage and product suspension.
- [ ] **Step 3: Run** `cd deploy/agent && python -m pytest tests/test_mcp_receiver_compose.py tests/test_tool_product_smoke.py -q`; expect missing receiver/smoke definitions.
- [ ] **Step 4: Add staging receiver and operational documentation**; production config remains unchanged until Staging passes and no active/unknown receiver journal entry exists.
- [ ] **Step 5: Run backend focused/full verification** from `new_backend`: affected pytest files, PostgreSQL suite with a real isolated DSN, `ruff check .`, contract export.
- [ ] **Step 6: Run frontend verification** from `new_frontend`: affected Vitest files, `npm run typecheck`, `npm run lint`, `npm run build`.
- [ ] **Step 7: Run isolated Staging smoke**, record release/product/schema digests and exact CORAL case IDs, then commit `deploy: add generic MCP tool product receiver`.

## Final Verification

- [ ] A synthetic MCP endpoint is onboarded, qualified and published without editing React routes, Python public routes or Nginx.
- [ ] One product page successfully selects and runs at least two different Capability bindings.
- [ ] Immediate and long-running paths survive refresh and preserve idempotency, progress, cancellation and usage semantics.
- [ ] A failed/missing GPU metric blocks qualification when required; service-reported usage is visibly labelled.
- [ ] CORAL four-Capability acceptance evidence includes real result shape, owned Artifacts and recorded usage source.
- [ ] Existing Agent chat, generic compute jobs, MCP Agent tools, AF3, authentication and private admin ingress regressions remain green.
- [ ] A published release can be suspended and rolled back without rewriting historical Jobs or changing Nginx.
