# Phase 7 — Verification

Date: 2026-10-10. Environment: local WSL checkout, Python 3.12 project virtualenv.
Tests use synthetic credentials, injected Docker command responses, mocked HTTP and
pytest temporary directories. Controller tests start no Docker container, external
service, paid model or real GPU task. Accounting tests connect only to the existing
disposable local PostgreSQL 17 and create/drop their own random schemas with exact
production core/compute table definitions; no real database/auth/global role is changed.

## Commands and actual results

```bash
TEST_POSTGRES_DSN='<disposable-loopback-PostgreSQL-DSN>' \
new_backend/.venv/bin/python -m pytest \
  deploy/agent/tests/test_release_control.py \
  deploy/agent/tests/test_release_accounting_pg.py \
  deploy/agent/tests/test_release_manifest.py \
  deploy/agent/tests/test_production_config.py -q \
  --junitxml=/tmp/pskit-phase7-controller-final-accounting.xml
```

**173 passed in 5.57s, zero skips**: 82 controller cases, 19 real local PostgreSQL
accounting cases, and 72 existing Manifest/config cases. The DSN is provided only by
the synthetic test configuration; the fixture refuses non-loopback hosts. Earlier
pure preparation passed 112 tests; the current safety hardening supersedes that count.

```bash
new_backend/.venv/bin/ruff check \
  deploy/agent/scripts/release_control.py deploy/agent/scripts/release_files.py \
  deploy/agent/tests/test_release_control.py deploy/agent/tests/test_release_accounting_pg.py
new_backend/.venv/bin/python -m compileall -q \
  deploy/agent/scripts/release_control.py deploy/agent/scripts/release_files.py
bash -n deploy/agent/pskitctl
PSKITCTL_PYTHON="$PWD/new_backend/.venv/bin/python" deploy/agent/pskitctl --help
```

Ruff, compilation, Bash syntax and `git diff --check` passed. The thin entry's existing
help check printed the explicit CLI options.
The final command is help only; no host release operation was executed.

## Fault and boundary coverage

- API-only operation and explicit rollback target only `backend`, keep project and
  ordered overlays, and preserve unrelated private config/secret bytes.
- Five-file OpenSandbox and four-file Staging overlay combinations are retained.
- Startup error, readiness HTTP 502 and failing critical smoke return failure and
  restore the previous API image; a failed rollback retains a private recovery marker.
- Web smoke HTTP 502 and ordinary interruption restore previous index/current pins;
  an injected power-loss state after index/current-pointer commit recovers explicitly.
- Old hashed assets remain available after release/rollback; collisions, artifact
  fingerprint tampering, symlink files and symlink parent directories are rejected.
- Run/Job/Attempt active or unknown counters, omitted counters, stale/unknown/missing
  Receiver evidence, unsafe auth grants, incompatible schema, missing auth keys,
  stale Manifest fingerprint, unqualified checks and lost runtime overlays fail closed.
- Fresh adapter receipt ownership, qualification, project/environment, Manifest pins,
  exact scopes and durable hold mode are checked; expired/stale/revoked/mismatched,
  oversized/malformed/boolean receipts and invalid command configuration are refused.
- No backend recreation occurs for work introduced after image inspection. Missing
  or partially acknowledged acquisition persists the owner before enter and retains
  the marker; barrier loss or post-smoke new work stops rollback/reopening. A lost
  reopening acknowledgement cannot trigger rollback after possible new admissions.
  Explicit recovery re-enters the original nonce; changed qualification/owner fails.
- A deleted old hashed asset is republished before both crash and ordinary Web
  rollback. Differing bytes or symlinks prevent the index switch and preserve the marker.
- Real SQL tests reject terminal `reserved`, `pending_reconciliation`, empty and
  future/unknown GPU accounting, active zero-budget compute reservations, active/
  unknown model guards and orphan/negative guard budgets. Actual closed GPU states,
  inactive reservations, NULL legacy minute fields, known conservative terminal
  Token charges and zero closed budgets remain accepted.
- Actual running-source-mount drift is rejected; logs redact configured secret/DSN
  values; controller/state permissions are enforced.
- A cloud-file edit injected during admission verification is preserved; an immediate
  byte recheck before atomic pinning refuses the update, keeps the hold/recovery marker
  and performs no backend recreation. This race regression was observed red then green.
- Doctor/status/logs do not write operational files or restart services; configured
  proxy variables do not affect local HTTP probes.
- Executor/bootstrap/migration commands and release without explicit component are
  refused; no whole-stack down/build/provision/migration path exists.

Tests were observed red before implementation (missing controller), and additional
runtime-overlay/source-mount/log-redaction and post-pointer crash cases were observed
failing before their fixes. The two independent P1 reproductions and missing mandatory
admission check first yielded **4 failing cases**; additional receipt/owner/race cases
yielded **18 failures** before protocol integration. The ordinary rollback asset-loss
case was separately observed red. Real PostgreSQL accounting tests yielded
**12 failures / 4 passes** before query hardening (terminal reserved/unknown returned
zero and the reservation/guard counters were absent); the final suite above is green.
The red SQL JUnit evidence is `/tmp/pskit-phase7-accounting-red.xml`.

## Not verified

No real Docker Compose `up`, complete target-host PostgreSQL probe, existing host adoption, Receiver
activity adapter, independently installed server admission adapter, real Staging
Pi/auth/SSE/Artifact smoke, public Nginx routing, graceful admission/drain or RPO/RTO
exercise has run in this phase. The isolated SQL tests do not qualify real auth grants,
host roles/permissions or every database-probe table. Fake Docker tests verify
command boundaries and intended handling, not actual host Compose-label/runtime
compatibility or actual admission enforcement. A real qualification must use the
runbook before enabling the controller; absent the adapter, API mutation is blocked.

## 最终跨阶段部署整合

当前全部部署/CI runner测试由 canonical isolated runner重跑，**349 passed / 18.23s**；包含上面的19项真实本地PG accounting测试与最后一项cloud.env字节竞争回归。合成Compose九个组合通过，仅render未up。正式new_backend context构建的 `ff873b2` 镜像三项契约通过（4.83s），未发布。
