# Contributing

## Development Setup

Backend:

```bash
cd backend
PYTHONPATH=. python3 -m uvicorn app.main:app --host 127.0.0.1 --port 10706
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Worker:

```bash
scripts/run_worker_forever.sh
```

## Validation

Run the backend smoke test:

```bash
cd backend
PYTHONPATH=. python3 ../scripts/smoke_backend.py
```

Build the frontend:

```bash
cd frontend
npm run build
```

Run the live backend smoke test:

```bash
scripts/smoke_live_backend.sh
```

## Code Rules

- Keep API keys, model weights, runtime databases, and task outputs out of Git.
- Preserve user ownership checks for sessions, tasks, artifacts, and downloads.
- Prefer typed request/response schemas for new APIs.
- Add RAG knowledge updates under `knowledge/` when adding user-facing capabilities or operational failure modes.
- Keep long-running model jobs in the task worker path instead of blocking request handlers.
