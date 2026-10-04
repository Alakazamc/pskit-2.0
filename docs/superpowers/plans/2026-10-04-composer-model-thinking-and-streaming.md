# Composer Model Picker and Streaming Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Let users choose an authorized model and supported thinking level in the composer, and make active chat streaming, stop control, and scrolling behave correctly.

**Architecture:** Python derives public model capabilities from LiteLLM and pins the chosen level to each Run; Pi RPC applies it before prompting. React presents a searchable picker using those capabilities. Run state drives the composer stop control, approval stays in the conversation, and chat scrolling follows live content only while the reader is near the bottom.

**Tech Stack:** FastAPI, Pydantic, Pi RPC, React, TypeScript, Radix, Zustand, TanStack Query.

**Spec:** `docs/superpowers/specs/2026-10-04-composer-model-thinking-picker-design.md`; user additionally requested the three streaming fixes on 2026-10-04.

## Global Constraints

- The gateway key and deployment metadata stay on the server.
- Model aliases and thinking levels are discovered, never hardcoded in the UI.
- Default thinking level omits the API field and preserves old Pi behavior.
- Existing conversation and Run data need no migration.
- Chinese and English UI copy remain complete.
- Approval actions, when required by the backend, remain available inside the conversation.

## Review Focus

- Model capability metadata is missing or inconsistent: show no explicit levels.
- A selected model disappears: show its unavailable state and require an explicit new selection before sending.
- Image attachments and text-only models: prevent invalid selection and retain server validation.
- The user scrolls up during streaming: new text must not pull the viewport away.
- Run completion, cancel, or failure: composer stop control returns to send and the stream does not remain stuck.

---

### Task 1: Server Model Capability Contract

**Files:** `new_backend/app/services/model_catalog.py`, `new_backend/app/contracts/conversation.py`, `new_backend/app/api/workspace.py`, `new_backend/app/domain/conversation.py`, `new_backend/app/domain/persistent_conversation/runs.py`, `new_backend/tests/test_model_catalog.py`.

**Interfaces:** `ModelOption.reasoning_levels`; `MessageRequest.reasoning_effort`; `accept_message(..., reasoning_effort: str | None)` stores it in Run context.

- [x] Add a failing test for per-model capability discovery, wildcard exclusion, and rejected unsupported effort before Run creation.
- [x] Run the targeted test and confirm the expected failure.
- [x] Implement conservative capability derivation, request validation, and Run pinning.
- [x] Run the targeted backend tests and check they pass.

### Task 2: Pi RPC Thinking Level

**Files:** `new_backend/app/services/agent.py`, `new_backend/app/adapters/live/pi_rpc.py`, `new_backend/tests/test_pi_rpc.py`, `new_backend/tests/test_model_catalog.py`.

**Interfaces:** `PSKIT_REASONING_EFFORT` carries the pinned value; `PiRpcRunner.prompt` queries available thinking levels then sets the requested one before `prompt`.

- [x] Add a failing test that checks RPC command order and explicit-level failure behavior.
- [x] Run the targeted test and confirm the expected failure.
- [x] Implement the Pi model flag, RPC validation, and environment propagation.
- [x] Run targeted backend tests.

### Task 3: Composer Model and Thinking Picker

**Files:** `new_frontend/src/features/chat/ModelPicker.tsx`, `new_frontend/src/features/chat/Composer.tsx`, `new_frontend/src/features/chat/composerStore.ts`, `new_frontend/src/styles/composer.css`, `new_frontend/src/i18n/translations.ts`, `new_frontend/src/features/chat/Composer.test.tsx`, generated OpenAPI types.

**Interfaces:** `ModelPicker` receives authorized options and selected model/level; Composer sends `model` and optional `reasoning_effort`.

- [x] Add a failing interaction test for search, selection, level filtering, and request payload.
- [x] Run that test and confirm it fails at the old native selector.
- [x] Implement compact searchable Radix picker with keyboard and mobile handling; preserve choices after sending.
- [x] Export OpenAPI and regenerate TypeScript types; run targeted frontend tests and typecheck.

### Task 4: Active Run Controls and Streaming Scroll

**Files:** `new_frontend/src/features/mono/MonoWorkspace.tsx`, `new_frontend/src/features/chat/Conversation.tsx`, `new_frontend/src/features/chat/Composer.tsx`, `new_frontend/src/features/chat/useChatAutoscroll.ts`, related CSS and tests.

**Interfaces:** Composer receives `runActive` and `onStop`; approval controls render inside the conversation; scroll hook follows live growth near the bottom.

- [x] Add failing tests for stop button after send resolves, no control bar above composer, and streaming scroll behavior.
- [x] Run targeted tests and confirm failures match the user symptoms.
- [x] Implement run-driven controls, inline approval, and resize-aware autoscroll.
- [x] Run targeted frontend tests, then typecheck, lint, and build.

### Task 5: Contract and Release Review

**Files:** `contracts/openapi.json`, generated frontend types, affected source files.

- [x] Run backend and frontend targeted checks, inspect the final diff, and confirm no secrets or stale debug instrumentation.
- [x] Commit independent backend, frontend picker, and streaming fix changes in reviewable batches.


## Implementation and Verification — 2026-10-04

- Implemented all five tasks in the current workspace, following the user's instruction to execute directly.
- Final backend regression: `PYTHONPATH=.:.. .venv/bin/pytest tests -q` — 317 passed, 133 skipped. Skips require optional external infrastructure. Two subsequently added Pi rejection tests are covered by the final targeted model/Pi run: 24 passed. These process tests ran with the same local-port permission as the full backend regression.
- Frontend: `npm test` — 148 passed across 25 files; typecheck, lint and production build passed. The build retains existing Molstar/Markdown chunk-size warnings.
- Backend changed files pass Ruff. OpenAPI and generated TypeScript contracts are synchronized.
- Real installed Pi 0.87.1 against a temporary local HTTP server emitted `reasoning_effort=none/high/xhigh/max` for off/high/xhigh/max; no production key or paid model call was used.
- Chromium checks with temporary API responses covered light/dark themes at 1360px and 390px: model/effort payload, searchable picker, stop/cancel returning to send, streamed Markdown height growth, manual wheel scrolling, overflowing composer text and a long scrollable page. Screenshots were inspected; the composer scroll area was inset inside its rounded border.
- Fresh final review findings were fixed with focused regression tests: active runs before the first event after reload, resize observation after the first message, reasoning support across every deployment under an alias, and exclusion of explicitly non-chat models. Explicit off-to-none mapping was also verified against Pi's actual outgoing request.
- Automatic follow uses wheel, touch, keyboard and scrollbar input to recognize reader intent. Browser anchoring events alone do not interrupt following; returning near the bottom resumes it.
- Cloud deployment was not changed in this implementation. The spec's Staging check with two real LiteLLM aliases remains a release prerequisite before production publication.
