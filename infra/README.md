# Infrastructure

The supported onboarding stack is the repository-root `compose.yaml`.

From the repository root:

```bash
cp .env.docker.example .env.docker
docker compose --env-file .env.docker up -d --build --wait
```

`infra/docker-compose.yml` is a compatibility include for older commands that
referenced this directory. It delegates to the root Compose definition.

The default stack intentionally contains only services used by the current
implementation:

- `web` (FastAPI plus the built Vue SPA);
- `worker` (the SQL-polling PSKit task worker, not Celery);
- `qdrant` (internal vector database).

Redis, MinIO, and PostgreSQL are not enabled by default because the current
worker and artifact code do not use them. Add production overlays only when the
corresponding application implementation is enabled.

See `DOCKER_QUICKSTART.md` for persistence, offline export, and model/GPU
boundaries.
