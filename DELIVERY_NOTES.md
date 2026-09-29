# PSKit 2.0 v0.3.0 Delivery Notes / 交付说明

## Delivery scope / 交付范围

This release contains the unified FastAPI/Vue production application, durable
Agent turns, background and science workers, the research harness, atomic
Qdrant indexing, database migrations, verified backups, health monitoring,
locked dependencies, CI gates, and offline-delivery scripts.

本版本统一交付 FastAPI/Vue 生产应用、可恢复 Agent 轮次、普通与科学 Worker、
科研运行框架、Qdrant 原子索引、数据库迁移、校验备份、健康监控、依赖锁、CI
门禁和离线交付脚本。

## Acceptance baseline / 验收基线

- Images build from lock files and run as non-root UID 10001.
- Fresh databases and supported legacy SQLite databases reach migration `0003`.
- Web, worker, Qdrant, and configured science dependencies pass readiness.
- Registration, login, session restoration, Agent SSE, tasks, artifact downloads,
  and task retry contracts pass end to end.
- Backend tests/lint/types/dependency audit and frontend build/lint/tests/audit pass.
- Backup output contains SQLite, artifacts, Qdrant snapshot, runtime config, and
  SHA-256 verification data.

## Deliberately not included / 不包含内容

The repository and offline image bundle exclude API keys, accounts, user data,
private deployment addresses, model weights, licensed AlphaFold3 assets, and
the separately managed CORAL/PepCCD services. Operators configure those assets
through server-only environment files and read-only mounts.

## Start and verify / 启动与验证

Follow `DOCKER_QUICKSTART.md`. For an offline handoff, verify both SHA-256 files,
load the bundle, create `.env.docker`, and start with:

```bash
docker compose --env-file .env.docker up -d --no-build --pull never --wait
./scripts/docker_smoke.sh http://127.0.0.1:10716
```

The expected final lines are `health=ok`, `spa=ok`, `asset=ok`, and `auth=ok`.

## Security and operations / 安全与运维

The default port binds to loopback. Public registration is enabled for this
delivery and protected by proxy-aware, database-backed request throttling.
Sessions are bounded and HTTP-only; artifact ownership, task quotas, GPU
allowlists, security headers, log rotation, resource limits, backup retention,
and readiness checks are enforced. Use HTTPS and `COOKIE_SECURE=true` for
internet exposure.

On hosts where the deployment user does not have systemd linger enabled, user
timers stop after the final SSH session exits. Install the equivalent persistent
user cron schedule instead:

```bash
./scripts/install_user_cron.sh
./scripts/run_scheduled_job.sh health
```

The installer preserves unrelated crontab entries, prevents overlapping runs,
and schedules readiness checks every five minutes plus a verified backup at
03:20 Asia/Shanghai each day. Logs are kept under `logs/` with bounded size.
