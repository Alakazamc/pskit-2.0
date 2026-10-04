# Frontend UI rules

## Inputs and model picker

- Text inputs use theme surface and border tokens. Keep composer and popover search inputs borderless in normal, focus, and focus-visible states; indicate keyboard focus through the containing row's theme background. Bright white rectangular input frames are prohibited. Scope these styles to the field so the shared focus rules cannot override them, while other controls retain visible keyboard focus.
- Place the selected model name and thinking strength as plain text at the composer's bottom right, immediately beside Send/Stop. Open a narrow settings panel above that entry with an upright airplane grip on a vertical track and the current level beside it. Put a clickable upward caret below the level to open the model list; use this entry instead of a separate model button. Stronger levels sit at the top and default at the bottom; support mouse, touch, and keyboard. Show only advertised thinking levels and reset to default when switching models.
- Keep the thinking panel compact, approximately 88 × 122 px at normal zoom with 4 px inner padding. Fix the strength text at the vertical center of the panel and center it horizontally in the column beside the slider, with the upward caret directly below. Preserve a 44 px wide drag target inside the panel and readable labels, including the longest English level; the searchable model list opens separately at a readable width.
- Use subdued exhaust colors inspired by real fighter jet photography: smoky blue at lower levels, gray violet in the middle, pale amber at the top. Keep each level distinct, with a narrow off-white core, translucent edges, a fading tip, and restrained glow; increase length and brightness with strength. Default and off have no flame. Key colors by the actual level so models with fewer levels retain the same colors. Preserve text labels and the compact panel dimensions.
- Verify rendered input focus and lever behavior in both themes at desktop and mobile widths; CSS declarations alone do not establish that the focus frame is gone.

## Page headings

- Use one clear page title. Keep page-level descriptions only when they add information needed to act; do not repeat the navigation label or describe obvious page functions. Preserve user-written project descriptions and tool-specific guidance.

## Attachments

- Keep upload guidance out of the composer until a limit is exceeded. Count ready and pending attachments together, including images, against ten files per turn. Accept files up to the remaining capacity and show a bilingual overflow alert only for the excess; the user can select the remaining files next turn. Release places on removal, cancellation, or failure, and reset the allowance after a successful send. Keep per-file size and format validation in upload errors.
- Keep attachment previews in a bounded, keyboard-accessible scrolling region so ten files leave the input and send action visible on small screens. Use the shared theme scrollbar styles.

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
