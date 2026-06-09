# Target Technology Stack

## Final Stack

| Layer | Technology | Role |
| --- | --- | --- |
| Frontend | Vue 3 + TypeScript + Vite | Main web application |
| UI styling | Tailwind CSS + Headless UI | Clean responsive UI |
| Frontend state | Pinia | Auth, sessions, tasks, runtime panels |
| Routing | Vue Router | Public/protected/admin routes |
| Molecular viewer | Mol* Viewer | PDB/mmCIF and AF3 structure visualization |
| Backend | FastAPI | HTTP APIs, SSE, auth, files, tasks, doctor |
| Schemas | Pydantic v2 | Request/response/tool/error models |
| DB | PostgreSQL | Users, sessions, tasks, messages, file metadata |
| ORM | SQLAlchemy 2 | Database access |
| Migration | Alembic | Schema migrations |
| Agent | LangGraph | Stateful agent workflow orchestration |
| LLM client | httpx + OpenAI-compatible schema | DeepSeek/OpenRouter/other providers |
| Vector DB | Qdrant | RAG vector search and metadata filtering |
| Embedding | BAAI/bge-m3 | Chinese/English semantic retrieval |
| Reranker | BAAI/bge-reranker-v2-m3 | RAG precision improvement |
| Queue | Celery + Redis | Long-running GPU/model tasks |
| Artifact store | MinIO/S3-compatible | CIF, PDB, CSV, JSON, logs, reports |
| Streaming | SSE | Agent deltas, tool calls, task progress |
| Auth | Argon2id + HttpOnly cookie | Secure session login |
| Permissions | RBAC + ownership checks | user/admin and data isolation |
| Deployment | A6000 native + Docker Compose dependencies | Stable server deployment |
| Reverse proxy | Caddy or Nginx | HTTPS/domain/proxy |
| Observability | Structured logs + OpenTelemetry-ready spans | Debugging and production traceability |
| Testing | pytest, Playwright, Vitest | Backend, E2E, frontend tests |
| Code quality | ruff, mypy, eslint, prettier | Maintainability |

## Why Qdrant Instead Of Chroma

The current PSKit uses Chroma successfully. For PSKit 2.0, Qdrant is selected because it is more authoritative for a production RAG story:

- Dedicated vector search engine.
- Strong payload metadata filtering.
- Payload indexes.
- Hybrid retrieval support.
- Better fit for multi-user and document-level isolation.
- Stronger interview story for production-grade retrieval infrastructure.

Chroma remains useful for quick local prototypes, but the GitHub/portfolio version should use Qdrant.

## Why PostgreSQL Instead Of SQLite

SQLite is good for single-machine demos, but PostgreSQL is a stronger default for:

- Multi-user auth.
- Task history.
- File metadata.
- Session persistence.
- Admin operations.
- Future deployment scaling.

SQLite can remain a local fallback, but PostgreSQL should be the documented production target.

## Why Celery + Redis Instead Of RQ

RQ is simpler, but Celery is more recognizable and more flexible for:

- Long-running model jobs.
- Retry policies.
- Worker routing.
- Task status.
- Scheduled cleanup.
- Production monitoring.

For PSKit, AF3 and model inference tasks justify Celery.

## Why LangGraph

PSKit workflows are graph-like:

```text
RAG -> skill selection -> preflight -> LLM -> tool call -> task -> artifact -> summary
```

LangGraph makes these steps explicit, testable, interruptible, and resumable.

