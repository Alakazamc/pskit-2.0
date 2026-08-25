# API Specification

## Auth

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| POST | `/api/auth/register` | public | Register user |
| POST | `/api/auth/login` | public | Login |
| POST | `/api/auth/logout` | user | Logout current session |
| GET | `/api/auth/me` | optional | Get current user |

## Agent

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| POST | `/api/agent/sessions` | user | Create session |
| GET | `/api/agent/sessions` | user | List own sessions |
| GET | `/api/agent/sessions/{session_id}` | owner | Get session history |
| DELETE | `/api/agent/sessions/{session_id}` | owner | Archive session |
| POST | `/api/agent/sessions/{session_id}/message` | owner | Send message and stream response |
| GET | `/api/agent/sessions/{session_id}/files` | owner | List artifacts |
| POST | `/api/agent/sessions/{session_id}/report` | owner | Generate report |

## Tasks

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| POST | `/api/tasks` | user | Create task |
| GET | `/api/tasks` | user | List own tasks |
| GET | `/api/tasks/{task_id}` | owner | Get task status |
| POST | `/api/tasks/{task_id}/cancel` | owner | Cancel task |
| GET | `/api/tasks/{task_id}/files` | owner | List task files |

## Files

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/api/files/{artifact_id}/download` | owner | Download file |
| GET | `/api/files/{artifact_id}/preview` | owner | Preview text/json/csv |
| GET | `/api/files/{artifact_id}/viewer` | owner | Viewer-compatible structure URL |

## RAG Admin

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/api/admin/rag/status` | admin | RAG index status |
| POST | `/api/admin/rag/rebuild` | admin | Rebuild index |
| POST | `/api/admin/rag/query-test` | admin | Test retrieval |

## Doctor

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/api/doctor` | admin | Runtime health report |

## Streaming Message API

`POST /api/agent/sessions/{session_id}/message` returns `text/event-stream`.

Events:

```text
knowledge_sources
message_delta
tool_call_started
tool_call_finished
task_created
task_update
artifact_created
error
done
```

