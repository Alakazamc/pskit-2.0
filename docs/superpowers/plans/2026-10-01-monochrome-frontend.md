# PSKit Monochrome Frontend Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task by task. Steps use checkbox syntax for tracking.

**Goal:** Replace the current green project-first frontend with the approved monochrome, chat-first workspace and dedicated laboratory tool views.

**Architecture:** Keep React Router, TanStack Query, the existing API client, Composer, and run event handling. Add one shared workspace shell with route-driven content; the backend's per-user default project is the storage scope for personal chats and stays hidden from the visible project list. Shared layout and visual tokens live in one monochrome stylesheet. Tool pages use the common shell but have their own form/result components. Existing backend capabilities are used only where their schemas support the operation; unavailable model operations show an explicit unavailable state.

**Tech Stack:** React 19, TypeScript, Vite, React Router, TanStack Query, Radix primitives, Lucide, Vitest, Testing Library.

**Spec:** `docs/PSKIT_MONOCHROME_UI_SPEC_2026-10-01.md`

## Global Constraints

- Main UI offers light and dark themes with grayscale chrome; scientific results may use color only with a legend and text values.
- New chat is the authenticated landing route. Personal chats are persisted under backend `project-${user.id}` until deliberately moved.
- A selected conversation row keeps a full-width neutral gray background; keyboard focus is separate and visible.
- Project spaces are personal. Project default Skills are limited to three and other Skills remain user selected.
- Do not present mock PDB/AF3 results as measured science. The current backend has `search_pdb`, `fetch_uniprot`, and a quota-oriented AF3 job API; INABe prediction and AF3 sequence submission are not yet present.
- Preserve existing auth, streaming run, file download, and approval behavior.

## Review Focus

- An empty new chat creates a backend session only after the first actual message; blank submissions do nothing.
- A session deep link restores its selected row and run state after refresh.
- Moving a chat validates ownership and target project, and keeps its messages and runs.
- Theme selection persists per browser and produces readable focus/selected contrast in both modes.
- Tool failures and missing capabilities remain visible errors; a mock response never appears as an experimental result.

---

### Task 1: Shared shell, routes, and theme

**Files:** Modify `new_frontend/src/app/App.tsx`, `new_frontend/src/features/workspace/WorkspacePage.tsx`, `new_frontend/src/features/workspace/ProjectSidebar.tsx`; create `new_frontend/src/styles/monochrome.css`; update tests in `new_frontend/src/app` and `new_frontend/src/features/workspace`.

**Interfaces:** Landing `/`; personal chat `/chats/:sessionId`; projects `/projects` and `/projects/:projectId`; tools `/tools`, `/tools/runs`, `/tools/:toolId`; skills `/skills`; settings `/settings`. Legacy `/projects/:projectId/sessions/:sessionId` remains readable.

- [x] Add route and selected-row tests; observe first-send failure before implementation.
- [x] Implement theme tokens, persistent theme toggle, sidebar, mobile drawer, header, and routes.
- [x] Make the new chat route the auth redirect and ensure old session deep links still work.
- [x] Run focused tests, build, and lint.

### Task 2: Chat-first flow and Skill selection

**Files:** Modify `WorkspacePage.tsx`, `Composer.tsx`, `Conversation.tsx`; add focused tests.

**Interfaces:** `send(payload)` creates a session in the default personal project when none is selected, then uses the existing message endpoint and run events. Composer receives optional default Skills and shows removable structured refs.

- [x] Test first-send creation, session restoration, and explicit Skill refs.
- [x] Implement centered empty chat, active transcript, personal history selection, move action, and structured Skill picker.
- [x] Run focused tests and full suite.

### Task 3: Project organization and durable Skill defaults

**Files:** Add project detail/list view and tests; add backend session move and project Skill default endpoints only as needed, with tests in `new_backend/tests`; extend typed API client.

**Interfaces:** Move session to an owned project without copying messages. Store at most three Skill IDs per project; resolve display names from the existing Skill catalog. Existing project APIs remain compatible.

- [x] Test move ownership, message continuity, default Skill cap, and persistence.
- [x] Implement backend endpoints and store methods for both in-memory and SQLite adapters.
- [x] Test project creation, default Skill settings, and moving a chat in the frontend.
- [x] Implement project pages and move dialog using Radix Dialog.
- [x] Run frontend and backend focused suites.

### Task 4: Tool catalog and dedicated workspaces

**Files:** Create `new_frontend/src/features/tools/` components and tests; extend `api/types.ts` only for supported capability schemas.

**Interfaces:** PDB uses `invokeMcpTool('search_pdb', {query})`; INABe checks discovered tool capability and reports unavailable until an adapter exists; AF3 clearly labels the existing job API as demonstration/quota mode because sequence payload is not accepted. A new server-side tool run store records MCP calls and supports saving to a project.

- [x] Test navigation, PDB result, missing INABe capability, AF3 unavailable state, and run history.
- [x] Implement tool directory, server-backed run list, PDB, INABe, and AF3 pages with distinct forms and states.
- [x] Run focused tests and full suite.

### Task 5: Responsive polish and verification

**Files:** Refine monochrome CSS and update design handoff notes.

- [x] Verify desktop and mobile rendering for both themes, selected chat, project modal, PDB result, and saved run.
- [x] Check focus styles, labels, disabled controls, and all implemented mobile routes for horizontal overflow.
- [x] Run `npm test`, `npm run lint`, and `npm run build` in `new_frontend`; run the full `new_backend` suite.
- [x] Record remaining backend capability gaps explicitly; do not imply model execution that the server cannot perform.

## Execution note

The user authorized implementation in this conversation. Work stays in the current checkout because `new_frontend/` and `new_backend/` are untracked user workspace directories; a new Git worktree would omit them. No automatic commits or resets.
