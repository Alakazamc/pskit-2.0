# PSKit 2.0 Deployment Guide

This guide explains how to deploy PSKit 2.0 for development, A6000 intranet demos, and production-style server operation.

If you run into deployment issues, please open a GitHub issue.

## 1. Prerequisites

Required:

- Linux server or workstation with Python 3.10+.
- Node.js 20+ and npm for building the Vue frontend.
- Git.
- Network access to the selected LLM and embedding providers.
- Existing PSKit 1.x runtime if you want to run legacy model tools.

Recommended for A6000:

- NVIDIA driver and Docker if AlphaFold3 runs in a container.
- Foldseek executable.
- DSSP/mkdssp executable.
- AlphaFold3 databases and model parameters.
- Local or cloud Qdrant.

Optional:

- PostgreSQL instead of SQLite.
- Redis/Celery for a production queue.
- S3/MinIO for artifact storage.
- Caddy/Nginx for HTTPS and reverse proxy.

## 2. Directory Plan

Recommended server path:

```text
/data1/kxchen/pskit-2.0
```

Runtime files:

```text
/data1/kxchen/pskit-2.0/.env
/tmp/pskit2/backend.pid
/tmp/pskit2/worker.pid
/tmp/pskit2/backend.log
/tmp/pskit2/worker.log
```

Do not commit runtime files, databases, logs, model weights, or `.env`.

## 3. Environment Setup

Create a private `.env`:

```bash
cd /data1/kxchen/pskit-2.0
cp .env.example .env
chmod 600 .env
```

Edit `.env`:

```bash
vim .env
```

Minimum required values for Agent chat:

```text
PSKIT_BIND=127.0.0.1:10716
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL_ID=deepseek-v4-flash
LLM_API_KEY=replace-me
DATABASE_URL=sqlite:///./data/pskit2.sqlite3
```

Minimum required values for RAG:

```text
EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_API_KEY=replace-me
QDRANT_COLLECTION=pskit_knowledge
QDRANT_VECTOR_SIZE=1024
```

For local embedded Qdrant on a single server:

```text
QDRANT_PATH=/data1/kxchen/pskit-2.0/data/qdrant-local
```

For Qdrant Cloud:

```text
QDRANT_URL=https://your-qdrant-cloud-url:6333
QDRANT_API_KEY=replace-me
QDRANT_PATH=
```

BioAI runtime values:

```text
PSKIT_LEGACY_ROOT=/data1/kxchen/pskit
PSKIT_MODEL_PARAMETERS=/data1/kxchen/pskit-data/model_parameters
PSKIT_FOLDSEEK=/path/to/foldseek
PSKIT_DSSP=/path/to/mkdssp
PSKIT_AF3_DB_DIR=/home/public/database/alphafold3
PSKIT_AF3_MODEL_DIR=/data/hzeng/af3/model-parameters
PSKIT_AF3_IMAGE=alphafold3:3.0.1
PSKIT_AF3_GPU_DEVICE=0
REMOTE_RNA_EXPERT_SSE_URL=http://127.0.0.1:8099/sse
```

## 4. Install Dependencies

Backend:

