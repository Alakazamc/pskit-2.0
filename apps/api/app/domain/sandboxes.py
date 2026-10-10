"""Persistent sandbox admission, activity fencing and lifecycle revisions."""

import sqlite3
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

from app.contracts.sandbox import SandboxActivity, SandboxLease, SandboxOperation, SandboxSummary
from app.db.postgres import PostgresDatabase, PostgresStatements


class SandboxConflict(Exception):
    """A stale revision, active session or draining owner prevents admission."""


class SandboxActivityStore:
    """Use row locks so independent manager processes share one runtime truth."""

    def __init__(self, database: PostgresDatabase, *, clock: Callable[[], float] = time.time):
        self.db = PostgresStatements(database)
        self.clock = clock
        self.postgres = True

    @contextmanager
    def _owner_transaction(self, owner_id):
        with self.db:
            if self.postgres:
                self.db.execute(
                    "SELECT owner_id FROM sandbox_owners WHERE owner_id=? FOR UPDATE", (owner_id,)
                )
            yield

    def ensure_owner(self, owner_id, instance_id, volume_id, image_digest):
        with self.db:
            self.db.execute(
                "INSERT INTO sandbox_owners VALUES (?,?,?,?, 'ready','stopped',0,?) "
                "ON CONFLICT(owner_id) DO NOTHING",
                (owner_id, instance_id, volume_id, image_digest, self.clock()),
            )
        return self.owner(owner_id)

    def owner(self, owner_id) -> SandboxSummary | None:
        row = self.db.execute(
            "SELECT owner_id,instance_id,volume_id,image_digest,state,"
            "runtime_state,revision,last_completed_at FROM sandbox_owners "
            "WHERE owner_id=?",
            (owner_id,),
        ).fetchone()
        if row is None:
            return None
        sessions = [
            item[0]
            for item in self.db.execute(
                "SELECT DISTINCT session_id FROM sandbox_leases WHERE owner_id=? AND state<>'released'",
                (owner_id,),
            )
        ]
        return SandboxSummary(
            **dict(
                zip(
                    (
                        "owner_id",
                        "instance_id",
                        "volume_id",
                        "image_digest",
                        "state",
                        "runtime_state",
                        "revision",
                        "last_completed_at",
                    ),
                    row,
                )
            ),
            active_sessions=sessions,
        )

    def list(self) -> list[SandboxSummary]:
        return [
            self.owner(row[0])
            for row in self.db.execute("SELECT owner_id FROM sandbox_owners ORDER BY owner_id")
        ]

    def set_runtime(self, owner_id, state, *, completed=False):
        with self._owner_transaction(owner_id):
            self.db.execute(
                "UPDATE sandbox_owners SET runtime_state=? WHERE owner_id=?", (state, owner_id)
            )
            if completed:
                self.db.execute(
                    "UPDATE sandbox_owners SET last_completed_at=? WHERE owner_id=?",
                    (self.clock(), owner_id),
                )

    def mark_ensured(self, owner_id, *, started=False):
        with self._owner_transaction(owner_id):
            owner = self.owner(owner_id)
            if owner.state != "ready" or owner.runtime_state == "stopping":
                raise SandboxConflict("Sandbox lifecycle changed during ensure")
            self.db.execute(
                "UPDATE sandbox_owners SET runtime_state='running' WHERE owner_id=?", (owner_id,)
            )
            if started:
                self.db.execute(
                    "UPDATE sandbox_owners SET last_completed_at=? WHERE owner_id=?",
                    (self.clock(), owner_id),
                )

    def acquire(
        self, owner_id, session_id, run_id, lease_seconds, *, purpose="pi", continuation_run_id=None
    ) -> SandboxLease:
        if type(lease_seconds) is not int or lease_seconds <= 0:
            raise ValueError("Lease seconds must be positive")
        with self._owner_transaction(owner_id):
            owner = self.owner(owner_id)
            if not owner or owner.runtime_state != "running":
                raise SandboxConflict("Sandbox is not accepting attempts")
            existing = self.db.execute(
                "SELECT run_id,purpose FROM sandbox_leases WHERE owner_id=? AND session_id=? "
                "AND state<>'released'",
                (owner_id, session_id),
            ).fetchall()
            continuing = (
                purpose == "transfer"
                and continuation_run_id is not None
                and existing == [(continuation_run_id, "pi")]
            )
            if owner.state != "ready" and not (owner.state == "draining" and continuing):
                raise SandboxConflict("Sandbox is not accepting attempts")
            if existing and not continuing:
                raise SandboxConflict("Session already has an unresolved attempt")
            token = self.db.execute(
                "SELECT COALESCE(MAX(fencing_token),0)+1 FROM sandbox_leases WHERE owner_id=?",
                (owner_id,),
            ).fetchone()[0]
            lease = SandboxLease(
                lease_id="lease-" + uuid.uuid4().hex,
                owner_id=owner_id,
                session_id=session_id,
                run_id=run_id,
                fencing_token=token,
                lease_seconds=lease_seconds,
                expires_at=self.clock() + lease_seconds,
                purpose=purpose,
            )
            self.db.execute(
                "INSERT INTO sandbox_leases VALUES (?,?,?,?,?,?,?,?,?)",
                tuple(lease.model_dump().values()),
            )
        return lease

    def _lease(self, lease_id):
        row = self.db.execute(
            "SELECT lease_id,owner_id,session_id,run_id,fencing_token,"
            "lease_seconds,expires_at,state,purpose FROM sandbox_leases WHERE lease_id=?",
            (lease_id,),
        ).fetchone()
        if not row:
            raise SandboxConflict("Lease is unavailable")
        return SandboxLease(**dict(zip(SandboxLease.model_fields, row)))

    def renew(self, lease_id, fencing_token) -> SandboxLease:
        lease = self._lease(lease_id)
        with self._owner_transaction(lease.owner_id):
            lease = self._lease(lease_id)
            if lease.fencing_token != fencing_token or lease.state == "released":
                raise SandboxConflict("Stale lease fencing token")
            self.db.execute(
                "UPDATE sandbox_leases SET expires_at=?,state='active' WHERE lease_id=?",
                (self.clock() + lease.lease_seconds, lease_id),
            )
            return self._lease(lease_id)

    def release(self, lease_id, fencing_token) -> None:
        lease = self._lease(lease_id)
        with self._owner_transaction(lease.owner_id):
            lease = self._lease(lease_id)
            if lease.fencing_token != fencing_token:
                raise SandboxConflict("Stale lease fencing token")
            if lease.state != "released":
                self.db.execute(
                    "UPDATE sandbox_leases SET state='released' WHERE lease_id=?", (lease_id,)
                )
                self.db.execute(
                    "UPDATE sandbox_owners SET last_completed_at=? WHERE owner_id=?",
                    (self.clock(), lease.owner_id),
                )

    def activity(self, owner_id) -> SandboxActivity:
        with self._owner_transaction(owner_id):
            owner = self.owner(owner_id)
            if not owner:
                raise SandboxConflict("Sandbox is unavailable")
            self.db.execute(
                "UPDATE sandbox_leases SET state='unknown' WHERE owner_id=? "
                "AND expires_at<=? AND state='active'",
                (owner_id, self.clock()),
            )
            leases = [
                self._lease(row[0])
                for row in self.db.execute(
                    "SELECT lease_id FROM sandbox_leases WHERE owner_id=? AND state<>'released'",
                    (owner_id,),
                )
            ]
            state = (
                "unknown"
                if any(lease.state == "unknown" for lease in leases)
                else ("active" if leases else "idle")
            )
            return SandboxActivity(
                owner_id=owner_id,
                leases=leases,
                state=state,
                last_completed_at=owner.last_completed_at,
            )

    def claim_stop(self, owner_id, idle_seconds) -> bool:
        with self._owner_transaction(owner_id):
            activity = self.activity(owner_id)
            owner = self.owner(owner_id)
            if activity.leases or owner.runtime_state != "running":
                return False
            if self.clock() - activity.last_completed_at < idle_seconds:
                return False
            self.db.execute(
                "UPDATE sandbox_owners SET runtime_state='stopping' WHERE owner_id=?", (owner_id,)
            )
            return True

    def drain(self, owner_id, expected_revision) -> SandboxOperation:
        with self._owner_transaction(owner_id):
            owner = self.owner(owner_id)
            if not owner or owner.revision != expected_revision:
                raise SandboxConflict("Sandbox revision conflict")
            revision = owner.revision + 1
            self.db.execute(
                "UPDATE sandbox_owners SET state='draining',revision=? WHERE owner_id=?",
                (revision, owner_id),
            )
            operation = SandboxOperation(
                operation_id="operation-" + uuid.uuid4().hex,
                owner_id=owner_id,
                kind="drain",
                state="draining",
                revision=revision,
            )
            self.db.execute(
                "INSERT INTO sandbox_operations VALUES (?,?,?,?,?,?)",
                tuple(operation.model_dump().values()),
            )
            return operation

    def pending_replacements(self, owner_id=None):
        query = (
            "SELECT operation_id,owner_id,kind,state,revision,image_digest FROM sandbox_operations "
        )
        query += "WHERE kind='replace' AND state='waiting'"
        if owner_id is not None:
            query += " AND owner_id=?"
        return [
            SandboxOperation(**dict(zip(SandboxOperation.model_fields, row)))
            for row in self.db.execute(query, (owner_id,) if owner_id is not None else ())
        ]

    def replacement(self, owner_id, image_digest) -> SandboxOperation:
        with self._owner_transaction(owner_id):
            owner = self.owner(owner_id)
            if owner and owner.state == "ready" and owner.image_digest == image_digest:
                completed = self.db.execute(
                    "SELECT operation_id,owner_id,kind,state,revision,image_digest "
                    "FROM sandbox_operations WHERE owner_id=? AND kind='replace' AND state='completed' "
                    "AND revision=?",
                    (owner_id, owner.revision),
                ).fetchone()
                if completed:
                    return SandboxOperation(**dict(zip(SandboxOperation.model_fields, completed)))
            if not owner or owner.state not in {"draining", "replacing"}:
                raise SandboxConflict("Drain sandbox before replacement")
            pending = self.db.execute(
                "SELECT operation_id,owner_id,kind,state,revision,image_digest "
                "FROM sandbox_operations WHERE owner_id=? AND kind='replace' AND state='waiting'",
                (owner_id,),
            ).fetchone()
            if pending:
                operation = SandboxOperation(**dict(zip(SandboxOperation.model_fields, pending)))
                if operation.image_digest != image_digest:
                    raise SandboxConflict("A different replacement is already pending")
                return operation
            operation = SandboxOperation(
                operation_id="operation-" + uuid.uuid4().hex,
                owner_id=owner_id,
                kind="replace",
                state="waiting",
                revision=owner.revision,
                image_digest=image_digest,
            )
            self.db.execute(
                "INSERT INTO sandbox_operations VALUES (?,?,?,?,?,?)",
                tuple(operation.model_dump().values()),
            )
            return operation

    def claim_replacement(self, operation):
        with self._owner_transaction(operation.owner_id):
            owner = self.owner(operation.owner_id)
            if (
                owner.state not in {"draining", "replacing"}
                or owner.revision != operation.revision
                or owner.active_sessions
            ):
                raise SandboxConflict("Sandbox replacement revision conflict")
            revision = owner.revision + 1
            self.db.execute(
                "UPDATE sandbox_owners SET state='replacing',revision=? WHERE owner_id=?",
                (revision, operation.owner_id),
            )
            self.db.execute(
                "UPDATE sandbox_operations SET revision=? WHERE operation_id=?",
                (revision, operation.operation_id),
            )
            return operation.model_copy(update={"revision": revision})

    def complete_replacement(self, operation):
        with self._owner_transaction(operation.owner_id):
            owner = self.owner(operation.owner_id)
            if owner.active_sessions or owner.revision != operation.revision:
                raise SandboxConflict("Sandbox changed while replacing")
            self.db.execute(
                "UPDATE sandbox_owners SET image_digest=?,state='ready',"
                "runtime_state='stopped',revision=revision+1 WHERE owner_id=?",
                (operation.image_digest, operation.owner_id),
            )
            self.db.execute(
                "UPDATE sandbox_operations SET state='completed',revision=? WHERE operation_id=?",
                (owner.revision + 1, operation.operation_id),
            )
            return operation.model_copy(
                update={"state": "completed", "revision": owner.revision + 1}
            )


