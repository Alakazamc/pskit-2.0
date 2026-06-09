# Test Plan

## Backend Tests

Use:

```text
pytest
httpx AsyncClient
```

Test areas:

- Register first user as admin.
- Register second user as normal user.
- Password hash is not plaintext.
- Login sets HttpOnly cookie.
- Logout invalidates session.
- Unauthenticated protected APIs return 401.
- Normal user doctor returns 403.
- User A cannot access User B sessions.
- User A cannot download User B artifacts.
- Task ownership is enforced.

## RAG Tests

- Markdown files are chunked.
- Embedding client handles API error.
- Qdrant upsert works.
- Retrieval returns sources.
- Reranker changes ordering.
- stale hash warning works.
- BM25 fallback works when Qdrant is unavailable.

## Agent Tests

- RAG question returns cited answer.
- PDB download tool is called with valid args.
- Missing `pdb_id` is rejected before tool execution.
- Binding site preflight catches missing Foldseek.
- AF3 request creates a task instead of blocking.
- Tool failure generates structured diagnostic.

## Worker Tests

- Celery task status transitions.
- stdout/stderr logs are registered.
- completed task registers artifacts.
- failed task stores error type and message.

## Frontend Tests

Use:

```text
Vitest
Playwright
```

Test:

- Login/register flow.
- Protected route guard.
- Agent stream rendering.
- Artifact card download.
- RAG source chip display.
- Doctor admin-only display.
- 401 redirects to login.
- 403 displays permission error.

## Smoke Tests

Before demo:

- `/api/health`
- `/api/doctor`
- Qdrant query test.
- LLM one-token response.
- BGE-M3 embedding dimension check.
- PDB download known ID.
- Read generated artifact.

