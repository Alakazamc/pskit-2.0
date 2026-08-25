# PSKit 2.0

PSKit 2.0 is a full-stack BioAI Agent workbench for protein-nucleic-acid analysis. It combines a Vue web UI, FastAPI backend, LangGraph agent orchestration, Qdrant RAG, authenticated user sessions, long-running BioAI task execution, and downloadable scientific reports.

The supported handoff is a reproducible single-server Docker Compose stack. An
optional compatible scientific runtime can be connected through
`PSKIT_LEGACY_ROOT` without bundling licensed models or private data.

## Highlights

- **LangGraph Agent architecture**: retrieval, planning, tool execution, and answer synthesis are explicit graph nodes.
- **RAG knowledge retrieval**: Markdown knowledge base indexed by Qdrant with `BAAI/bge-m3`; keyword fallback is available when the vector stack is unavailable.
- **BioAI tool calling**: PDB/RCSB, UniProt, RNAcentral, SerpAPI, structure splitting, contact maps, binding-site prediction, PAIR-style interaction prediction, CORAL/PepCCD MCP services, remote RNA expert, AlphaFold3 task submission, result reading, and report generation.
- **Recoverable SSE streaming UI**: durable turn IDs prevent duplicate scientific jobs after a browser or proxy disconnect; task and artifact state is polled from the server.
- **Authenticated multi-user system**: username/password login, HttpOnly cookie sessions, proxy-aware database-backed auth limits, admin user/session management, audit events, and ownership checks.
- **Long-running task runtime**: worker process for model/GPU jobs, structured task states, logs, and registered artifacts.
- **Deployment-oriented engineering**: database migrations, readiness checks, locked dependencies, CI gates, offline image export, and end-to-end smoke tests.

## Current User-Facing Features

- Public home/about/technical documentation pages.
- Login and registration pages.
- Agent chat page with Markdown rendering, SSE progress, RAG sources, tool events, result files, task list, and follow-up suggestions.
- Task center with live polling, result downloads, retry chains and queued/running/completed/failed BioAI jobs.
- Tool catalog page.
- Admin pages for users, sessions, runtime metrics and checks of LLM, embedding, Qdrant, MCP, model paths, Foldseek, DSSP and AlphaFold3.
- Downloadable artifacts, including CIF/PDB/JSON/CSV/log/report files.

## Technology Stack

| Layer | Technology | Role |
| --- | --- | --- |
| Frontend | Vue 3, TypeScript, Vite | SPA workbench and protected routes |
| Frontend state/routing | Pinia, Vue Router | Auth state, sessions, tasks, navigation |
| UI | Custom CSS | Responsive BioAI dashboard and Agent chat UI |
| Backend API | FastAPI, Pydantic v2 | HTTP APIs, SSE, auth, tasks, files, doctor |
| Database | SQLite default, PostgreSQL-ready SQLAlchemy models | Users, sessions, messages, tasks, artifacts |
| Auth | Argon2id/PBKDF2 fallback, HttpOnly cookie | Password login and session security |
| Agent orchestration | LangGraph, langchain-core | Planner, executor, synthesizer, graph state |
| LLM client | httpx + OpenAI-compatible chat schema | DeepSeek/OpenRouter/compatible providers |
| RAG vector DB | Qdrant | Semantic retrieval with metadata payloads |
| Embedding model | `BAAI/bge-m3` | 1024-dim Chinese/English document embedding |
| Optional rerank | `BAAI/bge-reranker-v2-m3` | Higher precision RAG ranking |
| Task runtime | Persistent SQL-polling Python worker | Long-running model/GPU task execution |
| Artifacts | Ownership-checked local filesystem | Registered scientific outputs |
| BioAI runtime | PSKit 1.x compatibility subprocess | INABe, PAIR, AF3 and legacy tools |
| External tools | Foldseek, DSSP, AlphaFold3, CORAL/PepCCD MCP, remote RNA expert | Structure and model workflows |
| Testing | pytest/TestClient, smoke scripts, Vue build checks | Local and deployment validation |

## Repository Layout

```text
backend/      FastAPI backend, LangGraph agent, auth, DB models, APIs, tools, worker
frontend/     Vue 3 + TypeScript frontend
knowledge/    Markdown RAG knowledge base
docs/         Design, architecture, API, and test notes
scripts/      Deployment, worker, smoke-test, RAG, and offline export helpers
infra/        Docker Compose dependency plan
agent/        Agent design notes
tests/        Test plan and placeholders
```

## Quick Start

For deployment or transfer to another machine, use the verified Docker path in
[DOCKER_QUICKSTART.md](DOCKER_QUICKSTART.md). The steps below are for native
development.

