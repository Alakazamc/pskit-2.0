# A6000 Deployment Design

## Deployment Goal

PSKit 2.0 should run directly on A6000 and should not require the developer laptop to stay online.

Target URL:

```text
http://172.31.199.38:<port>
```

Optional production URL can be added later through Caddy/Nginx and HTTPS.

## Services

Run these services on A6000:

- FastAPI app.
- Celery worker.
- PostgreSQL.
- Redis.
- Qdrant.
- MinIO.
- Caddy or Nginx.

## Recommended Ports

| Service | Port |
| --- | --- |
| FastAPI | 10706 or 18000 |
| PostgreSQL | 5432 |
| Redis | 6379 |
| Qdrant | 6333 |
| MinIO API | 9000 |
| MinIO Console | 9001 |

## systemd Units

Recommended units:

```text
pskit-api.service
pskit-worker.service
pskit-celery-beat.service
qdrant.service
minio.service
```

PostgreSQL and Redis can use system packages or Docker Compose.

## Runtime Env

Use:

```text
/data1/kxchen/pskit-2.0/.env
```

Do not commit `.env`.

Commit only:

```text
.env.example
```

## Direct API Access

Current A6000 can directly access:

- DeepSeek chat API.
- SiliconFlow embedding API.

Proxy/tunnel fallback can be documented, but the main design should not depend on local laptop forwarding.

## Deployment Checks

After start:

```text
GET /api/health
GET /api/doctor
GET /api/admin/rag/status
```

Expected:

- Backend OK.
- PostgreSQL OK.
- Redis OK.
- Qdrant OK.
- MinIO OK.
- LLM API OK.
- Embedding API OK.
- core model weights OK.
- Foldseek/DSSP OK.
- AF3 runtime OK or explicit WARN.

