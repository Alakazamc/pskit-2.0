# Scripts

Implemented scripts:

```text
start_backend.sh
run_worker_once.sh
run_worker_forever.sh
build_frontend.sh
build_rag_index.py
smoke_backend.py
start_a6000.sh
```

Scripts must not contain API keys or passwords.

Typical A6000 run:

```bash
cp .env.example .env
# edit .env without committing secrets
PSKIT_BIND=172.31.199.38:10706 scripts/start_a6000.sh
```

Run backend smoke test:

```bash
cd backend
PYTHONPATH=. python3 ../scripts/smoke_backend.py
```

Build Qdrant RAG index after Qdrant and `EMBEDDING_API_KEY` are configured:

```bash
cd backend
PYTHONPATH=. python3 ../scripts/build_rag_index.py
```
