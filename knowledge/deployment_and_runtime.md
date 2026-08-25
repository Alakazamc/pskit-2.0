# Deployment and Runtime / 部署与运行时

## Supported stack / 支持的部署栈

The reproducible deployment is Docker Compose. It runs a FastAPI/Vue web
service, a SQL-polling background worker, a one-shot Alembic migration service,
and Qdrant. SQLite and artifacts share the `pskit_data` volume; Qdrant uses the
`qdrant_data` volume.

## Startup / 启动

Copy `.env.docker.example` to `.env.docker`, set provider keys only when needed,
then run `docker compose --env-file .env.docker up -d --wait`. The migration must
finish and Qdrant must become healthy before the web service starts. The default
host address is `127.0.0.1:10716`.

## Persistence and upgrades / 持久化与升级

Normal `docker compose down` preserves both named volumes. Run
`scripts/backup_runtime.sh` before an upgrade. Never use `down --volumes` unless
a permanent reset is intended. Version 0.3.0 migrates the production schema and
stores verified SQLite, artifact, and Qdrant snapshots outside the root disk.

## External providers / 外部服务

Agent chat needs an OpenAI-compatible chat provider. Vector RAG needs an
embedding provider and Qdrant; without embeddings it falls back to keyword
retrieval. RCSB, UniProt, and RNAcentral use public APIs. SerpAPI and the remote
RNA expert are optional and require explicit configuration.

## Heavy tools / 重型工具

The core image intentionally excludes model weights, licensed AlphaFold 3 data,
and the legacy PSKit runtime. Install them in a derived image or mount them
read-only, then configure the `PSKIT_*` paths. GPU is not required for the core
web, authentication, database, RAG fallback, and lightweight structure tools.

## Network security / 网络安全

Keep the default loopback binding unless remote access is required. For LAN or
internet access, use a firewall and HTTPS reverse proxy and set
`COOKIE_SECURE=true`. Do not publish Qdrant or mount the Docker socket into the
web container.
