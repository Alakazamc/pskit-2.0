# Phase 7 — Change summary

Date: 2026-10-10. Scope: local preparation of a daily application release controller.

## Changes

- Added the thin `deploy/agent/pskitctl` CLI, `scripts/release_control.py`,
  and isolated atomic artifact/file helper `scripts/release_files.py`.
- Reused the existing strict, qualified `pskit.release.v1` Manifest and production
  literal-config validator. No alternative Manifest schema or daemon was introduced.
- Added read-only doctor/status/render/backend logs and explicit environment/component/
  immutable-version release/rollback. Bootstrap, migration and executor mutation are
  rejected as separate reviewed boundaries.
- API changes use the same project and full ordered overlays with backend-only
  `up -d --no-deps --wait`; failures return nonzero and attempt application rollback.
  Database state, volumes, Supabase, LiteLLM and long-lived execution services are not
  upgraded, stopped or downgraded by this path.
- Added automatic read-only central activity/schema/auth-grants probes; current image,
  actual Compose labels, backend environment, source mounts and networks must match.
  Missing or unknown counters fail closed. Production API changes also require fresh
  Receiver journal/outbox evidence; its real adapter remains pending.
- API mutation now requires an independently qualified server admission adapter.
  Private structured enter/verify/release commands must return fresh typed receipts
  matching a controller-generated owner nonce, project/environment, exact previous/
  target Manifest hashes, adapter qualification and all execution admission scopes.
  The durable hold cannot expire open, and missing integration blocks API release
  in both environments. The controller does not implement host enforcement itself.
- Persisted admission ownership precedes enter; partial acquisition, lost/revoked
  ownership, post-probe new activity and uncertain reopening retain a recovery marker.
  Candidate and rollback recreations require fresh hold and activity checks. The
  hold spans startup/smoke/rollback; explicit recovery uses the same owner.
- Recheck original cloud bytes immediately before atomic pin replacement, after
  potentially slow admission/database probes. Concurrent operator changes are preserved
  and require private review; they cannot be silently overwritten by the old snapshot.
- Hardened accounting gates: actual closed GPU states only, explicitly inactive
  CPU/GPU compute reservations, and owned/known closed model-call guards. Legitimate
  generic NULL legacy-minute fields and terminal conservative Token charges remain
  valid. Queries are checked on exact production table definitions in local PG.
- Web copies verified new assets before atomic index replacement, retains old files,
  rejects differing same-name resources and symlinks, and records a private pending
  transaction across the separate atomic index/current-pointer operations.
- Both interrupted and ordinary Web rollback republish missing verified immutable
  old assets before switching the index, and reject collisions/symlinks while retaining
  pending recovery state. They cannot clear the marker while referencing known missing
  old assets.
- Added deterministic fake Docker, mocked HTTP and temporary static/config tests.
- Added [operational setup, commands and exercise plan](../../../runbooks/RELEASE_CONTROL.md).

## Decisions and limits

Initial registration of current artifacts is a separately reviewed adoption action.
The controller will not assume historical mutable image tags are an immutable current
release. Existing staging manifests are not silently converted. Source binds and
optional OpenSandbox overlays are part of the complete fingerprint, not an optional
best-effort check. Existing unsafe auth grants are reported; this phase does not revoke
permissions or modify a real database.

Preflight snapshots are now followed by mandatory held-admission checks around API
mutation. The server enforcement adapter and its qualification remain pending; a
configured digest or fake typed receipt is not proof of runtime admission controls.
API recreation is not claimed to be zero downtime. Actual Receiver
evidence transport, Staging end-to-end exercise, adoption and measured RPO/RTO remain
pending. The runbook and tests are preparation, not production deployment evidence.

No production/SSH/real DB/credential reads, cloud release, backup restore, commit or
push was performed by this task. Existing Manifest, stack.sh, validators and deployment
manuals were not changed. The local PostgreSQL tests use only owned disposable schemas
and synthetic rows; they modify no auth/global roles or real database. Runtime benchmark
ownership remains with the main agent.
