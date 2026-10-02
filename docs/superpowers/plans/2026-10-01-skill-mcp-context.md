# Skill, MCP and File Context Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` to implement each task with a red → green test cycle. The existing workspace contains untracked `new_backend/` and `new_frontend/`; work in place to preserve them.

**Goal:** Make a selected Skill, resource, or uploaded text file affect the Pi run, with MCP mock tools available only when the run permits them.

**Architecture:** Python owns a curated Skill catalog and user file content. Message submission resolves context IDs before charging quota, stores the run's trusted instructions and allowed tools, and gives Pi the trusted instructions separately from the user prompt. Pi registers MCP tools from Python-provided schemas and calls a run-scoped internal endpoint; the endpoint rechecks ownership and permission. The public frontend continues using `/api/v1`.

**Tech Stack:** FastAPI, Pydantic, SQLite, Pi RPC extension, React, TypeScript, Vitest, pytest, Node test runner.

**Spec:** `docs/superpowers/specs/2026-10-01-research-agent-phase1-design.md` and `docs/superpowers/specs/2026-10-01-pi-agent-wakeup-design.md`, plus the user's approved Muse-style Skill → capability direction.

## Global constraints

- Keep the old `backend/` and `frontend/` untouched.
- Test public HTTP, Pi extension and frontend API/component seams; mock only external providers.
- Selected refs are resolved by ID on the server; client supplied names and instructions are never trusted.
- Text files up to 1 MiB are usable context. Unsupported formats, including PDF until parsing exists, receive a clear error.
- Existing AF3 mock suspend/resume remains functional.

## Review focus

- A forged Skill ID must fail before any message or Token charge.
- A file ID owned by another user must not become Pi context.
- A Pi tool token cannot invoke an MCP tool outside the run allowlist.
- A restarted Pi run must retain its Skill/tool permissions for wakeup.
- File bytes and declared size must agree; unsupported files must not appear ready.

### Task 1: Curated Skill and resource catalog

**Files:** `new_backend/app/domain/catalog.py`, `new_backend/skills/*`, `new_backend/tests/test_catalog_files.py`.

- [x] Add a public HTTP test for listed Skills/resources and server-owned descriptions.
- [x] Confirm red; load curated manifests and expose them through existing endpoints; confirm green.

### Task 2: Real text file context

**Files:** `new_backend/app/contracts/catalog.py`, `new_backend/app/domain/catalog.py`, `new_backend/app/api/catalog.py`, `new_frontend/src/api/{types,http}.ts`, `new_frontend/src/features/{chat/Composer,workspace/WorkspacePage}.tsx`, related tests.

- [x] Add HTTP and frontend tests proving the file content is sent, stored per user, and rejects unsupported or forged context.
- [x] Confirm red; implement minimal upload and context lookup; confirm green.

### Task 3: Run-scoped context into Pi

**Files:** `new_backend/app/api/workspace.py`, `new_backend/app/domain/persistent_conversation.py`, `new_backend/app/services/agent.py`, `new_backend/app/adapters/live/pi_rpc.py`, related tests.

- [x] Add an HTTP test proving selected Skill instructions, resource hints, and file text reach a fake Pi runner while the visible user message stays unchanged.
- [x] Confirm red; validate IDs before quota charge, persist trusted run context, and pass it to Pi; confirm green.

### Task 4: MCP tools through Pi

**Files:** `new_backend/app/adapters/mock/mcp.py`, `new_backend/app/contracts/capabilities.py`, `new_backend/app/api/internal.py`, `new_backend/pi/extension.js`, related Python and Node tests.

- [x] Add tests for permitted call, disallowed call, invalid token, and Pi registration from run-scoped schemas.
- [x] Confirm red; implement internal invocation and extension registration; confirm green.

### Task 5: Full verification and docs

- [x] Run backend tests and Ruff, Pi extension Node tests, frontend tests/typecheck/lint/build.
- [x] Update both READMEs and contract snapshots; review the final diff against this plan.

## Implementation notes

- Curated Skill instructions are appended to Pi's system prompt; Pi's own automatic Skill discovery stays disabled in this controlled runtime.
- MCP tool definitions come from the Python provider, and an internal run-scoped endpoint verifies authorization again. Completed runs cannot reuse their internal tool token.
- This slice did not configure external model, Supabase, New API, MCP, or AF3 credentials. The installed Pi CLI was smoke-checked through `get_state`; the full prompt flow was exercised with a fake Pi process.
