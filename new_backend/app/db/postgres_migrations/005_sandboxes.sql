-- Sandbox runtime state belongs to the control plane; expiry is not exit proof.
CREATE TABLE sandbox_owners (
    owner_id text PRIMARY KEY, instance_id text NOT NULL UNIQUE,
    volume_id text NOT NULL UNIQUE, image_digest text NOT NULL,
    state text NOT NULL CHECK (state IN ('ready','draining','replacing','error')),
    runtime_state text NOT NULL CHECK (runtime_state IN ('running','stopped','unknown','stopping')),
    revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0),
    last_completed_at double precision NOT NULL
);
CREATE TABLE sandbox_leases (
    lease_id text PRIMARY KEY, owner_id text NOT NULL REFERENCES sandbox_owners(owner_id),
    session_id text NOT NULL, run_id text NOT NULL,
    fencing_token bigint NOT NULL CHECK (fencing_token > 0),
    lease_seconds integer NOT NULL CHECK (lease_seconds > 0),
    expires_at double precision NOT NULL,
    state text NOT NULL CHECK (state IN ('active','unknown','released')),
    purpose text NOT NULL DEFAULT 'pi' CHECK (purpose IN ('pi','transfer'))
);
CREATE UNIQUE INDEX sandbox_session_active ON sandbox_leases(owner_id,session_id)
    WHERE state <> 'released' AND purpose = 'pi';
CREATE TABLE sandbox_operations (
    operation_id text PRIMARY KEY, owner_id text NOT NULL REFERENCES sandbox_owners(owner_id),
    kind text NOT NULL, state text NOT NULL, revision bigint NOT NULL,
    image_digest text
);
CREATE TABLE sandbox_artifacts (
    id text PRIMARY KEY, user_id text NOT NULL, session_id text NOT NULL,
    attempt_id text NOT NULL, name text NOT NULL, sha256 text NOT NULL,
    UNIQUE(user_id,session_id,attempt_id,name,sha256)
);
