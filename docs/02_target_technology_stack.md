# Technology Stack

This document describes the current PSKit 2.0 implementation and the production hardening targets.

If there are questions or issues, please open a GitHub issue.

## Implemented Stack

| Layer | Technology | Responsibility |
| --- | --- | --- |
| Frontend | Vue 3, TypeScript, Vite | Single-page workbench UI |
| Routing/state | Vue Router, Pinia | Public/protected routes and app state |
| Styling | Custom CSS | BioAI dashboard and Agent chat UI |
| Backend API | FastAPI | HTTP APIs, SSE, auth, files, tasks, doctor |
| Validation | Pydantic v2 | Request/response settings and schemas |
| ORM | SQLAlchemy 2 | Users, sessions, messages, tasks, artifacts |
| Database | SQLite default | Single-server demo persistence |
| Auth | Argon2id/PBKDF2 fallback, HttpOnly cookie | Login and session security |
| Agent graph | LangGraph | RAG, planner, executor, synthesizer workflow |
| LangChain layer | langchain-core messages | Message abstraction without monolithic LangChain dependency |
| LLM client | httpx + OpenAI-compatible schema | DeepSeek/OpenRouter/compatible chat APIs |
| RAG documents | Markdown files under `knowledge/` | Maintainable project/system knowledge |
| Vector DB | Qdrant | Semantic retrieval and payload metadata |
| Embedding | `BAAI/bge-m3` | 1024-dim bilingual embeddings |
| RAG fallback | Keyword search | Works when embedding/Qdrant is unavailable |
| Task runtime | Local Python worker | Long-running model/tool jobs |
| Artifacts | Local filesystem + DB metadata | Downloadable CIF/PDB/CSV/JSON/log/report files |
| BioAI runtime | `PSKIT_LEGACY_ROOT` subprocess calls | Reuse old PSKit scientific runtime |
| External tools | Foldseek, DSSP, AlphaFold3, remote RNA expert | Structure and model workflows |
| Ops | `scripts/pskit2_ctl.sh` | No-sudo start/stop/status/logs on A6000 |
| Testing | Python smoke scripts, Vue build checks | Basic regression coverage |

## Production Hardening Targets

| Area | Target |
| --- | --- |
| Database | PostgreSQL |
| Queue | Celery + Redis |
| Artifact store | S3/MinIO |
| Reverse proxy | Caddy or Nginx with HTTPS |
| Observability | Structured logs and OpenTelemetry-ready traces |
| Frontend E2E | Playwright |
| Backend tests | pytest integration tests with temporary DB |
| RAG quality | Optional reranker such as `BAAI/bge-reranker-v2-m3` |
| Deployment | systemd units or containerized dependency stack |

## Agent Architecture

PSKit workflows are stateful:

```text
retrieve knowledge -> plan next action -> execute tool -> observe result -> continue or synthesize answer
```

LangGraph makes these steps explicit and testable:

- `retrieve_knowledge` retrieves Qdrant/keyword RAG context.
- `plan_next_action` calls the LLM with OpenAI-compatible tools.
- `execute_tools` runs PSKit tools and records artifacts/tasks.
- `synthesize_answer` produces the final Chinese answer.

The UI receives each step through SSE events.

## RAG Stack

Qdrant is used because it provides a stronger production RAG story:

- Dedicated vector search engine.
- Payload metadata support.
- Cloud or local deployment options.
- Future-ready multi-user document isolation.
- Clear 1024-dimensional configuration for `BAAI/bge-m3`.

`BAAI/bge-m3` is selected because it supports Chinese and English technical documents and produces 1024-dimensional vectors.

Required setting:

```text
QDRANT_VECTOR_SIZE=1024
```

## SQLite and Worker Rationale

SQLite and the local Python worker are intentional first-release choices for A6000 single-machine demos:

- No extra database service required.
- Simple backup and debugging.
- Lower deployment friction.
- Queued long-running tasks are still persisted.

The architecture remains PostgreSQL and Celery/Redis ready.
