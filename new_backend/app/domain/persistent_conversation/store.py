"""Store persistence methods for the conversation store."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.contracts.capabilities import ComputeResourceRequirements
from app.db.migrations import migrate_core_database
from app.db.postgres import PostgresDatabase, PostgresStatements
from app.domain.conversation import ConversationStore
from app.domain.identity_policy import GuestAccountDeleting

from .af3 import Af3Mixin
from .recovery import RecoveryMixin
from .runs import RunsMixin
from .usage import UsageMixin
from .workspace import WorkspaceMixin


class PersistentConversationStore(WorkspaceMixin, RunsMixin, Af3Mixin, UsageMixin, RecoveryMixin, ConversationStore):
    """Persist workspace, Run, AF3, and quota state through one database handle."""

    def __init__(
        self, database: PostgresDatabase | str, *, af3_min_gpu_memory_mb: int = 0,
    ) -> None:
        """Open the PostgreSQL store or a temporary legacy SQLite store.

        Args:
            database: Shared PostgreSQL pool; string paths remain for offline legacy tests.
            af3_min_gpu_memory_mb: Minimum GPU memory requested by AF3 jobs.

        Raises:
            ValueError: The database has an unsupported or incomplete schema.
        """
        super().__init__()
        self.af3_resources = ComputeResourceRequirements(
            min_gpu_memory_mb=af3_min_gpu_memory_mb,
        )
        if isinstance(database, PostgresDatabase):
            self.database = database
            self.db = PostgresStatements(database)
            return
        path = database
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        schema = """
            CREATE TABLE IF NOT EXISTS agent_messages (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, session_id TEXT NOT NULL,
                role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL,
                parts_json TEXT
            );
            CREATE TABLE IF NOT EXISTS agent_runs (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, session_id TEXT NOT NULL,
                status TEXT NOT NULL, created_at TEXT NOT NULL,
                resume_attempts INTEGER NOT NULL DEFAULT 0, retry_after TEXT,
                context_json TEXT NOT NULL DEFAULT '{}',
                lease_owner TEXT, lease_expires_at TEXT,
                initial_attempts INTEGER NOT NULL DEFAULT 0, checkpoint_file TEXT,
                last_resumed_job_id TEXT, active_resume_job_id TEXT,
                turn_start_seq INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS agent_events (
                run_id TEXT NOT NULL, seq INTEGER NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY (run_id, seq)
            );
            CREATE TABLE IF NOT EXISTS pi_sessions (
                session_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, session_file TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_jobs (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, run_id TEXT,
                tool_call_id TEXT, status TEXT NOT NULL, progress INTEGER NOT NULL,
                estimated_minutes INTEGER NOT NULL, actual_minutes INTEGER,
                artifacts TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL,
                input_json TEXT, worker_id TEXT, lease_expires_at TEXT,
                simulation INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0, lease_token TEXT,
                first_claimed_at TEXT,
                resource_requirements_json TEXT NOT NULL DEFAULT
                    '{"capability":"af3","gpu_count":1,"min_gpu_memory_mb":0}',
                gpu_accounting_status TEXT NOT NULL DEFAULT 'reserved',
                UNIQUE (run_id, tool_call_id)
            );
            CREATE TABLE IF NOT EXISTS agent_artifact_blobs (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, job_id TEXT NOT NULL,
                name TEXT NOT NULL, kind TEXT NOT NULL, size INTEGER NOT NULL,
                sha256 TEXT NOT NULL, content BLOB NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_approvals (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, run_id TEXT NOT NULL,
                tool_call_id TEXT NOT NULL, estimated_minutes INTEGER NOT NULL,
                status TEXT NOT NULL, job_id TEXT, created_at TEXT NOT NULL,
                input_json TEXT,
                UNIQUE (run_id, tool_call_id)
            );
            CREATE TABLE IF NOT EXISTS agent_token_usage (
                user_id TEXT NOT NULL, period TEXT NOT NULL, used INTEGER NOT NULL,
                PRIMARY KEY (user_id, period)
            );
            CREATE TABLE IF NOT EXISTS agent_token_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
                period TEXT NOT NULL, kind TEXT NOT NULL, amount INTEGER NOT NULL,
                run_id TEXT, created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'posted'
            );
            CREATE TABLE IF NOT EXISTS agent_token_limits (
                user_id TEXT PRIMARY KEY, limit_value INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_model_call_guards (
                call_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, run_id TEXT NOT NULL,
                prompt_bytes INTEGER NOT NULL, max_output_tokens INTEGER NOT NULL,
                reserved_tokens INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_gpu_limits (
                user_id TEXT PRIMARY KEY, limit_value INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_request_keys (
                user_id TEXT NOT NULL, session_id TEXT NOT NULL, key TEXT NOT NULL,
                request_hash TEXT NOT NULL, run_id TEXT NOT NULL,
                PRIMARY KEY (user_id, session_id, key)
            );
            CREATE TABLE IF NOT EXISTS workspace_projects (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
                description TEXT NOT NULL, archived_at TEXT,
                icon TEXT NOT NULL DEFAULT 'folder'
            );
            CREATE TABLE IF NOT EXISTS workspace_sessions (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL, project_id TEXT NOT NULL,
                title TEXT NOT NULL, archived_at TEXT
            );
            CREATE TABLE IF NOT EXISTS workspace_project_skills (
                user_id TEXT NOT NULL, project_id TEXT NOT NULL, skill_id TEXT NOT NULL,
                is_default INTEGER NOT NULL, position INTEGER NOT NULL,
                PRIMARY KEY (user_id, project_id, skill_id)
            );
            """
        try:
            migrate_core_database(self.db, schema)
            self.db.execute("PRAGMA journal_mode=WAL")
        except BaseException:
            self.db.close()
            raise

    @contextmanager
    def _immediate_transaction(self):
        """Hold the database admission lock while a caller makes a claim.

        Yields:
            Control inside the transaction; it commits on success and rolls
            back on an exception.
        """
        if isinstance(self.db, PostgresStatements):
            with self.db:
                self.db.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended('pskit-run-admission', 0))"
                )
                yield
        else:
            self.db.execute("BEGIN IMMEDIATE")
            with self.db:
                yield

    def _require_not_deleting(self, user_id: str) -> None:
        """Reject a guest whose cleanup has started under the caller's write lock.

        Args:
            user_id: Account to check against the cleanup state.

        Raises:
            GuestAccountDeleting: Cleanup already owns the account.
        """
        if getattr(self, "identity_policy", None) is None:
            return
        row = self.db.execute(
            "SELECT cleanup_state FROM account_tiers WHERE user_id=?", (user_id,),
        ).fetchone()
        if row is not None and row[0] == "deleting":
            raise GuestAccountDeleting
