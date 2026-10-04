-- Current server-side management grants and append-only, secret-free audit.
CREATE TABLE admin_roles (
    user_id text NOT NULL, role text NOT NULL CHECK(role IN ('platform_admin','service_maintainer','quota_operator','auditor')),
    service_id text NOT NULL DEFAULT '', PRIMARY KEY(user_id,role,service_id)
);
CREATE TABLE admin_group_members (group_id text NOT NULL, user_id text NOT NULL, PRIMARY KEY(group_id,user_id));
CREATE TABLE admin_audit_events (
    event_id text PRIMARY KEY, actor_user_id text NOT NULL, action text NOT NULL,
    resource_id text NOT NULL, reason text NOT NULL, request_id text NOT NULL,
    created_at text NOT NULL, before_json text NOT NULL, after_json text NOT NULL
);
CREATE INDEX admin_audit_order ON admin_audit_events(created_at,event_id);
CREATE TABLE admin_models (
    alias text PRIMARY KEY, revision integer NOT NULL, state text NOT NULL,
    draft_json text, published_json text
);
CREATE TABLE admin_services (
    service_id text PRIMARY KEY, revision integer NOT NULL, state text NOT NULL,
    data_json text NOT NULL, published_revision integer, schema_digest text
);
CREATE TABLE admin_service_versions (
    service_id text NOT NULL, revision integer NOT NULL, data_json text NOT NULL,
    PRIMARY KEY(service_id,revision)
);
CREATE TABLE admin_service_checks (
    check_id text PRIMARY KEY, service_id text NOT NULL, revision integer NOT NULL,
    data_json text NOT NULL
);
CREATE TABLE admin_config_releases (
    release_id text PRIMARY KEY, revision integer NOT NULL, state text NOT NULL,
    data_json text NOT NULL
);
CREATE TABLE admin_config_outbox (
    release_id text PRIMARY KEY, payload_json text NOT NULL, created_at text NOT NULL,
    delivered_at text
);
CREATE TABLE admin_revisions (
    kind text NOT NULL, resource_id text NOT NULL, revision integer NOT NULL,
    PRIMARY KEY(kind,resource_id)
);
CREATE TABLE admin_user_limits (
    user_id text PRIMARY KEY, cpu_daily_core_ms bigint NOT NULL DEFAULT 0,
    concurrency_limit integer NOT NULL DEFAULT 1, storage_limit_bytes bigint NOT NULL DEFAULT 0
);

-- PostgreSQL job revisions reflect worker changes as well as operator actions.
CREATE FUNCTION admin_track_job_revision() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status IS DISTINCT FROM OLD.status OR NEW.progress IS DISTINCT FROM OLD.progress
       OR NEW.gpu_accounting_status IS DISTINCT FROM OLD.gpu_accounting_status THEN
        INSERT INTO admin_revisions(kind,resource_id,revision) VALUES ('job',NEW.id,1)
        ON CONFLICT(kind,resource_id) DO UPDATE SET revision=admin_revisions.revision+1;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER admin_job_revision AFTER UPDATE ON agent_jobs
    FOR EACH ROW EXECUTE FUNCTION admin_track_job_revision();
