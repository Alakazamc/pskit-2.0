CREATE TABLE release_admission_migrations (
    version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
);
CREATE TABLE release_admission_state (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    component_version INTEGER NOT NULL CHECK (component_version = 1),
    deployment_id TEXT NOT NULL,
    environment TEXT, project TEXT, qualification_sha256 TEXT,
    state TEXT NOT NULL CHECK (state IN ('open','closed')),
    identity_json TEXT,
    generation BIGINT NOT NULL DEFAULT 0 CHECK (generation >= 0),
    updated_at DOUBLE PRECISION NOT NULL,
    CHECK ((environment IS NULL AND project IS NULL AND qualification_sha256 IS NULL)
        OR (environment IS NOT NULL AND project IS NOT NULL AND qualification_sha256 IS NOT NULL)),
    CHECK (state <> 'closed' OR identity_json IS NOT NULL)
);
