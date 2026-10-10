ALTER TABLE catalog_skill_versions
    ADD COLUMN IF NOT EXISTS owner_id text;
ALTER TABLE catalog_skill_versions
    ADD COLUMN IF NOT EXISTS visibility text NOT NULL DEFAULT 'public';
ALTER TABLE catalog_skill_versions
    ADD COLUMN IF NOT EXISTS files_json text NOT NULL DEFAULT '[]';
ALTER TABLE catalog_skill_versions
    ADD COLUMN IF NOT EXISTS package_sha256 text;
ALTER TABLE catalog_skill_versions
    ADD COLUMN IF NOT EXISTS source_url text;
ALTER TABLE catalog_skill_versions
    ADD COLUMN IF NOT EXISTS source_commit text;
ALTER TABLE catalog_skill_versions
    ADD COLUMN IF NOT EXISTS license_name text;
ALTER TABLE catalog_skill_versions
    ADD COLUMN IF NOT EXISTS review_reason text;

ALTER TABLE catalog_skill_versions
    DROP CONSTRAINT IF EXISTS catalog_skill_versions_visibility_check;
ALTER TABLE catalog_skill_versions
    ADD CONSTRAINT catalog_skill_versions_visibility_check
    CHECK (visibility IN ('private', 'review_pending', 'public', 'rejected', 'disabled'));

CREATE INDEX IF NOT EXISTS catalog_skill_versions_owner
    ON catalog_skill_versions (owner_id, id, version DESC);
CREATE INDEX IF NOT EXISTS catalog_skill_versions_review
    ON catalog_skill_versions (visibility, created_at DESC);