### 1. Clone

```bash
git clone <your-repo-url> pskit-2.0
cd pskit-2.0
```

### 2. Configure Environment

```bash
cp .env.example .env
chmod 600 .env
```

Edit `.env` and fill in your API keys, model paths, Qdrant settings, and PSKit runtime paths. Do not commit `.env`.

### 3. Install Backend Dependencies

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install uv==0.11.16
uv sync --frozen --extra dev
```

### 4. Build Frontend

```bash
cd ../frontend
npm ci
npm run build
```

### 5. Start Backend and Worker

From the repository root:

```bash
scripts/pskit2_ctl.sh start
scripts/pskit2_ctl.sh status
scripts/pskit2_ctl.sh logs 120
```

Stop services:

```bash
scripts/pskit2_ctl.sh stop
```

## Server Deployment

For a direct trusted-LAN deployment, bind to the server interface and restrict
access with a firewall:

```bash
PSKIT_BIND=0.0.0.0:10716 scripts/pskit2_ctl.sh start
```

Then access:

```text
http://SERVER_IP:10716/agent
```

For a reproducible handoff, prefer the Docker workflow in
[DOCKER_QUICKSTART.md](DOCKER_QUICKSTART.md).

## RAG Setup

PSKit 2.0 uses Markdown files under `knowledge/` as the source document library.

Recommended vector setup:

```text
EMBEDDING_MODEL=BAAI/bge-m3
QDRANT_VECTOR_SIZE=1024
QDRANT_COLLECTION=pskit_knowledge
```

Build or rebuild the index:

```bash
cd backend
PYTHONPATH=. python3 ../scripts/build_rag_index.py
```

If `QDRANT_PATH` is set, PSKit uses embedded local Qdrant storage. If not, it connects to `QDRANT_URL`. If Qdrant or embedding is unavailable, Agent retrieval falls back to keyword search.

## Environment Variables

Use `.env.example` as the canonical template. Main groups:

- **Server**: `PSKIT_BIND`, `DATABASE_URL`, `DATA_DIR`, `ARTIFACT_DIR`
- **LLM**: `LLM_BASE_URL`, `LLM_MODEL_ID`, `LLM_API_KEY`
- **Embedding/RAG**: `EMBEDDING_BASE_URL`, `EMBEDDING_MODEL`, `EMBEDDING_API_KEY`, `QDRANT_URL`, `QDRANT_API_KEY`, `QDRANT_PATH`, `QDRANT_COLLECTION`, `QDRANT_VECTOR_SIZE`
- **Search**: `SERPAPI_API_KEY`
- **BioAI runtime**: `PSKIT_LEGACY_ROOT`, `PSKIT_MODEL_PARAMETERS`, `PSKIT_FOLDSEEK`, `PSKIT_DSSP`
- **AlphaFold3**: `PSKIT_AF3_DB_DIR`, `PSKIT_AF3_MODEL_DIR`, `PSKIT_AF3_IMAGE`, `PSKIT_AF3_GPU_DEVICE`
- **CORAL MCP**: `REMOTE_RNA_EXPERT_SSE_URL` (SSE transport; tool `generate_rna_for_protein`)
- **PepCCD MCP**: `PEPCCD_MCP_URL`, `PEPCCD_MCP_TOOL_NAME` (Streamable HTTP transport)
- **Security**: `REGISTRATION_MODE`, `SESSION_COOKIE_NAME`, `SESSION_TTL_DAYS`, `COOKIE_SECURE`, login rate limits

Never commit real API keys, model weights, task outputs, user databases, or `.env`.

## Smoke Tests

Backend API smoke test:

```bash
cd backend
PYTHONPATH=. python3 ../scripts/smoke_backend.py
```

Live backend smoke test:

```bash
scripts/smoke_live_backend.sh
```

Frontend build check:

```bash
cd frontend
npm run build
```

Runtime check:

```bash
scripts/pskit2_ctl.sh status
```

## Open Source Notes

This repository intentionally excludes:

- `.env` and private API keys.
- User database files.
- Generated task outputs and artifacts.
- ESM2, SaProt, RNA-FM, INABe, AlphaFold3, or other model weights.
- AlphaFold3 model parameters and public databases.

AlphaFold3, model weights, third-party tools, and biological databases are governed by their own licenses and terms. You must obtain and configure them separately.

## Publishing to GitHub

If you already have a GitHub repository:

```bash
git remote add origin git@github.com:<owner>/<repo>.git
git push -u origin main
```

If using GitHub CLI:

```bash
gh auth login
gh repo create <owner>/<repo> --private --source=. --remote=origin --push
```

## Contact

If there are questions or issues, please contact **Alakazamc on WeChat**.
