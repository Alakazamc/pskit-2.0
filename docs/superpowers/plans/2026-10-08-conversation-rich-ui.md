# Conversation Rich UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn existing structured conversation parts into safe, interactive Rich UI for saved and streaming messages.

**Architecture:** Keep Streamdown for prose and add a message-part renderer registry for semantic components. Reuse authenticated file and artifact APIs through injected resource actions, then project live run state into the same part model.

**Tech Stack:** React, TypeScript, Streamdown, Radix Dialog, Lucide, existing FastAPI message contracts.

**Spec:** `docs/superpowers/specs/2026-10-08-conversation-rich-ui-design.md`

## Global Constraints

- Preserve current `MessagePart` wire compatibility and historic messages.
- Never execute arbitrary model-authored HTML or client code.
- Never resolve model-authored local filesystem paths.
- Download and preview only through authenticated resource IDs.
- Keep Chinese and English UI copy in the shared translation catalog.
- Do not add or run automated tests unless the user explicitly requests testing.

## Review Focus

- Unknown future part types must render a safe fallback instead of disappearing.
- Missing API actions must leave readable resource metadata without dead controls.
- Browser popup blocking must not lose a downloaded blob or leave the card busy.
- Unsupported artifact previews must show a local error and retain download access.
- A completed live run and its newly saved assistant message must not render duplicate cards.

---

### Task 1: Typed part and tool-result registries

**Files:**
- Create: `new_frontend/src/features/chat/messagePartRegistry.tsx`
- Modify: `new_frontend/src/features/chat/MessageParts.tsx`
- Modify: `new_frontend/src/features/chat/toolRegistry.tsx`

**Interfaces:**
- Produces: `registerMessagePartRenderer`, `RegisteredMessagePart`, `MessageResourceActions`, `registerToolResultRenderer`.

- [x] Add the renderer context and safe unknown fallback.
- [x] Move the current switch behavior behind the registry.
- [x] Replace raw tool-result JSON with an expandable generic result card and tool-specific override seam.
- [x] Commit the registry layer.

### Task 2: Rich resource, citation and progress cards

**Files:**
- Create: `new_frontend/src/features/chat/ResourceCard.tsx`
- Modify: `new_frontend/src/features/chat/messagePartRegistry.tsx`
- Modify: `new_frontend/src/styles/chat.css`
- Modify: `new_frontend/src/i18n/translations.ts`

**Interfaces:**
- Consumes: `MessageResourceActions` from Task 1.
- Produces: authenticated open, preview and download interactions for `file` and `artifact` parts.

- [x] Implement format inference, blob opening and safe filename download.
- [x] Reuse `DetailPanel` for supported artifact previews.
- [x] Add compact accessible styles and bilingual copy.
- [x] Commit the resource card layer.

### Task 3: Saved and live conversation integration

**Files:**
- Modify: `new_frontend/src/features/chat/events.ts`
- Modify: `new_frontend/src/features/chat/Conversation.tsx`
- Modify: `new_frontend/src/features/mono/MonoWorkspace.tsx`
- Modify: `new_frontend/src/features/workspace/WorkspacePage.tsx`

**Interfaces:**
- Consumes: registry and resource actions from Tasks 1–2.
- Produces: one typed render path for historical messages and live run projections.

- [x] Preserve tool summaries while projecting events.
- [x] Build live `MessagePart` values for text, tools, artifacts and task progress.
- [x] Inject authenticated resource APIs from both workspace shells.
- [x] Keep saved-message replacement behavior so completed output is not duplicated.
- [x] Commit the conversation integration.

### Task 4: Contributor guidance and handoff

**Files:**
- Modify: `new_frontend/AGENTS.md`
- Modify: `docs/DEV_CLOUD_CONTEXT.md`

**Interfaces:**
- Produces: durable rules for future message types and a current handoff entry.

- [x] Document typed Rich UI and resource-security rules.
- [x] Record implementation scope and deferred verification in the handoff.
- [x] Commit documentation.

