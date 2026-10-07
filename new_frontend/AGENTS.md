# Frontend UI rules

## Inputs and model picker

- Text inputs use theme surface and border tokens. Keep composer and popover search inputs borderless in normal, focus, and focus-visible states; indicate keyboard focus through the containing row's theme background. Bright white rectangular input frames are prohibited. Scope these styles to the field so the shared focus rules cannot override them, while other controls retain visible keyboard focus.
- Place the selected model name and thinking strength as plain text at the composer's bottom right, immediately beside Send/Stop. Open a narrow settings panel above that entry with an upright airplane grip on a vertical track and the current level beside it. Put a clickable upward caret below the level to open the model list; use this entry instead of a separate model button. Stronger levels sit at the top and default at the bottom; support mouse, touch, and keyboard. Show only advertised thinking levels and reset to default when switching models.
- Keep the thinking panel compact, approximately 88 × 122 px at normal zoom with 4 px inner padding. Fix the strength text at the vertical center of the panel and center it horizontally in the column beside the slider, with the upward caret directly below. Preserve a 44 px wide drag target inside the panel and readable labels, including the longest English level; the searchable model list opens separately at a readable width.
- Use subdued exhaust colors inspired by real fighter jet photography: smoky blue at lower levels, gray violet in the middle, pale amber at the top. Keep each level distinct, with a narrow off-white core, translucent edges, a fading tip, and restrained glow; increase length and brightness with strength. Default and off have no flame. Key colors by the actual level so models with fewer levels retain the same colors. Preserve text labels and the compact panel dimensions.
- Verify rendered input focus and lever behavior in both themes at desktop and mobile widths; CSS declarations alone do not establish that the focus frame is gone.

## Conversation drafts

- Scope composer state by authenticated user ID and conversation ID. Persist unsent text, model, thinking strength and ready context IDs in versioned browser localStorage; restore them before displaying a different conversation. Keep personal and project new-chat drafts separate, then move a new-chat draft into its created session.
- On accepted sends, clear the submitted content while preserving model preferences and any newer typing. Retain rejected submissions. Bind asynchronous uploads and send completion to their originating draft; reset transient controls on conversation changes.
- Cache only the necessary draft fields. Keep file contents, temporary blob URLs and credentials out of draft storage, and keep preference persistence entirely in the browser. Verify switching, refresh, account isolation and late completions through the UI; unavailable storage must leave the current composer usable.

## Conversation titles

- New chats opt into server-side automatic naming after the first complete assistant reply. Use the first user text as a temporary title. Naming is a separate, quota-accounted request; it neither enters Pi's transcript nor keeps Send/Stop waiting after the reply finishes. Preserve manually renamed and historical titles. Refresh pending title snapshots until generation settles or a network error occurs, then stop refreshing; update the sidebar/header without clearing drafts or changing the selected model.

## Conversation artifacts

- Put one compact Artifacts button at the upper right of an existing conversation's chat header. Open a compact popover named for the current conversation with only one "Outputs / 产出" section; do not add change, subagent, source, or other tabs. List that conversation's outputs only; keep preview and download in its detail panel. Fetch through an owned session-scoped API and refresh when the Run produces new outputs. Keep artifacts out of the sidebar.
- A new chat without a session has no artifact control. Switching conversations resets the open list and preview to the newly selected session. Legacy `/artifacts` links return to the chat entry instead of showing an all-session catalog.

## Catalog cards and detail panels

- Sidebar destinations with catalogs reuse `src/components/catalog/CatalogCard.tsx` and `src/styles/catalog.css`. Skills, resources and projects use the compact 100 × 80 px variant with 10 px gaps. Tools use the large 200 × 160 px variant; keep it readable on narrow screens. Show only an icon, name and short description on cards.
- Open a Tool card at its `/tools/...` route within the full Tools content area. Replace the workspace's Tools heading with an icon-only Back to tools button and the actual tool name; put its gray description below the name in the same top bar. Use this single header for built-in viewers, MCP tools and configured products, keeping theme and authorized edit actions available. Parameters and that tool's history start directly below the header. Keep Skill and resource details in their shared modal until those flows are changed explicitly.
- Add a borderless search field for large catalogs, filtering names and descriptions. Use theme tokens in both themes and show loading, empty, no-match and error states. Keep each card one keyboard-accessible action with its full name and a visible focus state.
- Open details using the shared `DetailPanel.tsx`, centered over the retained directory. Keep long content scrollable within the viewport, support Escape/Close and return focus to the opening card. Project cards may open their existing project workspace. Chat messages, usage summaries and form sections retain their purpose-specific layouts.
- Put the slanted pencil at the detail panel's top right when the user has an authorized edit operation. Editing exposes actual editable fields (or a file tree and content editor for Skill packages when supported); save/cancel actions must follow a real write contract. Tool pencils focus editable invocation parameters. Public read-only catalogs show details without a pretend save action.
- Keep tool run history inside that tool's details alongside its invocation parameters. Request history with the exact tool name and authenticated user; scope query caches by both. Fetch history when its tab opens, retain entered parameters when switching tabs, and preserve project assignment. The Tools directory has no aggregate history entry.
- Verify compact rendered dimensions, search focus, panel scrolling, edit focus and per-tool history isolation at desktop and mobile widths in both themes.

