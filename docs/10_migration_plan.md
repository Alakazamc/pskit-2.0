# Migration Plan

## Phase 0: Preserve Current System

Rules:

- Do not modify `/data1/kxchen/pskit`.
- Do not move old tasks.
- Do not copy model weights into Git.
- Use old project as reference only.

## Phase 1: Create Repository Skeleton

Create:

- `backend/`
- `frontend/`
- `agent/`
- `knowledge/`
- `infra/`
- `docs/`
- `tests/`

## Phase 2: Backend Foundation

Implement:

- FastAPI app.
- Config loading.
- PostgreSQL connection.
- Alembic migrations.
- Auth.
- User/session tables.
- `/api/health`.
- `/api/doctor` skeleton.

## Phase 3: Artifact and Task System

Implement:

- Task table.
- Artifact table.
- MinIO client.
- Ownership checks.
- File download/preview APIs.
- Celery + Redis.

## Phase 4: RAG With Qdrant

Implement:

- Markdown loader.
- Chunker.
- BGE-M3 embedding client.
- Qdrant upsert.
- Query.
- Rerank.
- Citation.
- Stale hash check.

Migrate old knowledge files into `knowledge/`.

## Phase 5: Tool Adapter Migration

Order:

1. `search_pdb`
2. `fetch_pdb_info`
3. `download_pdb_file`
4. `read_result_file`
5. `split_complex`
6. `extract_fragment`
7. `calculate_contact_map`
8. `annotate_binding_pairs`
9. `predict_binding_sites`
10. `predict_interaction`
11. `run_alphafold3`
12. `remote_rna_expert`
13. `serpapi_search`

## Phase 6: LangGraph Agent

Implement:

- AgentState.
- RAG node.
- skill selection node.
- preflight node.
- LLM node.
- tool execution node.
- task queue node.
- error diagnosis node.
- answer composition node.
- persistence node.

## Phase 7: Vue Frontend

Implement:

- Public pages.
- Login/register.
- Agent workspace.
- Session list.
- Artifact cards.
- RAG source chips.
- Task progress.
- Doctor/admin page.
- Mol* viewer.

## Phase 8: Demo Cases

Prepare stable demos:

- RAG capability question.
- PDB download.
- RNA binding site prediction.
- Contact map.
- remote RNA design.
- AF3 short sequence.
- report generation.

