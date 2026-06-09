# ADR 0001: Final Technology Stack

## Status

Accepted.

## Decision

Use:

```text
Vue 3 + TypeScript + Vite
FastAPI + Pydantic v2
LangGraph
PostgreSQL + SQLAlchemy + Alembic
Qdrant + BAAI/bge-m3 + reranker
Celery + Redis
MinIO / S3-compatible artifacts
SSE
Argon2id + HttpOnly cookie sessions
A6000 native deployment
```

## Rationale

This stack is more authoritative than the current Rust/SQLite/Chroma-native prototype for an AI application portfolio:

- Python aligns better with BioAI model runtimes.
- LangGraph provides explicit stateful workflow orchestration.
- PostgreSQL is production-grade relational storage.
- Qdrant is stronger for production RAG and metadata filtering.
- Celery/Redis is recognized for long-running background jobs.
- MinIO/S3 makes artifact management explicit and scalable.

## Consequences

- More moving parts than the current deployment.
- Requires Docker Compose or system services for local development.
- Requires careful docs and doctor checks.
- Easier to explain in interviews and GitHub README.

