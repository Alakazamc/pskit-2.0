# Frontend UI rules

## Inputs and model picker

- Text inputs use theme surface and border tokens. Keep composer and popover search inputs borderless in normal, focus, and focus-visible states; indicate keyboard focus through the containing row's theme background. Bright white rectangular input frames are prohibited. Scope these styles to the field so the shared focus rules cannot override them, while other controls retain visible keyboard focus.
- The model picker opens above the composer. Use a vertical, draggable lever for thinking strength: stronger levels at the top, default at the bottom, with visible level labels and mouse, touch, and keyboard support. Show only levels advertised by the selected model and reset to default when switching models.
- Verify rendered input focus and lever behavior in both themes at desktop and mobile widths; CSS declarations alone do not establish that the focus frame is gone.

## Page headings

- Use one clear page title. Keep page-level descriptions only when they add information needed to act; do not repeat the navigation label or describe obvious page functions. Preserve user-written project descriptions and tool-specific guidance.

## Streaming replies

- Put the generating indicator in a reserved icon slot at the top left of the assistant reply, before its content. Show it while awaiting the first event and while generating; replace it with a static icon when the run ends or waits for approval. Use the same slot for saved replies so completion does not shift the body. Never put the primary spinner in the bottom copy-action row or a bar above the composer.
- Keep the composer stop action active until the run ends. Verify streamed Markdown growth follows the bottom automatically while preserving the position of a reader who scrolls upward, in both themes at desktop and mobile widths.

## Sidebar

- Align section labels and personal chat titles on one left edge. Put the add-project action in the project section heading, keep chat actions available on hover and keyboard focus, and use a full-row gray selected state.

## Scrollbars

- Use `src/styles/scrollbars.css` for every scrollable surface. Keep tracks transparent, thumbs narrow and rounded, and thumb colors tied to the active light or dark theme.
- Keep scrollbar arrows hidden. The chat composer textarea is not manually resizable; its overflow uses the shared scrollbar.
- In rounded input containers, inset the scrollable textarea so its scrollbar stays inside the rounded border, including at the top and bottom of the thumb. Check the visible thumb position with overflowing text rather than clipping it at the container edge.
- After changing scrolling behavior, inspect a long chat, a form textarea, and a scrollable page in both themes at desktop and mobile widths. Check that keyboard focus remains visible.
