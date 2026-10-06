-- No raw account or network identifiers are persisted in these tables.
CREATE TABLE auth_abuse_buckets (
    rule text NOT NULL,
    subject_key text NOT NULL,
    window_start timestamptz NOT NULL,
    count integer NOT NULL CHECK (count >= 0),
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (rule, subject_key, window_start)
);
CREATE INDEX auth_abuse_buckets_expiry ON auth_abuse_buckets (expires_at);

CREATE TABLE auth_abuse_locks (
    rule text NOT NULL,
    subject_key text NOT NULL,
    claim_token text,
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (rule, subject_key)
);
CREATE INDEX auth_abuse_locks_expiry ON auth_abuse_locks (expires_at);

CREATE TABLE auth_abuse_claims (
    token text PRIMARY KEY,
    action text NOT NULL CHECK (action IN (
        'signup_send', 'recovery_send', 'guest_upgrade_send',
        'password_login', 'otp_verify', 'guest_upgrade_verify'
    )),
    outcome text CHECK (outcome IN ('success', 'rejected', 'not_sent', 'outcome_unknown')),
    expires_at timestamptz NOT NULL
);
CREATE INDEX auth_abuse_claims_expiry ON auth_abuse_claims (expires_at);

CREATE TABLE auth_abuse_claim_items (
    token text NOT NULL REFERENCES auth_abuse_claims (token) ON DELETE CASCADE,
    rule text NOT NULL,
    subject_key text NOT NULL,
    window_start timestamptz NOT NULL,
    refundable boolean NOT NULL,
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (token, rule, subject_key, window_start)
);
CREATE INDEX auth_abuse_claim_items_expiry ON auth_abuse_claim_items (expires_at);
