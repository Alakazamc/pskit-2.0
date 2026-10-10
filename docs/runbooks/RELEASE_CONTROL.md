# PSKit application release controller

Status: **local controller, durable admission and receiver adapters implemented;
target-host qualification/adoption remains pending; no host release executed** (2026-10-10).
The controller is `deploy/agent/pskitctl`, backed by
`deploy/agent/scripts/release_control.py`. It reuses the existing
`pskit.release.v1` Manifest and literal production configuration validator.

## Command boundaries

| Command | Effect |
| --- | --- |
| `doctor` | Read private configuration; render current Agent Compose; verify current image, full overlays, runtime mounts/network/env, exact DB schema, activity and authentication grants; probe live/ready. Writes no operational files. |
| `status` | Show secret-free registered API/Web versions, current backend count and interrupted transaction flag. |
| `render` | Show project, full ordered overlays and SHA256 of canonical resolved JSON. Does not print the resolved JSON containing secrets. |
| `logs --service backend` | Read the last 100 backend log lines; redact configured key/password/token/secret/DSN values. |
| `release --component api` | Require a held, qualified admission barrier, recheck activity, update one backend with `up -d --no-deps --wait --wait-timeout 60 backend`, then verify probes, smoke and hold ownership before reopening. |
| `release --component web` | Copy new immutable assets first, then atomically replace `index.html`; retain every old asset; verify probes/smoke. No container update. |
| `rollback --component api\|web` | Apply the explicitly selected qualified previous Manifest, with the same gates. No database downgrade. Also recover an interrupted transaction to its recorded previous version. |
| `bootstrap`, `migrate`, `--component executor` | Exit nonzero. Use the separately reviewed infrastructure, migration or executor procedure. |

The controller has no `down`, `stop`, `build`, `pull`, schema migration, provisioning,
volume deletion or whole-stack restart path. It does not run `stack.sh up/down`.
Supabase/LiteLLM and executor lifecycle remain separate. One backend replica is
recreated during an API update; this is not a zero-downtime guarantee.

## Prerequisites and current enablement gaps

The CLI requires Python 3.12 and the existing `jsonschema` dependency. Use a known
operator environment explicitly when the host's `python3` lacks project dependencies:

```bash
PSKITCTL_PYTHON=/absolute/approved/venv/bin/python \
  /absolute/checkout/deploy/agent/pskitctl --help
```

Before real use, a reviewed installation/adoption operation must:

1. Place existing and candidate **qualified immutable** artifacts into a local
   release registry. Daily release neither downloads nor builds artifacts.
2. Generate the single strict Manifest from the **target host's exact full rendered
   Agent Compose** and qualification evidence. Existing historical staging manifests
   are a different format and are not accepted as this Manifest.
3. Register the current API and Web artifacts after verifying their actual digest,
   static bytes, overlays, mounts and target schema. The controller does not silently
   adopt mutable historical image tags or assume that the newest Git commit is live.
4. Install a private controller configuration and state directory, supply the
   approved synthetic Pi/SSE/Artifact/auth smoke command, and validate its behavior.
5. Enable and independently qualify the receiver's optional private `/activity`
   listener, with a distinct bearer token, actual retained journals and spool. Local
   implementation is available; no A6000 listener was enabled by this task.
6. Independently qualify and install a **server-enforced admission adapter** covering
   user/API submissions and scheduler claims. Its durable hold must survive backend
   recreation and controller failure, and remain closed until the same owner's explicit
   release. The local implementation is `pskit-admission-adapter`; host installation,
   deployed configuration and qualification are still pending. A missing
   adapter blocks every API release/rollback in both environments; Web and read-only
   operations do not acquire it.

No real configuration, credentials, database or deployment state was read or modified
while implementing this controller. Do not paste secrets into commands or manifests.

## Private operational configuration

The configuration JSON and `current.json` must be regular files with mode `0600`;
state directory mode `0700`. All directory components and file opens reject symlinks.
Use absolute paths. The configuration is trusted operator input, including the smoke
command, and must not be supplied by a browser or an Agent.