## Scientific tool workspaces

- Specialized tools such as CORAL reuse the catalog card and detail panel. Arrange target inputs on the left and actual results on the right on desktop. Use server-observable acceptance, execution and completion nodes; percentages come from the service. Respect advertised input limits, show unconfigured/error/cancellation states, and never animate fabricated scientific stages or result counts.
- Put download and Agent task actions beside the result summary. Keep previews bounded and keyboard-scrollable with an explicit scrolling hint; display full dataset completeness. On mobile, use one detail scroll area, fold completed parameters into a short editable summary, and keep result actions visible near the top. Completed jobs use a secondary regenerate action.
- Agent handoffs use the completed job's immutable input snapshot and a bounded result summary. Attach complete datasets through owned file APIs instead of putting bulk data in a prompt. Label partial data honestly. Upload before creating a new session; retries retain uploaded references, session identity and message idempotency. Offer small, clearly named preset tasks in a touch-accessible menu explaining that they continue in a new conversation.

## Page headings

- Use one clear page title. Keep page-level descriptions only when they add information needed to act; do not repeat the navigation label or describe obvious page functions. Preserve user-written project descriptions and tool-specific guidance.

## Profile and usage settings

- Center settings within a bounded content width. Use quiet section dividers, theme surfaces and compact form controls; account forms and usage charts retain their purpose-specific layouts instead of catalog cards. Keep nickname saving and avatar upload/removal in the Profile section, and update the sidebar after a successful Python API response. Preserve unsaved input on failure.
- Account profiles come from the server. Fetch private avatars through the authenticated Python API and use temporary blob URLs only for rendering; revoke them on replacement/unmount. Scope avatar and usage caches by user ID, and avatar images by revision. Keep Supabase service credentials out of React and browser storage.
- Display actual daily usage as a seven-row calendar of small blue squares, with month labels, a low-to-high legend and exact date/value details on hover or keyboard focus. Token and GPU are separate selectable metrics; dates use the API's UTC calendar. Show real zero-usage days without generating sample activity. Provide loading/error/empty states, a single Tab entry with arrow-key navigation, and horizontal chart scrolling on narrow screens without overflowing the page.
- Verify nickname persistence, sidebar synchronization, upload/removal, calendar details, keyboard focus and page/chart scrolling in both themes at desktop and mobile widths.

## Attachments

- Keep upload guidance out of the composer until a limit is exceeded. Count ready and pending attachments together, including images, against ten files per turn. Accept files up to the remaining capacity and show a bilingual overflow alert only for the excess; the user can select the remaining files next turn. Release places on removal, cancellation, or failure, and reset the allowance after a successful send. Keep per-file size and format validation in upload errors.
- Keep attachment previews in a bounded, keyboard-accessible scrolling region so ten files leave the input and send action visible on small screens. Use the shared theme scrollbar styles.

## Streaming replies

- Show the submitted user message immediately and keep it visible while POST/history refresh is pending; roll it back on rejection while retaining the composer draft. Place the waiting indicator at the assistant body's top-left content origin only until the first nonempty text arrives. Stream text at that same origin, replacing the indicator; use the same body layout for saved replies. Show reply copy actions only when the run completes, fails, or is cancelled, while keeping historical replies copyable. Keep each run's display isolated when switching runs or sessions.
- Keep the composer stop action active until the run ends. Verify streamed Markdown growth follows the bottom automatically while preserving the position of a reader who scrolls upward, in both themes at desktop and mobile widths.

## Sidebar

- Align section labels and personal chat titles on one left edge. Put the add-project action in the project section heading, keep chat actions available on hover and keyboard focus, and use a full-row gray selected state.
- Place account access at the sidebar's bottom avatar/name row, including an avatar-only entry when collapsed. Clicking it opens a compact menu upward with identity, Profile, Settings and Sign out; omit a separate top-right settings button. Profile navigation focuses nickname editing, preserves unsaved form values on repeat visits and closes the mobile drawer. Support Escape, outside-click dismissal, keyboard navigation and appropriate focus return in both themes; retain the guest sign-out warning.

## Scrollbars

- Use `src/styles/scrollbars.css` for every scrollable surface. Keep tracks transparent, thumbs narrow and rounded, and thumb colors tied to the active light or dark theme.
- Keep scrollbar arrows hidden. The chat composer textarea is not manually resizable; its overflow uses the shared scrollbar.
- In rounded input containers, inset the scrollable textarea so its scrollbar stays inside the rounded border, including at the top and bottom of the thumb. Check the visible thumb position with overflowing text rather than clipping it at the container edge.
- After changing scrolling behavior, inspect a long chat, a form textarea, and a scrollable page in both themes at desktop and mobile widths. Check that keyboard focus remains visible.
