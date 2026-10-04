# Account profile and daily usage settings

The requested implementation adds nickname editing, avatar upload and a Codex-style daily usage calendar to the existing settings page. Authentication continues through Python; the frontend never talks directly to Supabase with a service credential.

## Profile ownership and persistence

- `PATCH /api/v1/me` saves a trimmed, single-line nickname of 1–80 characters. Supabase Auth merges `user_metadata.full_name`; the stable account ID, email, quota and workspace ownership remain intact. Mock mode updates all tokens for the same in-memory identity.
- `PUT /api/v1/me/avatar` accepts PNG/JPEG/WebP up to 4 MiB and 16 million pixels. Python validates actual bytes, corrects image orientation, crops to a 256 px square and encodes WebP without the original metadata. Decoding runs outside the event loop with two concurrent processing slots.
- Supabase Storage stores immutable objects under `pskit-avatars/<authenticated-user-id>/<random-revision>.webp`. The private bucket is created on first upload and rejects public misconfiguration. The required `SUPABASE_SECRET_KEY` stays in the backend's private environment file. No SQL profile table or additional database is necessary: Supabase Auth already persists account metadata in PostgreSQL, and Storage uses the existing deployment's storage volume.
- Public identities expose only `avatar_revision`, not storage credentials or internal object paths. `GET /api/v1/me/avatar` derives the current user's path and returns private image bytes after Python authentication. `DELETE` removes the profile reference before cleaning the old object. Successful replacements also remove the old object.
- A lost Auth response may follow a committed metadata update. The newly uploaded object is retained on that ambiguous failure to avoid a broken active avatar; obsolete cleanup failure does not roll back a successful profile write. Rare orphan objects need later maintenance. No blanket RLS read/write policy is installed.
- React updates the current identity and sidebar on successful writes. An old request cannot replace a different currently logged-in account. Authenticated avatar blobs are cached by user/revision; object URLs are revoked on component cleanup. Profiles are not stored as browser preferences.

## Usage calendar

- `GET /api/v1/usage/activity?days=365` returns every date through today, bounded to 1–366 days, with zero-filled `tokens` and `gpu_ms` totals and `timezone: UTC`. The endpoint always uses the authenticated owner.
- The projection reads the full ledger rather than the latest 100 history entries. Model attempts count measured usage, including failed billed attempts. Reservation rows and their reconciliation transfers are excluded. Direct charges and standalone signed adjustments are included; negative net daily totals display as zero.
- Legacy AF3 actual minutes remain allocated to their original quota admission day. Generic compute jobs use the provider's recorded per-day device milliseconds, including reported usage before the job completes. Generic jobs are excluded from the legacy projection to avoid double counting.
- The UI has separate Token/GPU metrics, a seven-row calendar, small blue squares, month labels, an intensity legend and exact daily details. Each metric's four nonzero color levels scale against that metric's maximum in the displayed period. Empty usage remains gray. Mobile users scroll the chart horizontally; the page stays within the viewport.
- One calendar cell participates in Tab navigation. Arrow keys move by day/week; Home/End jump to the range edges. Labels and feedback support Chinese and English. The first metric is Token; weekly and cumulative chart modes are outside this request.

## Delivery boundary

Verify public HTTP and UI behavior with TDD, the isolated PostgreSQL test database, a disposable account against local Supabase Auth/Storage, and Chromium at desktop/mobile widths in both themes. Keep existing cloud deployments and user data untouched; committing these changes does not publish them.