Example shape (placeholders are intentionally non-operational):

```json
{
  "schema_version": "pskit.control.v1",
  "environment": "prod",
  "repository": "/absolute/checkout",
  "cloud_env_file": "/absolute/private/cloud.env",
  "backend_env_file": "/absolute/private/backend.env",
  "release_directory": "/absolute/releases",
  "state_directory": "/absolute/private/control-state",
  "static_directory": "/absolute/nginx-dist",
  "backend_url": "http://127.0.0.1:18088",
  "public_url": "https://agent.bioailab.net",
  "critical_smoke_command": ["/absolute/approved/synthetic-release-smoke"],
  "receiver_activity_urls": ["http://10.9.8.2:18188/activity"],
  "receiver_activity_auth": {
    "http://10.9.8.2:18188/activity": {
      "token_file": "/absolute/private/receiver-activity.token"
    }
  },
  "receiver_activity_required": true,
  "admission_barrier": {
    "adapter_id": "qualified-host-admission",
    "qualification_sha256": "<64 lowercase hex characters of independently reviewed evidence>",
    "enter_command": ["/absolute/checkout/deploy/agent/pskit-admission-adapter", "enter", "--config", "/absolute/private/admission.json"],
    "verify_command": ["/absolute/checkout/deploy/agent/pskit-admission-adapter", "verify", "--config", "/absolute/private/admission.json"],
    "release_command": ["/absolute/checkout/deploy/agent/pskit-admission-adapter", "release", "--config", "/absolute/private/admission.json"]
  }
}
```

Staging uses `environment: staging`, project `pskit-agent-staging`, loopback backend
port `18090`, its private site URL and independently qualified artifacts. The Stage
model/AF3 substitutes and secrets must already be provisioned. A Staging API release
can explicitly set `receiver_activity_required: false` when all execution is isolated
and synthetic. Production API release refuses this setting. Web-only release never
restarts the receiver and still validates central activity/schema/permissions.

`critical_smoke_command` is an argv array, never passed through a shell. It must be a
trusted, bounded script that returns nonzero if authentication, controlled Pi mock,
SSE replay or Artifact behavior fails. The controller additionally requires backend
`/health/live` and `/health/ready` to return 200, public `/login` 200, unauthenticated
`/api/v1/me/usage` 401, `/api/v1/internal/release-probe` 404, and production public
`/admin` 403. The last probe must address the public ingress, not a private admin
route. No real GPU inference or paid provider request belongs in the default smoke.
An operator smoke's correctness is separately reviewed; exit zero alone does not
prove that a newly written script exercises these flows.
Under the admission hold, use previously seeded synthetic terminal Runs and files
for authenticated reads, SSE replay and Artifact download. Ordinary new Run creation
must remain denied; do not add an administrator bypass to make the smoke pass.

The controller inherits only the small existing Docker operator environment allowlist;
Compose interpolation comes from the designated literal cloud file. Private backend
configuration is never printed. An API pin update replaces only the
`AGENT_BACKEND_IMAGE` line; all other bytes and credential values remain unchanged.
A private pending record holds original cloud bytes and admission owner identity for crash recovery and therefore
must be handled as a secret-bearing file. Do not publish it as release evidence.
The controller rechecks those bytes immediately before pin replacement, after the
potentially slow admission/activity checks. Operator edits detected during that interval
are preserved and retain the recovery marker. Operational edits must respect the same
release lock/maintenance procedure; these checks cannot serialize an unrelated root
process that deliberately writes during the atomic replacement itself.

## Artifact registry and current registration

```text
releases/
  previous-release/
    manifest.json          # strict pskit.release.v1, fixed source/image/dist/schema
    dist/                  # complete immutable frontend tree with index.html
  candidate-release/
    manifest.json
    dist/
private/control-state/
  current.json             # independently records live API and Web Manifest hashes
  release.lock             # one local controller mutation at a time
  pending.json             # present only during an update or failed recovery
```

The complete resolved Compose fingerprint is:

