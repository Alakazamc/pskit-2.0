CREATE TABLE workspace_attempts (
    attempt_id text PRIMARY KEY,
    run_id text NOT NULL,
    session_id text NOT NULL,
    user_id text NOT NULL,
    status text NOT NULL
        CHECK (status IN ('queued','running','cancelling','completed','failed','cancelled','unknown')),
    fencing_token bigint NOT NULL CHECK (fencing_token > 0),
    provider_process_id text,
    reserved_cpu_core_ms bigint NOT NULL DEFAULT 0 CHECK (reserved_cpu_core_ms >= 0),
    wall_ms bigint CHECK (wall_ms IS NULL OR wall_ms >= 0),
    cpu_core_ms bigint CHECK (cpu_core_ms IS NULL OR cpu_core_ms >= 0),
    peak_memory_bytes bigint CHECK (peak_memory_bytes IS NULL OR peak_memory_bytes >= 0),
    exit_code integer,
    output_truncated boolean NOT NULL DEFAULT false,
    provider_error_code text,
    started_at double precision,
    finished_at double precision,
    created_at double precision NOT NULL,
    updated_at double precision NOT NULL,
    UNIQUE (run_id, attempt_id),
    CHECK (status <> 'running' OR started_at IS NOT NULL),
    CHECK (status NOT IN ('completed','failed','cancelled') OR finished_at IS NOT NULL)
);

CREATE INDEX workspace_attempts_run ON workspace_attempts(run_id, created_at);
CREATE INDEX workspace_attempts_active ON workspace_attempts(user_id, status)
    WHERE status IN ('queued','running','cancelling','unknown');
