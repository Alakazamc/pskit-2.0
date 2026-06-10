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
pskit2_ctl.sh
```

Scripts must not contain API keys or passwords.

Typical A6000 run:

```bash
cp .env.example .env
# edit .env without committing secrets
PSKIT_BIND=172.31.199.38:10706 scripts/start_a6000.sh
```

For a no-sudo background deployment on A6000, prefer the control script:

```bash
PSKIT_BIND=172.31.199.38:10716 scripts/pskit2_ctl.sh start
scripts/pskit2_ctl.sh status
scripts/pskit2_ctl.sh logs 120
scripts/pskit2_ctl.sh stop
```

`pskit2_ctl.sh` stores PID files and logs under `/tmp/pskit2` by default. Override
that location with `PSKIT_RUN_DIR=/path/to/run-dir` if needed.

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
