# Avatar account menu

Date: 2026-10-05

## Delivered behavior

The bottom sidebar avatar/name row now opens a compact account menu upward. The menu shows the current nickname and email, then Profile, Settings and Sign out. The separate top-right settings button is removed. The collapsed sidebar retains an avatar-only account entry; mobile uses the same component.

Profile opens `/settings#profile` and focuses nickname editing. Following either navigation link closes the mobile drawer. A repeat Profile visit focuses the existing field without clearing an unsaved nickname. Closing without navigation restores avatar-button focus. Escape, outside clicks, keyboard arrows and touch are supported by the existing Radix menu primitives. Guest logout retains its existing confirmation and server logout contract.

The menu uses theme tokens, shared avatar reads and existing settings APIs. No new provider configuration, backend changes or dependencies are required. The layout rule is recorded in `new_frontend/AGENTS.md`.

## Verification

TDD: the first public App test failed because the old header settings link still existed. After replacing the entry, it passed. A subsequent profile-navigation test failed because nickname editing did not receive focus; it now passes, including repeat visits and unsaved values. Existing guest upgrade/logout tests now use the avatar menu.

| Check | Result |
| --- | --- |
| Focused App/account tests | 17 passed |
| Full frontend suite | 34 files, 217 tests passed |
| Typecheck | Passed |
| ESLint | Passed |
| Production build | Passed; existing large Markdown/Molstar chunk warnings remain |
| Diff whitespace | Passed |
| Chromium desktop/mobile × dark/light | 4 scenarios passed, Chinese/English coverage, zero page errors |

Browser inspection used synthetic HTTP responses, never real accounts. It verified the menu opening above its avatar anchor at 244 × 207 px, theme colors, viewport containment, Escape and outside-click focus return, keyboard selection, retained chat drafts, Profile focus and scroll return, repeat visits preserving unsaved values, nickname changes reflected in the menu, collapsed avatar access, mobile drawer closure and server logout. The collapsed trigger is 36 px wide with a 30 px avatar. Dark desktop and light mobile screenshots were also inspected visually.

## Reproduce

Run `npm test`, `npm run typecheck`, `npm run lint` and `npm run build` from `new_frontend`.

Start a temporary local Vite service with `VITE_AUTH_MODE=demo`, then run `python new_frontend/tests/browser/account_menu.py --executable <installed Chromium path>`. Its default base URL is `http://127.0.0.1:5197`. Browser evidence is written to `/tmp/pskit-account-menu`; all API requests are intercepted.

This change has not been deployed to the cloud.
