-- OpenSandbox ownership is distinct from the retired Docker sandbox manager.
-- Lease expiry means unknown, never confirmed process exit.
CREATE TABLE workspace_sandboxes (
    user_id text PRIMARY KEY,
    provider text NOT NULL CHECK (provider IN ('opensandbox')),
    sandbox_id text UNIQUE,
    volume_id text NOT NULL UNIQUE,
    image_digest text NOT NULL,
    provider_revision bigint NOT NULL CHECK (provider_revision > 0),
    lifecycle_state text NOT NULL
        CHECK (lifecycle_state IN ('creating','ready','draining','replacing','error')),
    runtime_state text NOT NULL
        CHECK (runtime_state IN ('pending','running','stopped','unknown')),
    claim_token text,
    claim_expires_at double precision,
    last_error_code text,
    last_confirmed_at double precision NOT NULL,
    created_at double precision NOT NULL,
    updated_at double precision NOT NULL,
    CHECK (
        (lifecycle_state IN ('creating','replacing') AND claim_token IS NOT NULL
            AND claim_expires_at IS NOT NULL)
        OR
        (lifecycle_state NOT IN ('creating','replacing') AND claim_token IS NULL
            AND claim_expires_at IS NULL)
    )
);

CREATE TABLE workspace_sandbox_leases (
    attempt_id text PRIMARY KEY,
    user_id text NOT NULL REFERENCES workspace_sandboxes(user_id),
    session_id text NOT NULL,
    run_id text NOT NULL,
    fencing_token bigint NOT NULL CHECK (fencing_token > 0),
    state text NOT NULL CHECK (state IN ('active','unknown','released')),
    expires_at double precision NOT NULL,
    process_id text,
    process_state text NOT NULL DEFAULT 'pending'
        CHECK (process_state IN ('pending','running','completed','failed','cancelled','unknown')),
    exit_code integer,
    exit_confirmed boolean NOT NULL DEFAULT false,
    created_at double precision NOT NULL,
    updated_at double precision NOT NULL,
    CHECK (state <> 'released' OR exit_confirmed),
    CHECK (NOT exit_confirmed OR process_state IN ('completed','failed','cancelled'))
);

CREATE UNIQUE INDEX workspace_session_unresolved_lease
    ON workspace_sandbox_leases(user_id, session_id)
    WHERE state <> 'released';

CREATE INDEX workspace_lease_expiry
    ON workspace_sandbox_leases(expires_at)
    WHERE state = 'active';
