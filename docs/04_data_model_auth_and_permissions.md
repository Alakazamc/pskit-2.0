# Data Model, Auth, and Permissions

## Auth Model

PSKit 2.0 uses username/password auth for v1:

- First registered user becomes `admin`.
- Later users become `user`.
- Passwords are hashed with Argon2id.
- Browser receives only an HttpOnly session cookie.
- Database stores only session token hash.
- Logout deletes the current session.

## PostgreSQL Schema

```sql
CREATE TABLE users (
  id UUID PRIMARY KEY,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
  created_at TIMESTAMPTZ NOT NULL,
  disabled_at TIMESTAMPTZ
);

CREATE TABLE auth_sessions (
  session_hash TEXT PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  last_seen_at TIMESTAMPTZ NOT NULL,
  user_agent TEXT,
  ip INET
);

CREATE TABLE agent_sessions (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  archived_at TIMESTAMPTZ
);

CREATE TABLE agent_messages (
  id UUID PRIMARY KEY,
  session_id UUID NOT NULL REFERENCES agent_sessions(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE tasks (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  session_id UUID REFERENCES agent_sessions(id) ON DELETE SET NULL,
  tool_call_id TEXT,
  task_type TEXT NOT NULL,
  status TEXT NOT NULL,
  progress DOUBLE PRECISION NOT NULL DEFAULT 0,
  input JSONB NOT NULL DEFAULT '{}',
  output JSONB NOT NULL DEFAULT '{}',
  error_type TEXT,
  error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ
);

CREATE TABLE artifacts (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  session_id UUID REFERENCES agent_sessions(id) ON DELETE SET NULL,
  task_id UUID REFERENCES tasks(id) ON DELETE SET NULL,
  kind TEXT NOT NULL,
  storage_backend TEXT NOT NULL,
  object_key TEXT NOT NULL,
  filename TEXT NOT NULL,
  mime_type TEXT,
  size_bytes BIGINT,
  metadata JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE rag_index_state (
  collection TEXT PRIMARY KEY,
  knowledge_hash TEXT NOT NULL,
  embedding_model TEXT NOT NULL,
  reranker_model TEXT,
  chunk_count INTEGER NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);
```

## Permission Rules

Public:

- Home page.
- About page.
- Technical docs.
- Login/register.

Authenticated users:

- Agent.
- Task pages.
- Viewer.
- File downloads for owned files.
- Session history for owned sessions.

Admin only:

- Doctor.
- User management.
- RAG rebuild.
- Runtime dependency details.

## Ownership Check Pattern

Every read/write/download endpoint must follow:

```text
authenticate user
-> load object by id
-> verify object.user_id == current_user.id OR current_user.role == admin
-> verify path/object key belongs to registered artifact
-> serve response
```

Never trust:

- Frontend session cache.
- Absolute file paths supplied by the user.
- Tool output paths without DB registration.

