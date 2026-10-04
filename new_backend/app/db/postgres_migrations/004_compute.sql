-- Generic jobs share identity and run linkage with legacy AF3, not its wire model.
CREATE TABLE compute_capability_versions (
    capability_id text NOT NULL, version text NOT NULL, service_id text NOT NULL,
    manifest_json jsonb NOT NULL, manifest_hash text NOT NULL,
    PRIMARY KEY(capability_id,version)
);
CREATE TABLE compute_job_data (
    job_id text PRIMARY KEY REFERENCES agent_jobs(id), service_id text NOT NULL,
    capability_json jsonb NOT NULL, arguments_json jsonb NOT NULL, budget_json jsonb NOT NULL,
    user_id text NOT NULL, idempotency_key text NOT NULL, request_hash text NOT NULL,
    report_json jsonb, stop_at timestamptz, latest_seq bigint NOT NULL DEFAULT 0,
    UNIQUE(user_id,idempotency_key)
);
CREATE INDEX compute_service_queue ON compute_job_data(service_id,job_id);
CREATE TABLE compute_reservations (
    job_id text PRIMARY KEY REFERENCES agent_jobs(id), user_id text NOT NULL,
    period text NOT NULL, cpu_ms bigint NOT NULL CHECK(cpu_ms>=0),
    gpu_ms bigint NOT NULL CHECK(gpu_ms>=0), active boolean NOT NULL DEFAULT true
);
CREATE TABLE compute_usage_reports (
    job_id text NOT NULL REFERENCES agent_jobs(id), seq bigint NOT NULL,
    payload_hash text NOT NULL, usage_json jsonb NOT NULL, window_json jsonb,
    terminal boolean NOT NULL, PRIMARY KEY(job_id,seq)
);
CREATE TABLE compute_usage_daily (
    job_id text NOT NULL REFERENCES agent_jobs(id), user_id text NOT NULL, day text NOT NULL,
    cpu_ms bigint, gpu_ms bigint, source text NOT NULL, PRIMARY KEY(job_id,day)
);
CREATE INDEX compute_usage_owner_day ON compute_usage_daily(user_id,day);
CREATE TABLE compute_cpu_limits (user_id text PRIMARY KEY, limit_ms bigint NOT NULL CHECK(limit_ms>=0));
