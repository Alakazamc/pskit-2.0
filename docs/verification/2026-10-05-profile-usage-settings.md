# Profile and daily usage settings verification

Date: 2026-10-05

## Delivered

- Settings supports nickname edits and avatar upload/removal, with sidebar updates after successful Python API writes. Nicknames use Supabase Auth's PostgreSQL-backed user metadata. Avatar objects use the existing Supabase Storage service, a private `pskit-avatars` bucket, and owner-derived immutable object names.
- Python limits uploaded raster images to 4 MiB and 16 million pixels, strips source metadata and normalizes them to 256 px WebP. Read routes authenticate the current owner; service credentials and storage paths stay off the frontend. Supabase Storage v1.74's HTTP 400 `NoSuchBucket` envelope is explicitly supported.
- Added owner-scoped `GET /api/v1/usage/activity` with 1–366 days, defaulting to 365, UTC dates and zero-filled real usage. Model-attempt usage, legacy settled AF3 minutes and generic per-day device milliseconds are aggregated independently of the recent 100-entry history. Admission holds and reservation reconciliation transfers are excluded.
- Extracted the settings page, profile section, shared user avatar and calendar components. The calendar has selectable Token/GPU metrics, seven rows of blue squares, month labels, an intensity legend, exact daily details and keyboard navigation. Narrow screens scroll the calendar horizontally without overflowing the page. Chinese/English and light/dark themes are supported.
- Updated generated API contracts, frontend rules and environment examples. New Staging configurations include their own Supabase server key in the existing 0600 backend environment file. Production deployment and existing backend environment files were not changed.

## TDD and integration evidence

The initial nickname API tests failed with HTTP 405; avatar endpoints were missing; daily activity tests failed with HTTP 404. New public UI tests could not find the nickname/upload/calendar controls. A regression reproducing Storage's HTTP 400 missing-bucket envelope failed with HTTP 503 before adapter correction. The Staging test initially failed because its backend environment lacked the Storage key. All now pass.

| Check | Result |
| --- | --- |
| Frontend full Vitest suite | 33 files, 213 tests passed |
| Backend final offline suite | 427 passed; 194 opt-in/environment-dependent tests skipped |
| New profile/activity APIs plus Staging configuration | 13 passed |
| PostgreSQL daily activity | 1 passed in a unique schema, removed afterward |
| Real local Supabase Auth and Storage | 1 passed using a disposable account; test objects and account removed |
| Typecheck, ESLint, production build | Passed; existing Markdown/Molstar bundle size warnings remain |
| Ruff on changed Python files and diff whitespace | Passed |
| Chromium desktop/mobile × dark/light | 4 scenarios passed with Chinese/English coverage and zero page errors |

The Supabase integration creates only its own confirmed test account, saves a nickname and avatar through the new API, recreates the application instance, verifies persistence through provider reads, checks that the public object URL cannot read the avatar, and removes both object and account. It uses local configuration in memory without printing credentials. The test creates the private avatar bucket if missing and leaves that empty bucket available for local use. No cloud or other user data is written.

The PostgreSQL test includes an explicit usage window crossing midnight. Each day receives 10,000 measured GPU milliseconds; another user's usage remains isolated. A 300-Token reservation does not appear in the calendar. HTTP tests verify that more than 100 direct Token ledger entries are included and failed billed model attempts count once.

Browser inspection verifies nickname save/reload, sidebar synchronization, avatar upload/removal, 365 rendered day cells at 14 × 14 px, metric switching, arrow-key focus, page scrolling and contained horizontal chart scrolling. Inspection JSON and screenshots are under `/tmp/pskit-settings-profile`. Browser API data is synthetic and never appears as product fixtures.

## Reproduce

- Run package scripts in `new_frontend` for `test`, `typecheck`, `lint` and `build`.
- Run the backend pytest suite from `new_backend`, adding the repository root to `PYTHONPATH`.
- For `tests/postgres/test_usage_activity_pg.py`, set `TEST_POSTGRES_DSN` to the isolated PostgreSQL test instance defined by `deploy/agent/tests/compose.pg17.test.yaml`.
- For `tests/test_supabase_profile_integration.py`, provide `TEST_SUPABASE_URL`, `TEST_SUPABASE_PUBLISHABLE_KEY` and `TEST_SUPABASE_SECRET_KEY` securely. It creates/deletes its own temporary account and requires local server-side Auth/Storage privileges.
- Start a temporary Vite preview with demo authentication and run `new_frontend/tests/browser/settings_profile.py --executable <installed Chromium path>`. All `/api/v1/` requests are intercepted by the browser test.

## Release requirements and limitations

Existing production/Staging backend environment files need that environment's `SUPABASE_SECRET_KEY`; generated fresh Staging environments now include it automatically. The key remains server-side. The existing pinned Storage image and persistent volume are reused, with no new containers or database migration.

Avatar metadata and Storage object writes cannot be one transaction. Ambiguous Auth failures or concurrent replacements can leave inactive objects; these are retained rather than risking removal of a committed active avatar. Later maintenance may reclaim them. Historical days without measured ledger rows stay zero; the implementation does not invent activity or infer unknown usage from admission holds. This work has not been deployed to the cloud.
