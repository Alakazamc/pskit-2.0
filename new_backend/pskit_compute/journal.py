"""Local WAL execution journal. Central PostgreSQL remains the quota authority."""

import fcntl
import os
import sqlite3
from pathlib import Path

from app.contracts.compute import ComputeResultRequest, ExecutionGrant, Pending
from app.domain.compute.common import payload_hash


class Journal:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._lock = open(f"{path}.lock", "a+b")  # noqa: SIM115 - Owned for the journal lifetime.
        os.chmod(f"{path}.lock", 0o600)
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock.close()
            raise ValueError("JOURNAL_ALREADY_OWNED") from None
        self.db = sqlite3.connect(path)
        os.chmod(path, 0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        with self.db:
            self.db.execute("CREATE TABLE IF NOT EXISTS executions (job_id TEXT PRIMARY KEY, "
                            "state TEXT NOT NULL, grant_json TEXT NOT NULL, payload_json TEXT, "
                            "pending_json TEXT, seq INTEGER NOT NULL DEFAULT 0, progress INTEGER NOT NULL DEFAULT 0)")

    def begin(self, grant):
        with self.db:
            self.db.execute("INSERT INTO executions(job_id,state,grant_json) VALUES (?,'executing',?)",
                            (grant.job.id, grant.model_dump_json()))

    def progress(self, job_id, seq, progress):
        with self.db:
            self.db.execute("UPDATE executions SET seq=?,progress=MAX(progress,?) WHERE job_id=?",
                            (seq, progress, job_id))

    def record(self, grant, payload):
        with self.db:
            changed = self.db.execute("UPDATE executions SET state='outbox',payload_json=?,seq=?,"
                "pending_json=COALESCE(?,pending_json) WHERE job_id=?",
                (payload.model_dump_json(), payload.seq, payload.report.model_dump_json()
                 if isinstance(payload.report, Pending) else None, grant.job.id)).rowcount
            if not changed:
                raise ValueError("EXECUTION_NOT_JOURNALED")

    def acknowledge(self, receipt):
        row = self.db.execute("SELECT payload_json FROM executions WHERE job_id=? AND state='outbox'",
                              (receipt.job_id,)).fetchone()
        payload = ComputeResultRequest.model_validate_json(row[0]) if row else None
        expected_status = "pending" if payload and isinstance(payload.report, Pending) else None
        if (payload is None or payload.seq != receipt.accepted_seq or not receipt.committed
                or payload_hash(payload.model_dump(mode="json")) != receipt.payload_hash
                or (expected_status is not None and receipt.status != expected_status)
                or (expected_status is None and receipt.status == "pending")):
            raise ValueError("RECEIPT_MISMATCH")
        with self.db:
            if receipt.status == "pending":
                self.db.execute("UPDATE executions SET state='waiting',payload_json=NULL WHERE job_id=?",
                                (receipt.job_id,))
            else:
                self.db.execute("DELETE FROM executions WHERE job_id=?", (receipt.job_id,))

    def recover(self):
        return [{"grant": ExecutionGrant.model_validate_json(grant), "state": state,
                 "payload": ComputeResultRequest.model_validate_json(payload) if payload else None,
                 "pending": Pending.model_validate_json(pending) if pending else None,
                 "seq": seq, "progress": progress}
                for state, grant, payload, pending, seq, progress in self.db.execute(
                    "SELECT state,grant_json,payload_json,pending_json,seq,progress FROM executions ORDER BY rowid")]

    def close(self):
        self.db.close()
        self._lock.close()