```bash
cd /data1/kxchen/pskit-2.0/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

If using the existing server Python environment:

```bash
cd /data1/kxchen/pskit-2.0/backend
PYTHONPATH=. python3 -m py_compile app/main.py
```

Frontend:

```bash
cd /data1/kxchen/pskit-2.0/frontend
npm install
npm run build
```

## 5. Build RAG Index

After LLM/embedding/Qdrant variables are configured:

```bash
cd /data1/kxchen/pskit-2.0/backend
PYTHONPATH=. python3 ../scripts/build_rag_index.py
```

Expected result:

```text
collection=<your collection>
chunks indexed
vector size=1024
```

If embedding or Qdrant is unavailable, PSKit still runs with keyword fallback, but RAG quality is lower.

## 6. Start Services

No-sudo background startup:

```bash
cd /data1/kxchen/pskit-2.0
PSKIT_BIND=127.0.0.1:10716 scripts/pskit2_ctl.sh start
```

Check status:

```bash
scripts/pskit2_ctl.sh status
```

Read logs:

```bash
scripts/pskit2_ctl.sh logs 120
```

Stop:

```bash
scripts/pskit2_ctl.sh stop
```

Restart:

```bash
scripts/pskit2_ctl.sh restart
```

## 7. Access

A6000 intranet access:

```text
http://127.0.0.1:10716/agent
```

For public or cross-network access, put Caddy/Nginx in front of the service and enable HTTPS. Keep the account system enabled.

## 8. First Login

Open the site and register the first user. The first registered account becomes `admin`.

Admin-only features:

- Runtime doctor.
- Full dependency health checks.

Normal users can use Agent, tasks, tools, files, and reports, but only for their own data.

## 9. Verification Checklist

Health:

```bash
curl http://127.0.0.1:10716/api/health
```

Backend smoke:

```bash
cd /data1/kxchen/pskit-2.0/backend
PYTHONPATH=. python3 ../scripts/smoke_backend.py
```

Frontend build:

```bash
cd /data1/kxchen/pskit-2.0/frontend
npm run build
```

LangGraph Agent smoke:

```bash
cd /data1/kxchen/pskit-2.0/backend
set -a
. ../.env
set +a
PYTHONPATH=. python3 - <<'PY'
from types import SimpleNamespace
from uuid import uuid4
from app.agent.orchestrator import AgentRuntime, LangGraphAgentRunner

runtime = AgentRuntime(
    db=None,
    user=SimpleNamespace(id=uuid4(), username="smoke"),
    session=SimpleNamespace(id=uuid4()),
)
runner = LangGraphAgentRunner(runtime, [{"role": "user", "content": "hello"}])
events = list(runner.iter_events("你好，只回复一句话"))
print([event["type"] for event in events])
print(runner.record.final_answer[:300])
print(runner.record.rag_backend)
PY
```

Expected event types include:

```text
agent_step
knowledge_sources
message_delta
suggestions
```

## 10. Troubleshooting

`Connection refused` for LLM:

- Confirm `LLM_BASE_URL` includes `/v1` for DeepSeek: `https://api.deepseek.com/v1`.
- Test from the server: `curl -I https://api.deepseek.com/v1/models`.
- Check campus gateway, firewall, or outbound HTTPS policy.

Embedding error:

- Confirm `EMBEDDING_API_KEY`.
- Confirm `EMBEDDING_MODEL=BAAI/bge-m3`.
- Confirm provider supports OpenAI-compatible `/embeddings`.

Qdrant dimension error:

- `BAAI/bge-m3` returns 1024-dimensional vectors.
- Set `QDRANT_VECTOR_SIZE=1024`.
- Rebuild the collection if it was created with the wrong dimension.

Foldseek missing:

- Set `PSKIT_FOLDSEEK=/absolute/path/to/foldseek`.
- Run `$PSKIT_FOLDSEEK version`.

DSSP missing:

- Set `PSKIT_DSSP=/absolute/path/to/mkdssp`.
- Run `$PSKIT_DSSP --version`.

AlphaFold3 failure:

- Confirm Docker/image availability.
- Confirm `PSKIT_AF3_DB_DIR`.
- Confirm `PSKIT_AF3_MODEL_DIR`.
- Confirm GPU visibility.

## 11. GitHub Publishing

If GitHub CLI is available:

```bash
gh auth login
gh repo create <owner>/<repo> --private --source=. --remote=origin --push
```

If the repository already exists:

```bash
git remote add origin git@github.com:<owner>/<repo>.git
git push -u origin main
```

If GitHub upload fails because no account is authenticated, finish `gh auth login` or provide a remote URL and push credentials.

## 12. Contact

If there are questions or issues, please open a GitHub issue.
