# Pi Agent Wakeup Implementation Plan

> **For agentic workers:** Use red → green TDD at each public seam; keep the original mock mode working.

**Goal:** Add an opt-in Pi RPC path whose AF3 mock task completes without browser polling and wakes the same Pi session after a restart.

**Architecture:** Python owns SQLite run/task/event state and a background coordinator. A per-session Pi RPC subprocess owns reasoning and a small project extension. Existing HTTP paths serve snapshots and events. The default in-memory demo is unchanged.

**Tech stack:** FastAPI, Python sqlite3/asyncio, Pi coding-agent RPC, JavaScript extension, pytest/httpx, Node test runner.

## File map

- `new_backend/app/domain/persistent_conversation.py`: SQLite conversations, run/task transitions, event cursor, quota transactions.
- `new_backend/app/adapters/live/pi_rpc.py`: LF-delimited Pi RPC subprocess client, response correlation, event projection.
- `new_backend/app/services/agent.py`: start/resume coordinator and background job scheduler.
- `new_backend/app/api/internal.py`: authenticated tool submission and completion lookup for Pi extension.
- `new_backend/pi/extension.js`: `submit_af3` tool and hidden `pskit_resume` command.
- `new_backend/app/main.py`, `app/api/workspace.py`, `app/api/runs.py`, `app/api/capabilities.py`, `app/config.py`: explicit mode wiring and API integration.
- `new_backend/tests/test_pi_agent_http.py`, `test_pi_rpc.py`, `pi/extension.test.js`: behavior tests.
- `new_frontend/src/features/chat/events.ts`, `src/features/workspace/WorkspacePage.tsx`: show waiting/failure and restore the latest Run after a reload.
- `new_backend/README.md`, `.env.example`, `contracts/openapi.json`: operation and contract docs.

## Task 1: Durable HTTP state

1. Write an HTTP test: a run, message, and events survive constructing a new app with the same SQLite path; foreign users still receive 404.
2. Run the test and observe red.
3. Implement the minimal SQLite-backed conversation and ordered events store in opt-in Pi mode.
4. Run the test and existing backend tests until green.

## Task 2: Pi RPC protocol

1. Write a test with a fake executable that emits command responses, Unicode text deltas, and `agent_settled`; assert response ID correlation, final text and session path.
2. Run red, implement the binary JSONL client with deadline and child cleanup, then run green.
3. Add a test for rejected prompt and unexpected exit; expose failure to the coordinator.

## Task 3: Tool submission and task state

1. Write HTTP tests for run-bound internal token, AF3 GPU reservation, per-user visibility, and completion idempotence.
2. Run red; implement SQLite job and quota transactions plus the internal endpoint; run green.
3. Write a Node test for extension `submit_af3` returning pending and `terminate:true`; run red then implement the extension.

## Task 4: Automatic wakeup

1. Write an HTTP integration test with fake Pi: submit message, wait for `waiting`, let backend clock advance, see completed task, internal completion command, and final `run.completed` without fetching the job.
2. Run red; implement background scheduler and serialized per-session run/resume coordinator; run green.
3. Write restart and duplicate completion tests; implement compare-and-set wake claim and recovery; run green.

## Task 5: Frontend and delivery

1. Add frontend tests for `run.failed` and reloading an in-progress Run, then implement status/error display and event recovery.
2. Regenerate OpenAPI and update README/env examples.
3. Run backend tests and Ruff; run frontend tests, typecheck, lint, build; record any real-Pi smoke limitation explicitly.