```python
sha256(json.dumps(model, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode())
```

Use the existing `release_manifest.dist_hash` for the complete frontend tree. It hashes
path length + UTF-8 relative path + fixed-width content SHA256 for every sorted file.
The controller snapshots bytes using `openat/O_NOFOLLOW`, computes this same format,
and publishes those verified bytes. Neither older OpenSandbox hashes nor a hash of
only `index.html` can substitute for this tree hash.

`current.json` contains no credentials:

```json
{
  "schema_version": "pskit.control.v1",
  "api": {
    "release_id": "previous-release",
    "manifest_sha256": "<64 lowercase hex characters for exact manifest.json bytes>"
  },
  "web": {
    "release_id": "previous-release",
    "manifest_sha256": "<64 lowercase hex characters for exact manifest.json bytes>"
  }
}
```

A Web change updates only `web`; an API change updates only `api`. Registered Manifest
bytes cannot change. The registry and current registration are not a substitute for
an audit of the actual running artifacts. Initial adoption is pending an explicitly
approved operation; it is intentionally not performed by this task.

## Read-only checks

```bash
/absolute/checkout/deploy/agent/pskitctl doctor \
  --env staging --config /absolute/private/control.json
/absolute/checkout/deploy/agent/pskitctl status \
  --env staging --config /absolute/private/control.json
/absolute/checkout/deploy/agent/pskitctl render \
  --env staging --config /absolute/private/control.json
/absolute/checkout/deploy/agent/pskitctl logs --service backend \
  --env staging --config /absolute/private/control.json
```

`doctor` reports missing receiver evidence as `unverified`/`configured`, not as a clean
journal. `release api` actually probes the configured endpoints and fails closed.
`status` reports registration and count, not a full infrastructure health assertion.
Log redaction removes known configured secret values; arbitrary application logs
still require responsible handling and should not be published wholesale.

## Activity, schema and configuration gates

Before application mutation the controller queries the current backend's restricted
PostgreSQL connection with `default_transaction_read_only=on`, a five-second query
limit and an explicit private schema. It requires:

- All Run, Compute Job and Workspace Attempt states known and terminal; incomplete,
  `unknown`, NULL and unrecognized states fail closed.
- No Run lease, active Job lease, unresolved workspace lease, unknown/changing/error
  workspace lifecycle, device lease, pending reconciliation or unconsumed compute
  outbox entry. Historical terminal Job fencing tokens are not counted as live leases.
- GPU accounting must be one of the actual closed stored states: `settled`,
  `reconciled`, or `released`. Terminal work still marked `reserved`, and any NULL,
  omitted or future/unknown accounting state, blocks release. Generic compute jobs
  share `agent_jobs`; their usage is recorded in milliseconds, so legitimate NULL
  legacy `actual_minutes` is not blanket-rejected.
- Every `compute_reservations` row must be explicitly inactive, even when its CPU/GPU
  budget is zero. Model-call guards must be owned by an existing Run, have a valid
  nonnegative reserved budget and a known closed state (`reported`, `unreported`,
  `rejected`). `active`/unknown guards block release. A terminal `unreported` guard
  is the existing conservative charge established by `settle_tokens`, not evidence
  of an executing call. Historical Token reservation ledger entries and already
  charged usage are not mistaken for pending holds. No ledger entry is changed here.
- The exact contiguous schema versions required by the Manifest. Current code rejects
  newer schemas as well as missing versions, so app rollback does not assume forward
  compatibility. No `GRANT`, `REVOKE`, DDL or data update is executed by this probe.
- No app/LiteLLM table or column access on **any** Supabase `auth` table; absence of the expected
  `auth.users` boundary or missing roles/permissions also prevents release.

The current and candidate project and ordered overlays must match. Both resolved
fingerprints must match their immutable Manifests. The normalized configurations can
only differ in backend-image references; source binds, environments, networks and
volumes must remain identical. The running backend's image, Compose config-file
labels, environment, mounts and networks are verified against that model. An API
update never includes a receiver, proxy, database, gateway or OpenSandbox service.
A changed LiteLLM/provider compatibility/OpenSandbox pin requires separate qualification.

