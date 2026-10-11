# MCP capacity CI follow-up (separate from PR-2 summaries)

## Observations and limits

The first full PR-2 backend suite failed test_mcp_capacity.py::test_two_python_instances_share_mcp_capacity_through_the_same_database: a second request returned 200 instead of 429 while the first call was active. The MCP adapter/store were unchanged by PR-2. Isolated runs of that original test passed 4/4 on eb8df70 and 4/4 on the PR-2 candidate. Therefore the exact scheduler/load cause of that single full-suite failure is not proven.

A separate controlled real executor saturation establishes an existing correctness defect: LimitedMcp queues its long-lived heartbeat via asyncio.to_thread into the general default executor but immediately launches the provider. If the executor is occupied, ownership expires before any renewal runs; a second instance can admit another provider while the first is still executing. The deterministic RED checks second_calls == 0 and observes 1. The original .12-second lease, .18 sleep and .05 queue boundary are retained; no existing test was deleted, skipped, weakened or given a longer timeout.

## Owned files and minimal fix

- apps/api/app/adapters/live/limited_mcp.py
- apps/api/tests/test_mcp_heartbeat.py (new)

The heartbeat gets one stdlib executor worker per admitted call. It still renews the same token on the same schedule and carries copied ContextVars. Cancellation shields and asynchronously drains ongoing renewal before the adapter releases capacity. Worker shutdown uses wait=False so a blocked renewal never creates a synchronous event-loop join. Dedicated workers finish after each call; 20 repeated calls verify no heartbeat worker remains. Normal default-executor use by other work is unchanged.

The domain capacity module remains byte-identical to eb8df70. No lease SQL, max_calls, configured TTL, queue deadline, release hold, durable ledger, API/SSE field, provider invocation protocol or migration changes were made.

## RED/GREEN evidence

- Baseline original regression: 4/4 passed.
- PR-2 candidate original regression: 4/4 passed.
- red-over-admission: 1 failed (a second provider actually ran).
- red-and-compatibility: 1 failed / 2 passed, characterizing ContextVars, caller cancellation, provider cancellation, lost lease and release-before-retry.
- red-blocking-shutdown: 1 failed / 3 passed. A preliminary dedicated executor with synchronous shutdown blocked the loop during cancelled renewal; the independent watchdog bounded the probe. The final patch drains asynchronously.
- green-cancellation: 28 passed, including PostgreSQL admission 3/3; no skips.
- green-canonical-formatted: 35 passed, including PostgreSQL admission 3/3; no skips. This is detached eb8df70 plus only the two owned files, using canonical npm-lock Pi dependencies.
- Ruff, compileall and owned-file git diff --check pass.

A first unbounded starvation probe was precisely terminated after exposing cleanup waiting behind the occupied default executor; its log is retained under red-default-executor/. A bounded diagnostic probe then failed with TimeoutError. The final red-over-admission probe asserts actual extra dispatch directly and always unblocks fixture workers in cleanup.

The first related run using root pnpm Pi dependencies produced 31 passes and 2 Pi-executable setup failures in unrelated release fixtures. These were re-run successfully using canonical locked dependencies. An initial canonical attempt stopped at import-format Ruff I001 in the new test; formatting was corrected before the successful run.

Only synthetic mock fixtures and the task-owned loopback PostgreSQL 17 service were used. No production, SSH, real database, paid model, staging or remote configuration was touched. This lane did not commit or push. Full exact-HEAD/backend and remote CI are the main agent's integration gates.

## Rollback

Revert the eventual standalone MCP heartbeat follow-up commit. No data/schema migration, TTL or persistent ledger rollback is required. The original executor starvation behavior returns with the old adapter; the PR-2 fixed summary patch remains independent.

Repeated final frozen candidate: the 5 new checks plus the original shared-capacity regression passed 6/6 in each of 3 independent isolated runs (18 passes, 0 skips total), under repeated-green/.

## Cancellation review follow-up

Independent review found a remaining cleanup defect in `LimitedMcp.invoke`: cancellation arriving after the provider returned could cancel the cleanup gather and skip both shared lease release and local semaphore return. A shared store release exception also skipped local return. The review's synthetic counterexample is retained in `../review-pr2-heartbeat/`; this follow-up owns only the same adapter and heartbeat test file.

Before changing production code, two deterministic tests failed in `cancellation-followup/red-cleanup.log`: repeated cancellation during a deliberately blocked renewal produced no shared release call, and a shared release exception left the local semaphore occupied so a public retry raised `McpCapacityExceeded`. Both tests passed after the minimal fix in `green-cleanup.log`.

Cleanup now shields and drains the existing provider/heartbeat gather, remembering cancellation until both tasks have exited and release has been attempted. Local capacity is returned in `finally` even if shared release fails. Cancellation still propagates after successful cleanup. The new cancellation test observes that provider and renewal have both exited before shared release, verifies the lease disappears, and retries through the public adapter. The shared failure test preserves the store exception and verifies a subsequent invocation can acquire local capacity. No ownership SQL, fencing, lease interval, queue deadline, provider dispatch or executor policy changed.

The six previously frozen regression cases are retained. Together with the two follow-up cases, all eight passed in each of three independent runs (`cancellation-followup/repeated-green-*.log`: 24 passes, 0 skips). The same focused admission/idempotency compatibility group initially passed 32 tests and failed two unrelated Pi executable setup checks in the workspace (`green-compatibility.log`), because its existing Pi dependency tree lacks `.bin/pi`. Re-running the same 34 tests in an isolated HEAD snapshot with the two owned files and the existing canonical lock dependencies passed all 34, with 0 skips (`green-canonical-compatibility.log`). No dependency or lock file was changed.

The frozen source snapshot uses HEAD `f16a09cecf9a077deec9787e943107bbf9dabe51` and only the two owned file replacements. All eight checks also passed in each of three independent isolated snapshot runs (`canonical-repeated-green-*.log`: 24 passes, 0 skips). Its source SHA and before/after snapshots are retained under `cancellation-followup/`. AST comparison confirms the other adapter methods are unchanged, the existing five heartbeat tests remain an exact file prefix, and the domain capacity module retains its previous SHA. Ruff, compilation of both owned files, and adapter diff whitespace checks passed. This follow-up ran no PostgreSQL or broad backend suite; exact candidate refresh and integrated gates remain with the main agent. No Git index, commit, remote, deployment or production configuration was touched.

## Final integrated local gate

Clean PR-0/1 source plus the committed PR-2 summary fix and only the two final MCP files passed `scripts/ci_new_stack.py backend --timeout 1800` on Node 22, Python 3.12 and the task-owned loopback PG17: **1328 passed / 1 pre-existing external Supabase integration skipped, PostgreSQL 232 passed / 0 skipped, 367.58s**. Ruff and compileall passed in the same isolated runner. Final stdout and JUnit: `/tmp/pskit-remediation-20261012/pr2-final-full/backend.log` and `backend.xml`. These results supersede the earlier 1326-case snapshot which preceded cancellation cleanup review. No new skip or timeout change was introduced. Remote exact commit/job results are indexed in the final phase report.
