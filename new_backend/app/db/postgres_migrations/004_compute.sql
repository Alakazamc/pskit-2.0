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
