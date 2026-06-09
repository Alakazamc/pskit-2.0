# Backend

Backend stack:

```text
FastAPI
Pydantic v2
SQLAlchemy 2
Alembic
SQLite fallback / PostgreSQL-ready SQLAlchemy models
httpx
Local worker now; Celery/Redis target
Local artifacts now; MinIO/S3 target
LangGraph retrieval node plus OpenAI-compatible tool-call loop
```

Recommended modules:

```text
app/
  main.py
  config.py
  db/
  auth/
  api/
  agent/
  rag/
  tools/
  tasks/
  artifacts/
  doctor/
```

## Current Status

Implemented:

- FastAPI app factory.
- Settings loader.
- SQLAlchemy models.
- SQLite fallback for local smoke tests.
- Auth register/login/logout/me.
- First registered user becomes admin.
- HttpOnly cookie sessions.
- Health endpoint.
- Admin doctor endpoint.
- Tool catalog endpoint.
- Qdrant-first RAG endpoint with keyword fallback.
- Agent session/message SSE endpoint.
- Tool-call loop with OpenAI-compatible chat client.
- LangGraph retrieval node with Qdrant-first RAG and keyword fallback source injection.
- Structure tools for splitting, fragments, contact maps, and binding-pair annotation.
- PDB/UniProt/RNAcentral/SerpAPI lookup tools.
- Task and artifact endpoints with ownership checks.
- Local task worker for AlphaFold3, INABe binding-site prediction, PAIR, and empirical features through `PSKIT_LEGACY_ROOT`.

## Local Smoke Test

From `backend/`:

```bash
PYTHONPATH=. python3 - <<'PY'
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
resp = client.post("/api/auth/register", json={"username": "admin", "password": "password123"})
print(resp.status_code, resp.json())
print(client.get("/api/auth/me").status_code)
print(client.get("/api/tools").status_code)
print(client.get("/api/health").json())
PY
```

Run server:

```bash
PYTHONPATH=. uvicorn app.main:app --host 127.0.0.1 --port 10706
```

Run one queued task:

```bash
PYTHONPATH=. python3 -m app.tasks.worker once
```

Run worker continuously:

```bash
PYTHONPATH=. python3 -m app.tasks.worker forever
```

Production deployment should use PostgreSQL, Redis/Celery, Qdrant, and MinIO instead of the SQLite/local filesystem fallback when the project moves beyond single-host A6000 operation.