Receiver evidence JSON is bounded to 64 KiB, returned by an authenticated/private
read-only adapter and automatically generated from real durable journal/outbox state:

```json
{
  "schema_version": "pskit.receiver-activity.v1",
  "observed_at": 1790000000.0,
  "counts": {"active": 0, "unknown": 0, "journal_pending": 0, "outbox_pending": 0}
}
```

It must be HTTP 200, at most 30 seconds old, and explicitly report all four zero
integer counters. A missing endpoint, stale timestamp or omitted counter never means
zero. Transport/authentication and trusted observation of the receiver remain a
separate integration gate; do not hand-write an all-zero response for a real release.

The optional listener lives in the existing unified receiver process. Its three
server-selected journal files (`coral.sqlite3`, `af3.sqlite3`, `af3-product.sqlite3`)
and AF3 spool are observed read-only, including uncheckpointed WAL. Missing, corrupt,
unrecognized, changed or unobservable state reports `unknown` rather than zero.
Retained outputs remain pending until the existing delivery/ACK path removes them.
No payload, sequence, model key or file content is exposed. It also tracks live lane
polls so unready, active or failed polls cannot produce clean evidence.

`PSKIT_RECEIVER_ACTIVITY_ENABLED=false` remains the default. After a separate host
review, bind to loopback or the literal private WireGuard address, set the distinct
32–256 character `PSKIT_RECEIVER_ACTIVITY_TOKEN`, and choose an available private port.
No Compose public port is added. Restrict host ingress to the authorized controller.
Controller `receiver_activity_auth` binds each exact configured URL to an owned
regular `0600` token file; the file's parent is `0700` and paths reject symlinks.
The HTTP client ignores ambient proxies and redirects. Tokens never enter argv,
Manifest, receipts or logs.

The activity probe alone is a snapshot. API mutation additionally requires the held
admission protocol below, fresh central/receiver checks, and a second ownership check
immediately before each candidate or rollback backend recreation. The controller
serializes operator releases on one host; actual user and scheduler exclusion is the
separately qualified adapter's responsibility. Do not promise graceful SSE draining
or zero downtime from the protocol's fake tests.

## Mandatory admission adapter protocol (local implementation; host adoption pending)

`admission_barrier` is an optional private configuration member to preserve existing
read-only/Web setup; omission or `null` makes **API mutation unavailable**. A boolean
"maintenance enabled" flag or a manually written all-zero snapshot is not accepted.
`doctor`/`status` report `configured` or `unavailable`; `configured` never means that
the actual hold is active or the host adapter is qualified.

The three configured commands are trusted absolute argv arrays. The controller uses
no shell expansion, inherits only `PATH`/`HOME`, passes one JSON request on stdin,
and accepts one strict JSON receipt on stdout (maximum 64 KiB). Commands must keep
credentials in their own private configuration, never in argv, receipts or evidence.
Requests have this shape, with real nonce and Manifest hashes generated/read by the
controller:

```json
{
  "schema_version": "pskit.admission-command.v1",
  "operation": "enter",
  "adapter_id": "qualified-host-admission",
  "qualification_sha256": "<reviewed-evidence-sha256>",
  "owner_nonce": "<controller-generated 256-bit lowercase hex nonce>",
  "environment": "prod",
  "project": "pskit-agent-cloud",
  "previous_manifest_sha256": "<previous registered Manifest sha256>",
  "target_manifest_sha256": "<candidate Manifest sha256>",
  "scopes": ["agent_runs", "compute_jobs", "workspace_attempts", "scheduler_claims"]
}
```

Receipts echo **all identity, Manifest and scope fields exactly**, replace
`schema_version` with `pskit.admission-receipt.v1`, omit `operation`, and add:

```json
{
  "state": "closed",
  "hold_mode": "until_explicit_release",
  "observed_at": 1790000000.0,
  "valid_until": 1790000020.0
}
```

