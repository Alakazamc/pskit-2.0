# Conversation Rich UI Design

## Goal

Render assistant prose as safe Markdown and render structured message parts as interactive, typed UI without allowing model output to control arbitrary HTML or application styling.

## Existing foundation

- `Message.parts` is already a discriminated union covering text, tools, files, artifacts, citations, progress and errors.
- Assistant text already uses Streamdown for streaming Markdown, code, tables and math.
- Files and artifacts use authenticated resource IDs, with download and artifact preview APIs.
- Tool calls already have a tool-name renderer registry.

## Design

`MessageParts` is the only conversation-level dispatcher. It delegates each part to a registered React renderer. Built-in renderers cover every current part type and an unknown-part fallback preserves forward compatibility.

Files and artifacts share a `ResourceCard`. The card infers a human-readable format from the resource name, loads bytes only after an explicit user action, and uses the existing authenticated APIs. Text artifacts can open the existing detail panel; other resources open a browser blob and can be downloaded. Historic parts containing only `id`, `name` and optional `kind` remain valid.

Tool results use a second registry keyed by tool name. Unknown tools render a compact expandable result rather than exposing a large JSON block by default.

Live run state is projected into the same typed parts used by saved messages. Text keeps Streamdown's streaming mode; tools, progress and artifacts appear immediately and are replaced naturally by the saved assistant message after completion.

## Security and compatibility

- Markdown keeps raw HTML disabled.
- Model-authored filesystem paths such as `sandbox:/...` are never treated as trusted resources.
- Resource actions use server-owned IDs and existing authorization checks.
- HTTP citations keep the existing external-link allowlist.
- Missing resource actions produce a readable static card.
- No message migration or database schema change is required.

## Product behavior

- A DOCX/PDF/CSV/image/structure output appears as a compact file card with type, name, open and download actions.
- Tool calls and results use concise cards; detailed JSON is opt-in.
- Progress displays a determinate bar when a value is available.
- Live and historical messages use the same visual language.

