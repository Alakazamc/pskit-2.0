# Phase 7 — Migration and rollback

## Migration

No database migration, directory migration, deployment or state conversion was
executed. Added files provide a separate application-only entry. The existing strict
Manifest and stack/config validators remain unchanged.

Before authorized real use, qualify artifacts, install private control configuration
and register verified current API/Web manifests. Existing historical formats and
mutable pins are not accepted automatically. Provide a trusted synthetic smoke and
fresh Receiver journal/outbox observation for production API changes. Check real
Compose labels, source binds and full optional overlays with `doctor`.
Install and independently qualify the mandatory admission adapter before any API
release in either environment. The adapter's hold must remain server-enforced through
backend/controller failure and receipt expiry until explicit same-owner release.
The private configuration accepts structured commands and a reviewed qualification
digest, not a maintenance boolean. No adapter is installed or qualified by this phase;
API mutation remains unavailable without it. Read-only/Web commands do not acquire it.

Do not run bootstrap or migrations as an implicit daily release step. Schema changes
are forward-only separately approved operations with independent backup/compatibility
checks. Current application versions require exact contiguous schema versions;
rollback to an incompatible schema is refused rather than attempting downgrade.

## Application rollback

Use `pskitctl rollback --env <prod|staging> --config <private-file>
--component <api|web> --to <qualified-previous-release>` with explicit target pins.
API rollback recreates only backend with the same full overlays, preserving all
stateful services/volumes. Web rollback atomically restores previous index bytes while
retaining old and new hashed assets. Missing old assets are republished from the
verified immutable snapshot before the index switch; collisions/symlinks fail closed
and keep the interrupted recovery marker. Both run critical postflight checks.

Failed API release after candidate recreation attempts rollback automatically only
while hold ownership, zero activity and original config bytes remain verified; it
still exits nonzero. Pre-recreation errors never trigger a backend `up`. The controller
keeps the same owner's hold through startup, smoke, rollback and final verification.
Unknown ownership/activity, partial acquisition, rollback failure or uncertain
reopening retains the pending owner marker. If reopening may already have succeeded,
automatic rollback is forbidden; explicit recovery reacquires the same owner, verifies
activity, restores the previous image and smokes it before explicit release.
Failed recovery preserves private `pending.json`; new releases are refused. Explicit
previous-version rollback handles interruption before or after the current pointer
commit. Config changes after interruption require private review rather than an
unconditional overwrite. Pending records include original private config bytes and
must not be shared as public evidence.
Pending API records also bind owner nonce, adapter qualification, environment/project,
both Manifest hashes and phase. Changed private adapter identity or records without
this binding require a separate reviewed operator recovery; do not remove the marker
or invent a new owner to force reopening.

## Code rollback

These files are additive. Revert the Phase7 controller/tests/docs commit after the
main agent commits it, retaining any real host state/artifacts pending independent
review. Removing a CLI implementation does not imply rolling back a host application
or deleting its release registry. Existing infrastructure/install/migration scripts
remain available with their prior responsibilities.

## Pending exercises

No Staging/production operation or backup restore is claimed. The runbook contains
the isolation, failure injection, restore validation and measurement plan. Actual
RPO/RTO values, live deployment time and unaffected-container evidence must be recorded
only after a separately authorized real exercise. An application rollback is not a
backup restore and cannot recover lost database/storage/workspace/receiver state.