class MemorySandboxActivityStore(SandboxActivityStore):
    """Explicit ephemeral test double; never constructed by the live factory."""

    def __init__(self, *, clock: Callable[[], float] = time.time):
        self.db = sqlite3.connect(":memory:")
        self.clock = clock
        self.postgres = False
        source = Path(__file__).parents[1] / "db/postgres_migrations/005_sandboxes.sql"
        self.db.executescript(source.read_text().replace("bigint", "integer"))


class SandboxArtifactStore:
    """Persist workspace outputs in the existing artifact blob storage with owned references."""

    def __init__(self, statements):
        self.db = statements
        self.storage_limit_for = lambda _owner: None

    @contextmanager
    def _admission_transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def put(self, user_id, session_id, attempt_id, name, content):
        import hashlib

        from app.contracts.catalog import ArtifactRef

        digest = hashlib.sha256(content).hexdigest()
        artifact_id = (
            "sandbox-"
            + hashlib.sha256(
                f"{user_id}:{session_id}:{attempt_id}:{name}:{digest}".encode()
            ).hexdigest()
        )
        with self._admission_transaction():
            existing = self.db.execute(
                "SELECT 1 FROM agent_artifact_blobs WHERE id=?", (artifact_id,)
            ).fetchone()
            limit = self.storage_limit_for(user_id)
            if not existing and limit is not None:
                from app.domain.catalog import InvalidFileUpload

                uploads = self.db.execute(
                    "SELECT COALESCE(SUM(size),0) FROM catalog_files WHERE user_id=?", (user_id,)
                ).fetchone()[0]
                artifacts = self.db.execute(
                    "SELECT COALESCE(SUM(size),0) FROM agent_artifact_blobs WHERE user_id=?",
                    (user_id,),
                ).fetchone()[0]
                if uploads + artifacts + len(content) > limit:
                    raise InvalidFileUpload("STORAGE_QUOTA_EXCEEDED", 413)
            self.db.execute(
                "INSERT INTO agent_artifact_blobs "
                "(id,user_id,job_id,name,kind,size,sha256,content,created_at) VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO NOTHING",
                (
                    artifact_id,
                    user_id,
                    attempt_id,
                    name,
                    "file",
                    len(content),
                    digest,
                    content,
                    str(time.time()),
                ),
            )
            self.db.execute(
                "INSERT INTO sandbox_artifacts VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
                (artifact_id, user_id, session_id, attempt_id, name, digest),
            )
        return ArtifactRef(
            id=artifact_id, name=name, kind="file", available=True, size=len(content), sha256=digest
        )

    def list(self, user_id, session_id=None):
        from app.contracts.catalog import ArtifactRef

        where = "AND a.session_id=?" if session_id is not None else ""
        params = (user_id, user_id, session_id) if session_id is not None else (user_id, user_id)
        return [
            ArtifactRef(
                id=row[0], name=row[1], kind=row[2], available=True, size=row[3], sha256=row[4]
            )
            for row in self.db.execute(
                "SELECT b.id,b.name,b.kind,b.size,b.sha256 "
                "FROM agent_artifact_blobs b JOIN sandbox_artifacts a ON a.id=b.id "
                f"WHERE a.user_id=? AND b.user_id=? {where} ORDER BY b.created_at",
                params,
            )
        ]

    def read(self, user_id, artifact_id):
        row = self.db.execute(
            "SELECT b.name,b.content FROM agent_artifact_blobs b "
            "JOIN sandbox_artifacts a ON a.id=b.id WHERE b.id=? AND a.user_id=? AND b.user_id=?",
            (artifact_id, user_id, user_id),
        ).fetchone()
        return (row[0], bytes(row[1])) if row else None
