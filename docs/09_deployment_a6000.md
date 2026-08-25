# A6000 Deployment

This document records the recommended A6000 deployment for PSKit 2.0.

If there are questions or issues, please contact **Alakazamc on WeChat**.

## Goal

Run PSKit 2.0 directly on A6000 so other computers on the same intranet can access it without keeping the developer laptop online.

Current demo URL pattern:

```text
http://172.31.199.38:10716/agent
```

## Runtime Layout

Recommended path:

```text
/data1/kxchen/pskit-2.0
```

Important runtime files:

```text
/data1/kxchen/pskit-2.0/.env
/tmp/pskit2/backend.pid
/tmp/pskit2/worker.pid
/tmp/pskit2/backend.log
/tmp/pskit2/worker.log
```

`.env`, local databases, generated artifacts, logs, and model weights must not be committed.

## Minimum Environment

```text
PSKIT_BIND=172.31.199.38:10716
DATABASE_URL=sqlite:///./data/pskit2.sqlite3

LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL_ID=deepseek-v4-flash
LLM_API_KEY=replace-me

EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_API_KEY=replace-me

QDRANT_COLLECTION=pskit_knowledge
QDRANT_VECTOR_SIZE=1024
QDRANT_DISTANCE=cosine
```

Use embedded local Qdrant for a single-machine demo:

```text
QDRANT_PATH=/data1/kxchen/pskit-2.0/data/qdrant-local
```

Use Qdrant Cloud if available:

```text
QDRANT_URL=https://your-qdrant-cloud-url:6333
QDRANT_API_KEY=replace-me
QDRANT_PATH=
```

## BioAI Runtime Environment

```text
PSKIT_LEGACY_ROOT=/data1/kxchen/pskit
PSKIT_MODEL_PARAMETERS=/data1/kxchen/pskit-data/model_parameters
PSKIT_FOLDSEEK=/path/to/foldseek
PSKIT_DSSP=/path/to/mkdssp

PSKIT_AF3_DB_DIR=/home/public/database/alphafold3
PSKIT_AF3_MODEL_DIR=/data/hzeng/af3/model-parameters
PSKIT_AF3_IMAGE=alphafold3:3.0.1
PSKIT_AF3_GPU_DEVICE=0

REMOTE_RNA_EXPERT_SSE_URL=http://172.31.226.126:8099/sse
```

## Start and Stop

Start:

```bash
cd /data1/kxchen/pskit-2.0
PSKIT_BIND=172.31.199.38:10716 scripts/pskit2_ctl.sh start
```

Status:

```bash
scripts/pskit2_ctl.sh status
```

Logs:

```bash
scripts/pskit2_ctl.sh logs 120
```

Restart:

```bash
scripts/pskit2_ctl.sh restart
```

Stop:

```bash
scripts/pskit2_ctl.sh stop
```

## Build RAG Index

```bash
cd /data1/kxchen/pskit-2.0/backend
set -a
. ../.env
set +a
PYTHONPATH=. python3 ../scripts/build_rag_index.py
```

`BAAI/bge-m3` requires:

```text
QDRANT_VECTOR_SIZE=1024
```

## Verification

Health:

```bash
curl http://172.31.199.38:10716/api/health
```

Frontend:

```bash
curl http://172.31.199.38:10716/agent
```

Backend compile:

```bash
cd /data1/kxchen/pskit-2.0/backend
PYTHONPATH=. python3 -m py_compile app/main.py app/agent/orchestrator.py
```

Frontend build:

```bash
cd /data1/kxchen/pskit-2.0/frontend
npm run build
```

Agent smoke:

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

Expected event types:

```text
agent_step
knowledge_sources
message_delta
suggestions
```

## Common Issues

### LLM connection refused

Use the `/v1` base URL for DeepSeek:

```text
LLM_BASE_URL=https://api.deepseek.com/v1
```

Then test:

```bash
curl -I https://api.deepseek.com/v1/models
```

### Qdrant dimension mismatch

`BAAI/bge-m3` produces 1024-dimensional vectors. Recreate the collection if it was created with a different dimension.

### Missing Foldseek or DSSP

Set absolute paths:

```text
PSKIT_FOLDSEEK=/absolute/path/to/foldseek
PSKIT_DSSP=/absolute/path/to/mkdssp
```

### AlphaFold3 resource missing

Check:

- `PSKIT_AF3_DB_DIR`
- `PSKIT_AF3_MODEL_DIR`
- `PSKIT_AF3_IMAGE`
- GPU visibility
- Docker availability, if using containerized AF3

## Future Production Deployment

For a public deployment, add:

- Caddy or Nginx reverse proxy.
- HTTPS.
- `COOKIE_SECURE=true`.
- PostgreSQL.
- Redis/Celery.
- S3/MinIO artifact storage.
- systemd unit files.

### Persistent scheduling without systemd linger

The A6000 deployment account may not keep a user systemd manager alive after
SSH logout. In that case, install the repository-managed user cron entries:

```bash
./scripts/install_user_cron.sh
./scripts/run_scheduled_job.sh health
tail -n 20 logs/health.log
```

This runs health verification every five minutes and a verified backup every
day at 03:20 Asia/Shanghai. The jobs use per-task locks so a slow run cannot
overlap its next invocation.