`enter` and `verify` require `state: closed`; `release` requires `state: released`.
Observations must be no older than 30 seconds; `valid_until` must still be in the
future and at most 30 seconds after observation. **This expiry invalidates the receipt,
never reopens admission.** A TTL that opens access automatically is forbidden because
Compose waits and smoke can take longer, or the controller can be killed.
An unknown/revoked owner, wrong scope, stale/expired receipt, mismatched environment,
project, qualification or Manifest identity makes the operation fail closed.

The independently reviewed adapter must:

- Enforce the hold on the server for every admission path, including Run creation,
  direct/Agent Compute Job submission, Workspace Attempt creation, and background
  queued/resume/workspace/receiver claims. Denial must happen before quota reservation
  or execution side effects. Existing acknowledged operations may finish and reconcile.
- Store ownership and gate state outside the backend container being recreated;
  process exit, receipt expiry and backend restart cannot reopen it.
- Atomically acquire/verify/release by exact owner nonce and bound project/environment/
  Manifest identity. Same-owner `enter` must be idempotent, and may reacquire after a
  lost release acknowledgement; it must never steal a different owner's active hold.
- Acknowledge `closed` only after every enforcement point has observed the hold, and
  acknowledge `released` only after verified same-owner reopening. Return failure for
  unavailable, partial or unconfirmed state; never invent constant success receipts.
- Qualify authenticated synthetic smoke under the hold without reopening general
  admission, including concurrency, backend restart, revoked ownership, command
  failure and controller kill cases. Prove that new user work cannot enter between
  the activity check and backend recreation. Bind the reviewed report to the deployed
  adapter implementation/configuration and the configured qualification digest.

No Nginx adapter or new service is installed by this phase. The configured qualification digest identifies externally reviewed evidence;
its presence and a well-formed receipt are not independent proof of enforcement.

### Local adapter implementation and adoption boundaries

`new_backend/app/domain/release_admission.py` stores one durable deployment binding
and hold owner in the existing private PostgreSQL schema. Component version 1 is
installed only by the explicit migration entrypoints; application startup never runs
DDL. Core schema version 13 and the ordinary `migrate_postgres` table set are
unchanged. Install the component separately through `migrate_admission_postgres`
only after reviewing its DDL, database grants and host qualification. Do not run
either procedure against an existing host as part of a daily release. The legacy
SQLite import/export path refuses any installed admission state: preserve the
component with a complete PostgreSQL backup instead of omitting its owner/nonce.
The legacy migration's final activation holds migration locks for both staging and
target through its checks and rename. Failure cleanup holds the staging migration
lock and retains the schema if any admission component table appeared. Investigate
that retained schema using the recorded owner; never delete it merely to retry.

The backend remains compatible with old deployments because enforcement defaults
to disabled. Enabling it requires live PostgreSQL, persistent Pi runtime, an installed
component and a 32-character-or-longer private key. Legacy sandbox lifecycle paths
without durable workspace records cannot be used with enforcement. Startup binds an
unbound open component once, preserves an existing closed hold and rejects a different
deployment/qualification binding.

Configure these backend settings in its private environment after qualification:

```ini
RESEARCH_AGENT_RELEASE_ADMISSION_ENABLED=true
RESEARCH_AGENT_RELEASE_ADMISSION_ENVIRONMENT=staging
RESEARCH_AGENT_RELEASE_ADMISSION_PROJECT=pskit-agent-staging
RESEARCH_AGENT_RELEASE_ADMISSION_KEY=<distinct private proof key>
RESEARCH_AGENT_RELEASE_ADMISSION_QUALIFICATION_SHA256=<reviewed evidence digest>
```

Use `prod` / `pskit-agent-cloud` for production. The key stays in private files; it is
not a user, model or receiver credential. The private adapter config has this exact shape:

```json
{
  "schema_version": "pskit.admission-adapter.v1",
  "database_url": "<private PostgreSQL DSN>",
  "database_schema": "pskit",
  "backend_url": "http://127.0.0.1:18090",
  "backend_token": "<same backend proof key>",
  "adapter_id": "qualified-host-admission",
  "qualification_sha256": "<reviewed evidence digest>",
  "environment": "staging",
  "project": "pskit-agent-staging"
}
```

