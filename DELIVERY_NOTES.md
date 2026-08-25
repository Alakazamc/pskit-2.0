# PSKit 2.0 v0.2.0 Delivery Notes / 交付说明

## Delivery scope / 交付范围

This release contains a reproducible Linux AMD64 Docker stack: FastAPI/Vue web
application, background worker, one-shot database migration, Qdrant, persistent
volumes, sanitized source, locked dependencies, CI, and smoke-test scripts.

本版本包含可复现的 Linux AMD64 Docker 交付栈：FastAPI/Vue Web 应用、后台
worker、一次性数据库迁移、Qdrant、持久化卷、已脱敏源码、依赖锁、CI 与冒烟
测试脚本。

## Acceptance baseline / 验收基线

- The image builds from lock files and runs as non-root UID 10001.
- Fresh install and 0.1.0 SQLite upgrade both reach migration `0002`.
- Web, worker, and Qdrant report healthy before Compose returns success.
- Registration, current-user lookup, protected task listing, logout, rejected
  unauthenticated access, login, and session restoration pass end to end.
- Backend lint, formatting, strict type check, and tests pass.
- Frontend lint, tests, production build, and production dependency audit pass.

## Deliberately not included / 不包含内容

API keys, accounts, user databases, artifacts, private deployment addresses,
model weights, licensed AlphaFold 3 assets, and the legacy PSKit runtime are not
included. Heavy prediction tools remain unavailable until an operator installs
and configures those external assets.

## Start and verify / 启动与验证

Follow `DOCKER_QUICKSTART.md`. For an offline handoff, verify both SHA-256 files,
load the image bundle, copy `.env.docker.example` to `.env.docker`, and start
with `--no-build --pull never --wait`. Then run:

```bash
./scripts/docker_smoke.sh http://127.0.0.1:10716
```

The expected final lines are `health=ok`, `spa=ok`, `asset=ok`, and `auth=ok`.

## Security defaults / 安全默认值

The port binds to loopback, public registration closes after the first admin,
sessions use HTTP-only cookies, login attempts are rate-limited, artifact access
is owner-checked, task inputs are bounded, and production CORS is disabled.
Use an HTTPS reverse proxy and set `COOKIE_SECURE=true` before internet exposure.
