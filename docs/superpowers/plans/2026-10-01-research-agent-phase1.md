# Research Agent Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a standalone React research workbench and Python mock API with explicit Supabase/New API integration seams and per-user Token/daily GPU quota contracts.

**Architecture:** `new_frontend/` and `new_backend/` run independently and communicate through `/api/v1` and SSE. The frontend has mock and HTTP implementations of one typed API; the backend has in-memory mock domain state behind provider ports. Existing PSKit services remain untouched.

**Tech Stack:** React, Vite, TypeScript, Tailwind CSS, Radix/shadcn primitives, assistant-ui chat primitives, TanStack Query, Zustand, Vitest, Testing Library; Python FastAPI, Pydantic, pytest.

**Spec:** `docs/superpowers/specs/2026-10-01-research-agent-phase1-design.md`

## Global Constraints

- New directories are exactly `new_frontend/` and `new_backend/`; no imports from the old backend.
- Mock mode works without external credentials. Live mode fails clearly when required configuration is absent.
- Frontend never receives New API admin/user secrets or GPU scheduler credentials.
- Raw tokens, New API quota points, and GPU minutes are distinct units.
- Backend owns authorization and quota decisions. UI never derives daily reset time from its own clock.
- TDD seams: public HTTP API, SSE/event consumer, and user-operable React views. One failing test precedes each minimal implementation.
- Each frontend slice runs `npm run typecheck`, `npm run lint`, and `npm run build`; backend slices run their focused pytest checks.

## Review Focus

1. Missing auth on a protected route returns 401 without exposing another user's data — Task 1 test.
2. A user requesting another user's session/run/task gets 404 — Tasks 2 and 3 tests.
3. Insufficient GPU quota rejects AF3 before reservation and does not mutate usage — Task 3 test.
4. SSE reconnection from an event ID emits only later events and keeps run IDs stable — Task 2 test.
5. Token usage and New API quota points never share a numeric unit or label — Tasks 1 and 5 tests.

---

### Task 1: Backend app, identity, and quota snapshot

**Files:** Create `new_backend/pyproject.toml`, `new_backend/app/main.py`, `new_backend/app/contracts/models.py`, `new_backend/app/domain/store.py`, `new_backend/app/api/auth.py`, `new_backend/app/api/usage.py`, `new_backend/tests/test_auth_usage.py`.

**Interfaces:** `create_app(settings: Settings | None = None) -> FastAPI`; `GET /api/v1/me`; `POST /api/v1/auth/demo`; `GET /api/v1/usage`. Public `UsageSnapshot` has `tokens`, `model_credits`, and `gpu` with the fields in the spec.

- [ ] Write `test_demo_login_returns_user_and_distinct_usage_units`, `test_protected_usage_requires_auth`, and `test_quota_snapshot_is_scoped_to_user` through TestClient.
- [ ] Run `pytest tests/test_auth_usage.py -q` and confirm red.
- [ ] Implement minimal demo token storage, request auth dependency, and per-user usage snapshot.
- [ ] Run focused pytest and confirm green; commit the slice.

### Task 2: Projects, sessions, messages, and resumable run events

**Files:** Create `new_backend/app/api/workspace.py`, `new_backend/app/api/runs.py`, `new_backend/app/domain/conversation.py`, `new_backend/tests/test_conversation_events.py`.

**Interfaces:** `GET /api/v1/g`, `GET /api/v1/g/{projectKey}/c`, `GET /api/v1/c/{id}/messages` or `GET /api/v1/g/{projectKey}/c/{id}/messages`, matching `POST` paths returning `{run_id}`, and `GET /api/v1/runs/{id}/events?after={event_id}`. Event has `id`, `run_id`, `type`, `created_at`, and typed payload.

- [ ] Write tests for owned message submission, foreign session 404, ordered SSE events, and resume after an ID without duplicates.
- [ ] Run `pytest tests/test_conversation_events.py -q` and confirm red.
- [ ] Implement minimal in-memory project/session/run store, event emission, and SSE serializer.
- [ ] Run focused pytest and confirm green; commit the slice.

### Task 3: Mock MCP and AF3 with daily GPU admission

**Files:** Create `new_backend/app/api/capabilities.py`, `new_backend/app/domain/quota.py`, `new_backend/app/adapters/mock/mcp.py`, `new_backend/app/adapters/mock/af3.py`, `new_backend/tests/test_capabilities.py`.

**Interfaces:** `GET /api/v1/mcp/tools`, `POST /api/v1/mcp/tools/{name}/invoke`, `POST /api/v1/af3/jobs`, `GET /api/v1/af3/jobs/{id}`. `reserve_gpu(user_id, estimated_minutes)`, `settle_gpu(job_id, actual_minutes)`, and failure release act on one user's daily bucket.

