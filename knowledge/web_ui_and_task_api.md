# Web UI and Task API / 网页与任务 API

## Routes / 页面路由

`/` and `/about` are public. `/login` and `/register` handle authentication.
Authenticated users can access `/agent`, `/tasks`, and `/tools`.
`/admin/doctor` requires an administrator.

## Authentication API / 认证 API

Use `GET /api/auth/registration` to discover whether registration is open.
Register, login, logout, and current-user endpoints are under `/api/auth` and
use an HTTP-only session cookie. Administrators create additional users with
`POST /api/auth/users`.

## Task API / 任务 API

`POST /api/tasks` validates and queues a supported task. `GET /api/tasks`
returns the current user's paginated history, `GET /api/tasks/{task_id}` returns
one owned task, and `GET /api/tasks/{task_id}/files` lists registered outputs.
Statuses are `queued`, `running`, `completed`, and `failed`.

## Supported task types / 支持的任务类型

The supported names are `predict_binding_sites`, `predict_interaction`,
`extract_empirical_features`, `run_alphafold3`, `coral_mcp__predict`,
`pepccd_mcp__generate`, and
`remote_rna_expert__generate_rna_for_protein`. Sequence input is capped at
20,000 total characters and serialized task input at 1 MB. Individual tools
have tighter shape and count validation.

## Artifact API / 产物 API

Task and tool outputs are registered as artifacts. Download an owned artifact
with `GET /api/files/{artifact_id}/download`. IDs belonging to another user are
reported as not found.