Keep its parent directory `0700` and file `0600`, owned by the operator. Use an
explicit loopback IP and port. `backend_url` addresses the app directly (no `/api/v1`
prefix); the application observation route is `/internal/release-admission`.
The thin entry defaults to the checkout's `new_backend/.venv/bin/python`. On a host
using another approved environment, configure the command arrays with that absolute
interpreter followed by the absolute `admission_adapter.py` path. The controller
does not forward arbitrary interpreter environment variables and never installs a
virtual environment as part of a release.
The adapter verifies a fresh challenge against the actual running backend before
and after each operation and compares the response with the database deployment ID,
state, generation and owner. A missing/disabled/older backend, incorrect credential or
different database cannot emit a successful receipt. If post-close verification fails,
the durable hold remains closed and the controller retains its recovery marker.

Every new admission and claim checks the gate while holding the existing transaction
advisory lock until quota/record/lease commit. Run/title/compute/workspace dispatch,
direct MCP calls and PDF execution are covered. Completion, cancellation, delivery
ACK and reconciliation remain available. Receipt expiry and process restarts never
release the hold. Calls rejected before provider dispatch release their idempotency
claim; uncertain dispatched calls remain unknown.

API current, candidate and rollback Manifests must include identical independently
qualified admission metadata:

```json
{
  "admission": {
    "component_version": 1,
    "enforcement_version": "pskit.db-admission.v1",
    "qualification_sha256": "<same reviewed evidence digest>"
  }
}
```

This field is optional for old Manifest validation and Web-only compatibility, but
API mutation refuses its absence or mismatch. The backend private environment and
resolved Compose must also enable the same binding. An old gate-unaware image is
not a safe API rollback target. Initial host adoption needs a separate maintenance
operation; do not disable the flag, delete/reset the component row or downgrade the
database to bypass a pending hold. Preserve unknown accounting and all journals.

Local tests exercise actual PostgreSQL, the app's private HTTP handler and CLI
processes, including interruption before acknowledgement. They do not substitute
for a qualified host Compose recreation, actual WireGuard routing or RPO/RTO drill.

`pending.json` is fsynced **before** `enter`, including nonce, adapter/qualification,
project/environment, bound Manifest hashes and admission phase. A command might close
admission before failing or before returning its receipt; that case retains the marker
and never silently invokes release. The hold remains across config mutation, candidate
startup, postflight, automatic rollback and final verification. Activity and ownership
are checked again after startup/smoke and before reopening.

If ownership/activity becomes unknown, or rollback fails, the marker is retained and
API recreation/reopening stops. If reopening acknowledgement is invalid or lost,
the gate may already have opened: the controller records `releasing`, performs **no
automatic rollback**, and requires explicit recovery. Recovery validates the persisted
identity against current private configuration and immutable artifacts, re-enters
the **same owner** idempotently, drains/rechecks activity, restores the previous image,
smokes it, verifies again, and only then releases. It never creates a new owner to
override an uncertain hold. Legacy API markers lacking this identity need a separately
reviewed operator procedure; they cannot be marked safe or automatically reopened.

## Release and rollback

After independent artifact transport, qualification, permission review and the
required operational authorization:

```bash
/absolute/checkout/deploy/agent/pskitctl release --component api \
  --env staging --config /absolute/private/control.json --version candidate-release
/absolute/checkout/deploy/agent/pskitctl release --component web \
  --env staging --config /absolute/private/control.json --version candidate-release
/absolute/checkout/deploy/agent/pskitctl rollback --component api \
  --env staging --config /absolute/private/control.json --to previous-release
/absolute/checkout/deploy/agent/pskitctl rollback --component web \
  --env staging --config /absolute/private/control.json --to previous-release
```

