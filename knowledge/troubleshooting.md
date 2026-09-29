# PSKit Troubleshooting / 故障排查

## Start with health / 先看健康状态

Use `/api/health/live` for process liveness and `/api/health/ready` for database
and Qdrant readiness. In Docker, inspect `docker compose ps` and the `migrate`,
`web`, `worker`, and `qdrant` logs. A migration failure blocks web startup by
design.

## Agent does not answer / Agent 不回复

The UI can work without an LLM key, but chat requires `LLM_BASE_URL`,
`LLM_MODEL_ID`, and `LLM_API_KEY`. Check provider reachability and the finite
stream timeout. Do not put real keys in source files or support bundles.

## RAG failure / RAG 失败

Vector retrieval depends on `EMBEDDING_BASE_URL`, `EMBEDDING_MODEL`,
`EMBEDDING_API_KEY`, and healthy Qdrant settings. If embeddings are unavailable,
PSKit can use keyword retrieval from Markdown files under `knowledge/`. Rebuild
the Qdrant index after changing the corpus or embedding model.

## Task remains queued or running / 任务卡住

Confirm the worker is healthy and shares the same `DATABASE_URL`, `DATA_DIR`,
and `ARTIFACT_DIR` as the web service. On startup the worker recovers stale
running tasks and limits retries with `TASK_MAX_ATTEMPTS`. Heavy tools also need
their external binaries, model files, licenses, and GPU/runtime configuration.

## External database failure / 外部数据库失败

RCSB, UniProt, RNAcentral, and SerpAPI can fail because of invalid identifiers,
provider limits, DNS, proxies, or timeouts. Check the matching URL and API-key
variables in the environment template. Avoid aggressive retries after quota or
rate-limit responses.

## Artifact access / 结果文件访问

Structure tools and `read_result_file` accept registered artifact IDs, not
arbitrary server paths. A user can access only their own artifacts. Text preview
is limited to regular UTF-8 files; download binary or large outputs through
`/api/files/{artifact_id}/download`.

## Registration / 注册

With `REGISTRATION_MODE=open`, users can self-register. The first administrator
must also submit the server-only `INITIAL_ADMIN_BOOTSTRAP_TOKEN`. Administrators
can disable users and revoke sessions in `/admin/users`; use `disabled` to close
public registration.
