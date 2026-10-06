-- Versioned MCP endpoints, config-driven Tool Products, immutable releases, and durable runs.
CREATE TABLE mcp_service_endpoints (
    endpoint_id text PRIMARY KEY,
    uri text NOT NULL,
    transport text NOT NULL CHECK (transport IN ('streamable_http', 'sse', 'stdio')),
    credential_ref text,
    network_zone text NOT NULL,
    state text NOT NULL CHECK (state IN ('approved', 'suspended')),
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE mcp_service_revisions (
    service_id text NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    endpoint_id text NOT NULL REFERENCES mcp_service_endpoints(endpoint_id),
    manifest_json jsonb NOT NULL,
    manifest_digest text NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (service_id, revision)
);

CREATE TABLE mcp_discovery_snapshots (
    discovery_id text PRIMARY KEY,
    service_id text NOT NULL,
    service_revision integer NOT NULL,
    protocol_json jsonb NOT NULL,
    tools_json jsonb NOT NULL,
    discovery_digest text NOT NULL,
    discovered_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (service_id, service_revision)
        REFERENCES mcp_service_revisions(service_id, revision)
);

CREATE TABLE tool_products (
    product_id text PRIMARY KEY,
    slug text NOT NULL UNIQUE,
    owner_user_id text NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    draft_json jsonb NOT NULL,
    active_release_id text,
    updated_by text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE tool_product_revisions (
    product_id text NOT NULL REFERENCES tool_products(product_id),
    revision integer NOT NULL CHECK (revision > 0),
    snapshot_json jsonb NOT NULL,
    service_revisions_json jsonb NOT NULL,
    binding_digest text NOT NULL,
    ui_digest text NOT NULL,
    suite_digest text NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (product_id, revision)
);

CREATE TABLE capability_bindings (
    product_id text NOT NULL,
    product_revision integer NOT NULL,
    binding_id text NOT NULL,
    product_action_id text NOT NULL,
    service_id text NOT NULL,
    service_revision integer NOT NULL CHECK (service_revision > 0),
    capability_id text NOT NULL,
    capability_version text NOT NULL,
    binding_json jsonb NOT NULL,
    binding_digest text NOT NULL,
    PRIMARY KEY (product_id, product_revision, binding_id),
    FOREIGN KEY (product_id, product_revision)
        REFERENCES tool_product_revisions(product_id, revision)
);
CREATE INDEX capability_bindings_service
    ON capability_bindings(service_id, service_revision);

CREATE TABLE acceptance_suites (
    suite_id text NOT NULL,
    revision integer NOT NULL CHECK (revision > 0),
    suite_json jsonb NOT NULL,
    suite_digest text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (suite_id, revision)
);

CREATE TABLE acceptance_cases (
    suite_id text NOT NULL,
    suite_revision integer NOT NULL,
    case_id text NOT NULL,
    case_json jsonb NOT NULL,
    case_digest text NOT NULL,
    PRIMARY KEY (suite_id, suite_revision, case_id),
    FOREIGN KEY (suite_id, suite_revision)
        REFERENCES acceptance_suites(suite_id, revision)
);

CREATE TABLE qualification_reports (
    report_id text PRIMARY KEY,
    product_id text NOT NULL,
    product_revision integer NOT NULL,
    suite_id text NOT NULL,
    suite_revision integer NOT NULL,
    report_json jsonb NOT NULL,
    service_revisions_json jsonb NOT NULL,
    binding_digest text NOT NULL,
    ui_digest text NOT NULL,
    suite_digest text NOT NULL,
    status text NOT NULL CHECK (status IN ('passed', 'failed')),
    qualified_at timestamptz NOT NULL,
    FOREIGN KEY (product_id, product_revision)
        REFERENCES tool_product_revisions(product_id, revision),
    FOREIGN KEY (suite_id, suite_revision)
        REFERENCES acceptance_suites(suite_id, revision)
);

CREATE TABLE qualification_case_results (
    report_id text NOT NULL REFERENCES qualification_reports(report_id),
    case_id text NOT NULL,
    result_json jsonb NOT NULL,
    status text NOT NULL CHECK (status IN ('passed', 'failed')),
    PRIMARY KEY (report_id, case_id)
);

CREATE TABLE tool_product_releases (
    release_id text PRIMARY KEY,
    product_id text NOT NULL,
    product_revision integer NOT NULL,
    qualification_report_id text NOT NULL REFERENCES qualification_reports(report_id),
    state text NOT NULL CHECK (state IN ('published', 'suspended')),
    snapshot_json jsonb NOT NULL,
    service_revisions_json jsonb NOT NULL,
    binding_digest text NOT NULL,
    ui_digest text NOT NULL,
    suite_digest text NOT NULL,
    published_by text NOT NULL,
    published_at timestamptz NOT NULL DEFAULT now(),
    suspended_by text,
    suspended_at timestamptz,
    FOREIGN KEY (product_id, product_revision)
        REFERENCES tool_product_revisions(product_id, revision)
);
CREATE INDEX tool_product_release_order
    ON tool_product_releases(product_id, published_at, release_id);

ALTER TABLE tool_products
    ADD CONSTRAINT tool_products_active_release_fk
    FOREIGN KEY (active_release_id) REFERENCES tool_product_releases(release_id);

CREATE TABLE tool_product_release_capabilities (
    release_id text NOT NULL REFERENCES tool_product_releases(release_id),
    binding_id text NOT NULL,
    product_action_id text NOT NULL,
    capability_id text NOT NULL,
    capability_version text NOT NULL,
    service_id text NOT NULL,
    service_revision integer NOT NULL,
    binding_json jsonb NOT NULL,
    binding_digest text NOT NULL,
    PRIMARY KEY (release_id, binding_id)
);

CREATE TABLE review_decisions (
    decision_id text PRIMARY KEY,
    release_id text NOT NULL REFERENCES tool_product_releases(release_id),
    report_id text NOT NULL REFERENCES qualification_reports(report_id),
    actor_user_id text NOT NULL,
    decision text NOT NULL CHECK (decision IN ('approved', 'rejected')),
    reason text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- The legacy completed-history table is named tool_runs. New durable executions use an
-- explicit product prefix so their lifecycle cannot corrupt that backwards-compatible API.
CREATE TABLE tool_product_runs (
    run_id text PRIMARY KEY,
    release_id text NOT NULL REFERENCES tool_product_releases(release_id),
    action_id text NOT NULL,
    user_id text NOT NULL,
    status text NOT NULL CHECK (
        status IN ('queued', 'running', 'cancelling', 'completed', 'failed', 'cancelled')
    ),
    progress integer NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    input_json jsonb NOT NULL,
    snapshot_json jsonb NOT NULL,
    result_json jsonb,
    artifacts_json jsonb NOT NULL DEFAULT '[]'::jsonb,
    usage_json jsonb,
    idempotency_key text NOT NULL,
    request_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, idempotency_key)
);
CREATE INDEX tool_product_runs_owner_order
    ON tool_product_runs(user_id, created_at DESC, run_id);

CREATE TABLE tool_product_run_steps (
    run_id text NOT NULL REFERENCES tool_product_runs(run_id),
    step_id text NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    binding_id text NOT NULL,
    compute_job_id text REFERENCES agent_jobs(id),
    status text NOT NULL,
    input_json jsonb NOT NULL,
    result_json jsonb,
    PRIMARY KEY (run_id, step_id),
    UNIQUE (run_id, ordinal)
);

CREATE TABLE tool_run_events (
    run_id text NOT NULL REFERENCES tool_product_runs(run_id) ON DELETE CASCADE,
    sequence bigint NOT NULL CHECK (sequence > 0),
    event_id text NOT NULL UNIQUE,
    type text NOT NULL,
    data_json jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, sequence)
);

CREATE TABLE tool_product_run_artifacts (
    run_id text NOT NULL REFERENCES tool_product_runs(run_id) ON DELETE CASCADE,
    artifact_id text NOT NULL,
    step_id text,
    metadata_json jsonb NOT NULL,
    PRIMARY KEY (run_id, artifact_id)
);

CREATE TABLE tool_product_run_usage (
    run_id text NOT NULL REFERENCES tool_product_runs(run_id) ON DELETE CASCADE,
    step_id text NOT NULL DEFAULT '',
    usage_json jsonb NOT NULL,
    source text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, step_id)
);