There is no implicit default component, environment or "latest" version. API failure
after candidate recreation tries the previous image with the same full overlays only
while fresh ownership and zero activity remain verified, and runs critical probes/smoke
again. A pre-recreation failure makes no backend `up` call. The original release still
exits nonzero when rollback succeeds. If rollback
also fails, `pending.json` is retained and further releases are refused. Inspect private
state/logs and perform the explicit previous-version rollback.

The real adapter requires a running backend's private enforcement proof for enter,
verify and release. If the candidate cannot start at all, that proof is unavailable:
automatic and explicit controller rollback preserve the closed hold and pending
marker, and perform no unverified backend recreation. Recover qualified backend
observability through a separately reviewed host recovery procedure before resuming
the recorded owner's workflow. Successful fake startup-failure rollback tests do not
promise recovery from every actual container startup failure.

For Web, every non-index asset is published without replacing an existing filename.
Identical bytes are accepted; a collision with different bytes is rejected. Rename
changing public resources to content-hashed paths rather than overwriting an asset
that an already-open old page may reference. Directories, file opens and copies reject
symlinks/special files, and the controller copies from a verified in-memory snapshot
(maximum complete artifact size 512 MiB). It first verifies all current static files
needed by the rollback artifact. Files are fsynced and atomically linked/replaced in
pinned directories. Old assets are not deleted during release or rollback.

`index.html` is the atomic serving switch; `current.json` is the separately atomic
private version pointer. There is no filesystem-wide two-file transaction. A private
pending marker spans both commits: ordinary interruption restores the previous index
and registration; simulated power loss after either commit requires explicit rollback
and restores the previous registration. Unknown interruptions must not be marked
successful manually. A changed private config after a crash requires private review
rather than overwriting unrelated changes.

Interrupted and ordinary Web rollback first republish every missing immutable old
asset from the verified registry snapshot. A different same-name asset causes failure
before restoring the old index and retains the pending marker. Recovery never clears
the marker while leaving an old index that points to a known missing/colliding asset.

## Staging exercise and backup/RPO/RTO plan (not executed)

Run this only with a later explicit Staging exercise authorization:

1. Verify isolation of Staging logical databases, credentials, volume names, ingress,
   OpenSandbox policies and mock model/AF3 services. Record the current immutable
   API/Web manifests and pre-exercise DB/volume fingerprints.
2. Establish an admission barrier; measure drain time and collect fresh central and
   receiver activity evidence. Create only synthetic accounts, sessions, files,
   tool results and usage entries in Staging.
3. Exercise API-only and Web-only updates; record unaffected DB, LiteLLM, Receiver,
   OpenSandbox and proxy container IDs/start times. Check real authenticated SSE,
   cancellation, upload, download, replay and artifacts using the qualified mock path.
4. Inject one readiness/critical-smoke failure; measure detection and application
   rollback time. Reopen an old browser page and ensure its retained hashed assets
   load. Exercise interrupted index/current registration recovery separately.
5. In **dedicated disposable restore infrastructure**, restore approved PostgreSQL
   backups plus WAL according to the backup policy, Supabase Storage objects and
   persistent workspace/receiver spool/journal/outbox backups. Never restore into the
   live Staging/production database as part of this exercise.
6. Verify transaction boundaries: restored jobs cannot double execute; uncertain
   attempts remain unknown until trusted reconciliation; outbox replay is idempotent;
   resource usage settlement and artifacts match synthetic fixtures.
7. Measure observed RPO as the latest acknowledged synthetic operation minus the
   latest recoverable operation; measure RTO from declared outage to complete
   application readiness plus synthetic end-to-end success. Include backup age,
   WAL/archive delay, object/volume backup consistency and receiver reconciliation.
8. Set numerical RPO/RTO targets only after agreeing the backup cadence, storage
   capacity and operational responsibilities. Record targets and actual measurements
   separately. Neither has been measured in this remediation task.

Application rollback is not disaster recovery and does not recover corrupted/lost
state. Executor upgrades remain governed by their separate journal/spool/unknown
Attempt checks. No actual cloud exercise, infrastructure decision or backup restore
is claimed here.
