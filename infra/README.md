# Infrastructure

Recommended services:

```text
postgres
redis
qdrant
minio
fastapi
celery-worker
caddy or nginx
```

For local development, use Docker Compose.

For A6000, dependencies can run either through Docker Compose or system services. FastAPI and Celery can run under systemd.