CREATE FUNCTION reject_tool_product_release_snapshot_change()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.product_id IS DISTINCT FROM OLD.product_id
       OR NEW.product_revision IS DISTINCT FROM OLD.product_revision
       OR NEW.qualification_report_id IS DISTINCT FROM OLD.qualification_report_id
       OR NEW.snapshot_json IS DISTINCT FROM OLD.snapshot_json
       OR NEW.service_revisions_json IS DISTINCT FROM OLD.service_revisions_json
       OR NEW.binding_digest IS DISTINCT FROM OLD.binding_digest
       OR NEW.ui_digest IS DISTINCT FROM OLD.ui_digest
       OR NEW.suite_digest IS DISTINCT FROM OLD.suite_digest
       OR NEW.published_by IS DISTINCT FROM OLD.published_by
       OR NEW.published_at IS DISTINCT FROM OLD.published_at THEN
        RAISE EXCEPTION 'IMMUTABLE_RELEASE';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER tool_product_release_snapshot_immutable
    BEFORE UPDATE ON tool_product_releases
    FOR EACH ROW EXECUTE FUNCTION reject_tool_product_release_snapshot_change();

CREATE FUNCTION reject_tool_product_release_capability_change()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'IMMUTABLE_RELEASE';
END $$;

CREATE TRIGGER tool_product_release_capability_immutable
    BEFORE UPDATE OR DELETE ON tool_product_release_capabilities
    FOR EACH ROW EXECUTE FUNCTION reject_tool_product_release_capability_change();