- [ ] Write tests for deterministic MCP response, AF3 queue-to-complete events, daily GPU reservation/settlement, exhausted quota with no mutation, and foreign job 404.
- [ ] Run `pytest tests/test_capabilities.py -q` and confirm red.
- [ ] Implement minimal mock providers and quota admission; publish `task.updated`, `usage.updated`, `artifact.created`, and final run events.
- [ ] Run focused pytest and confirm green; commit the slice.

### Task 4: Live service adapters and OpenAPI contract

**Files:** Create `new_backend/app/ports/providers.py`, `new_backend/app/adapters/live/supabase_auth.py`, `new_backend/app/adapters/live/new_api.py`, `new_backend/tests/test_live_adapters.py`, `contracts/README.md`, `contracts/openapi.json`.

**Interfaces:** `IdentityProvider.verify(access_token) -> UserIdentity`; `ModelUsageProvider.get_usage(user_id) -> ModelUsage`; live Supabase adapter verifies bearer token using Auth API; New API adapter reads `/api/usage/token` using a server-side per-user token source. `contracts/openapi.json` is generated from FastAPI.

- [ ] Write adapter tests with HTTPX MockTransport for valid/expired Supabase tokens and New API `total_granted/used/available` fields. Assert no New API key appears in API JSON.
- [ ] Run `pytest tests/test_live_adapters.py -q` and confirm red.
- [ ] Implement adapters, explicit mock/live configuration, and OpenAPI snapshot generation.
- [ ] Run focused pytest and a snapshot regeneration check; commit the slice.

### Task 5: Frontend app, API port, mock auth, and usage

**Files:** Create `new_frontend/package.json`, Vite/TS/Tailwind config, `new_frontend/src/app/*`, `new_frontend/src/api/{types,client,mock,http}.ts`, `new_frontend/src/features/{auth,usage}/*`, and focused Vitest tests.

**Interfaces:** `ResearchApi` exposes `loginDemo`, `getMe`, `getUsage`, project/session/message methods, `subscribeRun`, `listMcpTools`, `invokeMcpTool`, `submitAf3`, and `getAf3Job`. Environment selects `mock` or `http` before rendering.

- [ ] Write failing tests for mock login, distinct quota labels and reset times, and HTTP adapter request/response shape.
- [ ] Run focused Vitest and confirm red.
- [ ] Scaffold React app and implement the API implementations, auth route, query provider, and quota card.
- [ ] Run focused tests plus `npm run typecheck`, `npm run lint`, `npm run build`; commit the slice.

### Task 6: Workspace shell, chat, and Composer

**Files:** Create `new_frontend/src/features/workspace/*`, `new_frontend/src/features/chat/{Conversation,Composer,MessagePart,ToolCard,AgentPanel}.*`, `new_frontend/src/styles/*`, and focused interaction tests.

**Interfaces:** Route `/projects/:projectId/sessions/:sessionId`; `ComposerState` stores text and selected attachment/skill/resource references; `MessagePart` renders text/tool/artifact/progress variants; `AgentPanel` shows Plan/Artifacts/Compute tabs.

- [ ] Write failing user interaction tests for project/session navigation, `/` Skill selection, `@` Resource selection, file chip removal, and sending structured message references.
- [ ] Run focused Vitest and confirm red.
- [ ] Implement shell, mint theme, responsive panels, message rendering, and Composer without direct fetch in components.
- [ ] Run focused tests plus typecheck/lint/build; commit the slice.

### Task 7: Event stream, MCP/AF3 demo, and integration

**Files:** Create `new_frontend/src/features/chat/events.ts`, `new_frontend/src/features/chat/useRunEvents.ts`, demo scenario fixtures, `new_frontend/src/features/chat/*.test.tsx`, `new_frontend/README.md`, `new_backend/README.md`.

**Interfaces:** `subscribeRun(runId, afterId, onEvent) -> unsubscribe`; event reducer deduplicates by `id`, maintains `waiting/running/completed/failed`, and displays MCP tool result, AF3 progress, artifact and final message.

- [ ] Write failing tests for MCP fast result, AF3 waiting-to-completed flow, stream reconnection without duplicate cards, quota error, and refresh from snapshot.
- [ ] Run focused Vitest and confirm red.
- [ ] Implement mock scenarios, HTTP SSE reader, event projector, demo controls, and mode setup documentation.
- [ ] Run full frontend tests, typecheck, lint, build; full backend pytest; one browser path against the mock backend; commit the slice.

## Final Review

- Regenerate `contracts/openapi.json` and compare with frontend types and mock responses.
- Check that no secret or placeholder credentials reached client bundle or version control.
- Check `new_frontend/` works alone and with `new_backend/` in mock mode.
- Record any real Supabase/New API configuration that could not be exercised without user credentials.
