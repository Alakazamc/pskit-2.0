# PSKit 2.0

PSKit 2.0 is a full-stack BioAI Agent workbench for protein-nucleic-acid analysis. It rebuilds the original PSKit deployment around a Vue frontend, FastAPI backend, authenticated multi-user sessions, RAG-assisted agent behavior, long-running model tasks, and downloadable scientific artifacts.

The current implementation keeps the proven PSKit 1.x scientific runtime as a compatibility backend through `PSKIT_LEGACY_ROOT`, while the 2.0 service owns orchestration, auth, task state, file ownership, and the web interface.

## Core Capabilities

- Multi-user auth with HttpOnly cookie sessions and role-based admin access.
- Agent sessions with persisted message history and Server-Sent Events.
- Tool catalog for RCSB PDB, UniProt, RNAcentral, SerpAPI, structure splitting, contact maps, result reading, binding-site prediction, PAIR, empirical features, and AlphaFold3.
- Long-running task execution with persisted status, structured failure states, stdout/stderr logs, and registered downloadable artifacts.
- Runtime doctor for model weights, Foldseek, DSSP, AF3 resources, LLM API, embedding API, and Qdrant.
- Project-knowledge RAG through a LangGraph retrieval node: Qdrant + BAAI/bge-m3 when configured, keyword fallback otherwise.
- Vue 3 workbench UI with public docs, login/register, Agent, Tasks, Tools, and Doctor pages.

## Technology Stack

| Layer | Current Implementation |
| --- | --- |
| Frontend | Vue 3, TypeScript, Vite, Pinia, Vue Router, custom CSS |
| Backend API | FastAPI, Pydantic, SQLAlchemy, httpx |
| Auth | Argon2id when available, PBKDF2 development fallback, HttpOnly cookie sessions |
| Database | SQLite fallback now, PostgreSQL-ready SQLAlchemy models |
| Agent | LangGraph retrieval node, OpenAI-compatible chat client, tool-call loop, SSE events |
| RAG | Qdrant + BAAI/bge-m3 when configured, Markdown keyword fallback otherwise |
| Task runtime | Local worker process now; Celery/Redis target design |
| Artifacts | Local filesystem with ownership checks now; S3/MinIO target design |
| BioAI runtime | PSKit 1.x compatibility subprocess via `PSKIT_LEGACY_ROOT` |

## Repository Layout

```text
backend/      FastAPI backend, database models, auth, APIs, tools, worker
frontend/     Vue 3 application source
knowledge/    Markdown knowledge files for RAG
docs/         Architecture, migration, deployment, test, and resume docs
scripts/      Local/A6000 startup, worker, frontend build, and smoke scripts
infra/        Docker Compose dependency plan
references/   Read-only reference copy of selected old PSKit Agent config
```

## Quick Start

Backend:

```bash
cd backend
PYTHONPATH=. python3 -m uvicorn app.main:app --host 127.0.0.1 --port 10706
```

Worker:

```bash
scripts/run_worker_forever.sh
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

A6000 direct bind example:

```bash
PSKIT_BIND=172.31.199.38:10706 scripts/start_a6000.sh
```

Smoke test:

```bash
cd backend
PYTHONPATH=. python3 ../scripts/smoke_backend.py
```

Live backend smoke test:

```bash
scripts/smoke_live_backend.sh
```

Build the Qdrant RAG index after configuring Qdrant and the embedding API:

```bash
cd backend
PYTHONPATH=. python3 ../scripts/build_rag_index.py
```

## Environment

Copy `.env.example` to `.env` and edit locally. Do not commit `.env`, API keys, model weights, runtime data, or generated artifacts.

Important runtime variables:

- `LLM_BASE_URL`, `LLM_MODEL_ID`, `LLM_API_KEY`
- `EMBEDDING_BASE_URL`, `EMBEDDING_MODEL`, `EMBEDDING_API_KEY`
- `PSKIT_LEGACY_ROOT`
- `PSKIT_MODEL_PARAMETERS`
- `PSKIT_FOLDSEEK`
- `PSKIT_DSSP`
- `PSKIT_AF3_DB_DIR`
- `PSKIT_AF3_MODEL_DIR`
- `PSKIT_AF3_IMAGE`

## Verified On A6000

Current smoke coverage:

- Backend import and route registration.
- Register/login/me.
- Agent session creation.
- Tool catalog API.
- Queued task creation.
- Worker execution and structured failed state for invalid input.
- Worker execution of short AlphaFold3 smoke task, producing CIF/JSON/log artifacts.

## Open Source Notes

This repository does not include private API keys, model weights, task outputs, user databases, or AlphaFold3 parameters. AlphaFold3, model weights, third-party tools, and external databases remain subject to their own licenses and terms.

To publish after GitHub authentication is configured:

```bash
git remote add origin git@github.com:<owner>/<repo>.git
git push -u origin main
```
