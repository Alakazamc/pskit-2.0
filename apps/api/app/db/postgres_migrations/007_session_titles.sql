CREATE TABLE agent_session_titles (
    session_id text PRIMARY KEY, user_id text NOT NULL, status text NOT NULL,
    run_id text, prompt_json text, created_at text NOT NULL, started_at text
);
CREATE INDEX agent_session_titles_pending ON agent_session_titles(status, created_at);
