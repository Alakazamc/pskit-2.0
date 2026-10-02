# Frontend UI rules

## Page headings

- Use one clear page title. Keep page-level descriptions only when they add information needed to act; do not repeat the navigation label or describe obvious page functions. Preserve user-written project descriptions and tool-specific guidance.

## Sidebar

- Align section labels and personal chat titles on one left edge. Put the add-project action in the project section heading, keep chat actions available on hover and keyboard focus, and use a full-row gray selected state.

## Scrollbars

- Use `src/styles/scrollbars.css` for every scrollable surface. Keep tracks transparent, thumbs narrow and rounded, and thumb colors tied to the active light or dark theme.
- Keep scrollbar arrows hidden. The chat composer textarea is not manually resizable; its overflow uses the shared scrollbar.
- In rounded input containers, inset the scrollable textarea so its scrollbar stays inside the rounded border, including at the top and bottom of the thumb. Check the visible thumb position with overflowing text rather than clipping it at the container edge.
- After changing scrolling behavior, inspect a long chat, a form textarea, and a scrollable page in both themes at desktop and mobile widths. Check that keyboard focus remains visible.
