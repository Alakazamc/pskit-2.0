"""Service-bound leases, device exclusion, cumulative reports and durable receipts."""

import secrets
import uuid
from datetime import datetime, timedelta

from psycopg.types.json import Jsonb

from app.contracts.compute import ExecutionGrant, GrantUpdate, Pending, UsageReceipt
from app.domain.compute.common import admission_lock, payload_hash, utcnow
from app.domain.compute.jobs import ComputeJobs
from app.domain.compute.metering import validate_report


class ComputeLeases:
    def __init__(
        self,
        database,
        ledger,
        *,
        lease_seconds=60,
        events=None,
        tool_runs=None,
    ):
        self.database, self.ledger = database, ledger
        self.jobs = ComputeJobs(database, ledger)
        self.events = events
        self.jobs.events = events
        self.tool_runs = tool_runs
        self.lease_seconds = lease_seconds

    def _grant(self, connection, job_id):
        row = connection.execute(
            "SELECT j.user_id,j.worker_id,j.attempts,j.lease_token,d.stop_at,j.lease_expires_at "
            "FROM agent_jobs j JOIN compute_job_data d ON d.job_id=j.id WHERE j.id=%s", (job_id,),
        ).fetchone()
        return ExecutionGrant(job=self.jobs.get(row[0], job_id, connection=connection),
                              worker_id=row[1], attempt=row[2], fencing_token=row[3],
                              stop_at=row[4], lease_expires_at=datetime.fromisoformat(row[5]),
                              gpu_uuids=[item[0] for item in connection.execute(
                                  "SELECT gpu_uuid FROM compute_device_leases WHERE job_id=%s "
                                  "ORDER BY gpu_uuid", (job_id,))])

    def _recover_expired(self, connection):
        now = utcnow()
        return connection.execute(
            "UPDATE agent_jobs j SET gpu_accounting_status='pending_reconciliation',"
            "status=CASE WHEN d.stop_at<=%s THEN 'cancelling' ELSE j.status END "
            "FROM compute_job_data d WHERE d.job_id=j.id AND j.status IN ('running','cancelling') "
            "AND (j.lease_expires_at IS NULL OR j.lease_expires_at<=%s OR d.stop_at<=%s) "
            "AND (j.gpu_accounting_status<>'pending_reconciliation' "
            "OR (j.status='running' AND d.stop_at<=%s))",
            (now, now.isoformat(), now, now),
        ).rowcount

    def recover_expired(self):
        """Mark unknown consumption without releasing, reassigning or rerunning work."""
        with self.database.transaction() as connection:
            admission_lock(connection)
            return self._recover_expired(connection)

    def claim(self, payload):
        if len(set(payload.resources.gpu_uuids)) != len(payload.resources.gpu_uuids):
            raise ValueError("INVALID_GPU_UUIDS")
        with self.database.transaction() as connection:
            admission_lock(connection)
            self._recover_expired(connection)
            # Lost claim ACK recovers the same ownership, never creates a second execution.
            prior = connection.execute(
                "SELECT j.id FROM agent_jobs j JOIN compute_job_data d ON d.job_id=j.id "
                "WHERE d.service_id=%s AND j.worker_id=%s AND j.status IN ('running','cancelling') "
                "ORDER BY j.ordinal LIMIT 1", (payload.service_id, payload.worker_id),
            ).fetchone()
            if prior:
                return self._grant(connection, prior[0]).model_copy(update={"recovered": True})
            rows = connection.execute(
                "SELECT j.id,j.user_id FROM agent_jobs j JOIN compute_job_data d ON d.job_id=j.id "
                "WHERE d.service_id=%s AND j.status='queued' ORDER BY j.ordinal "
                "FOR UPDATE OF j SKIP LOCKED", (payload.service_id,),
            ).fetchall()
            for job_id, user_id in rows:
                job = self.jobs.get(user_id, job_id, connection=connection)
                if self.jobs.catalog.get(user_id, job.capability.id, job.capability.version,
                                         connection=connection) is None:
                    # A queued attempt must still have a current execution grant.
                    self.jobs.cancel(user_id, job_id, connection=connection)
                    continue
                user_limit = connection.execute(
                    "SELECT concurrency_limit FROM admin_user_limits WHERE user_id=%s",
                    (user_id,),
                ).fetchone()
                if user_limit:
                    user_active = connection.execute(
                        "SELECT COUNT(*) FROM agent_jobs WHERE user_id=%s "
                        "AND status IN ('running','cancelling')", (user_id,),
                    ).fetchone()[0]
                    if user_active >= user_limit[0]:
                        continue
                active = connection.execute(
                    "SELECT COUNT(*) FROM agent_jobs j JOIN compute_job_data d ON d.job_id=j.id "
                    "WHERE j.status IN ('running','cancelling') AND d.capability_json->>'id'=%s",
                    (job.capability.id,),
                ).fetchone()[0]
                if active >= job.capability.concurrency:
                    continue
                free = [gpu for gpu in payload.resources.gpu_uuids if not connection.execute(
                    "SELECT 1 FROM compute_device_leases WHERE gpu_uuid=%s", (gpu,),
                ).fetchone()]
                if len(free) < job.capability.gpu_count:
                    continue
                token, now = secrets.token_urlsafe(32), utcnow()
                connection.execute(
                    "UPDATE agent_jobs SET status='running',worker_id=%s,attempts=attempts+1,"
                    "lease_token=%s,first_claimed_at=%s,lease_expires_at=%s WHERE id=%s",
                    (payload.worker_id, token, now.isoformat(),
                     (now+timedelta(seconds=self.lease_seconds)).isoformat(), job_id),
                )
                connection.execute("UPDATE compute_job_data SET stop_at=%s WHERE job_id=%s",
                                   (now+timedelta(seconds=job.capability.max_execution_seconds), job_id))
                for gpu in free[:job.capability.gpu_count]:
                    connection.execute("INSERT INTO compute_device_leases VALUES (%s,%s)", (gpu, job_id))
                if self.events is not None:
                    self.events.append(
                        job_id,
                        "run.started",
                        {"job_id": job_id, "worker_id": payload.worker_id},
                        connection=connection,
                        key="run:started",
                    )
                    self.events.append(
                        job_id,
                        "stage.started",
                        {"job_id": job_id, "worker_id": payload.worker_id},
                        connection=connection,
                        key="stage:started",
                    )
                return self._grant(connection, job_id)
        return None

    def _owned(self, connection, service_id, job_id, payload):
        row = connection.execute(
            "SELECT j.user_id,j.worker_id,j.attempts,j.lease_token,d.service_id,j.status "
            "FROM agent_jobs j JOIN compute_job_data d ON d.job_id=j.id "
            "WHERE j.id=%s FOR UPDATE OF j", (job_id,),
        ).fetchone()
        if not row or row[4] != service_id:
            raise LookupError("JOB_NOT_FOUND")
        if row[1:4] != (payload.worker_id, payload.attempt, payload.fencing_token):
            raise ValueError("LEASE_NOT_OWNED")
        return self.jobs.get(row[0], job_id, connection=connection)

    def heartbeat(self, service_id, job_id, payload):
        with self.database.transaction() as connection:
            admission_lock(connection)
            job = self._owned(connection, service_id, job_id, payload)
            if job.status not in {"running", "cancelling"}:
                raise ValueError("JOB_ALREADY_FINAL")
            grant = self._grant(connection, job_id)
            now = utcnow()
            overdue = now >= grant.stop_at
            if payload.usage is not None:
                self._validate_window(grant, payload.window)
                self.ledger.accept_usage(connection, job_id, payload.seq, payload.usage,
                                         window=payload.window)
            expiry = now+timedelta(seconds=self.lease_seconds)
            connection.execute("UPDATE agent_jobs SET lease_expires_at=%s,progress=GREATEST(progress,%s),"
                               "status=%s,gpu_accounting_status=%s WHERE id=%s",
                               (expiry.isoformat(), payload.progress,
                                "cancelling" if overdue else job.status,
                                "pending_reconciliation" if overdue else job.accounting_status, job_id))
            if self.events is not None:
                self.events.append(
                    job_id,
                    "stage.progress",
                    {"job_id": job_id, "progress": payload.progress},
                    connection=connection,
                    key=f"heartbeat:{payload.seq}:progress",
                )
                if payload.usage is not None:
                    self.events.append(
                        job_id,
                        "usage.updated",
                        {"job_id": job_id, "usage": payload.usage.model_dump(mode="json")},
                        connection=connection,
                        key=f"heartbeat:{payload.seq}:usage",
                    )
            return GrantUpdate(stop_at=grant.stop_at, lease_expires_at=expiry,
                               cancel_requested=overdue or job.status == "cancelling")

    @staticmethod
    def _validate_window(grant, window):
        if window is not None:
            # Reports cannot expand an accounting window into unrelated days.
            start = grant.stop_at-timedelta(seconds=grant.job.capability.max_execution_seconds)
            if window.start < start-timedelta(seconds=5) or window.end > utcnow()+timedelta(seconds=5):
                raise ValueError("INVALID_USAGE_WINDOW")

    def complete(self, service_id, job_id, payload):
        data = payload.model_dump(mode="json")
        digest = payload_hash(data)
        with self.database.transaction() as connection:
            admission_lock(connection)
            job = self._owned(connection, service_id, job_id, payload)
            prior = connection.execute("SELECT payload_hash,receipt_json FROM compute_result_receipts "
                                       "WHERE job_id=%s AND seq=%s", (job_id, payload.seq)).fetchone()
            if prior:
                if prior[0] != digest:
                    raise ValueError("RESULT_CONFLICT")
                return UsageReceipt.model_validate(prior[1])
            if job.status not in {"running", "cancelling"}:
                raise ValueError("JOB_ALREADY_FINAL")
            latest = connection.execute("SELECT latest_seq FROM compute_job_data WHERE job_id=%s",
                                        (job_id,)).fetchone()[0]
            if payload.seq <= latest:
                raise ValueError("RESULT_SEQUENCE_CONFLICT")
            if isinstance(job.report, Pending) and (
                payload.report.job_id != job.report.job_id
            ):
                raise ValueError("EXTERNAL_JOB_CONFLICT")
            validate_report(payload.report, job.capability)
            grant = self._grant(connection, job_id)
            self._validate_window(grant, payload.window)
            if not isinstance(payload.report, Pending):
                if not payload.stopped:
                    raise ValueError("STOP_NOT_CONFIRMED")
                self.ledger.accept_usage(connection, job_id, payload.seq, payload.report.usage,
                                         window=payload.window, terminal=True)
                status = "cancelled" if job.status == "cancelling" else payload.report.status
                connection.execute("UPDATE agent_jobs SET status=%s,progress=100,"
                                   "gpu_accounting_status='settled' WHERE id=%s", (status, job_id))
                connection.execute("DELETE FROM compute_device_leases WHERE job_id=%s", (job_id,))
                connection.execute("INSERT INTO compute_outbox (job_id,run_id) VALUES (%s,%s) "
                                   "ON CONFLICT(job_id) DO NOTHING", (job_id, job.run_id))
                if self.events is not None:
                    for artifact in payload.report.artifacts:
                        self.events.append(
                            job_id,
                            "artifact.created",
                            {"job_id": job_id, "artifact": artifact.model_dump(mode="json")},
                            connection=connection,
                            key=f"result:{payload.seq}:artifact:{artifact.id}",
                        )
                    self.events.append(
                        job_id,
                        "usage.updated",
                        {"job_id": job_id, "usage": payload.report.usage.model_dump(mode="json")},
                        connection=connection,
                        key=f"result:{payload.seq}:usage",
                    )
                    self.events.append(
                        job_id,
                        "stage.completed",
                        {"job_id": job_id, "status": status},
                        connection=connection,
                        key=f"result:{payload.seq}:terminal",
                    )
                if self.tool_runs is not None:
                    self.tool_runs.advance_completed_steps(
                        job_id,
                        payload.report,
                        status,
                        connection=connection,
                    )
            else:
                status = "pending"
            connection.execute("UPDATE compute_job_data SET report_json=%s,latest_seq=%s WHERE job_id=%s",
                               (Jsonb(payload.report.model_dump(mode="json")), payload.seq, job_id))
            receipt = UsageReceipt(receipt_id=uuid.uuid4().hex, job_id=job_id,
                                   accepted_seq=payload.seq, payload_hash=digest, status=status)
            connection.execute("INSERT INTO compute_result_receipts VALUES (%s,%s,%s,%s)",
                               (job_id, payload.seq, digest, Jsonb(receipt.model_dump(mode="json"))))
        # Returned only after transaction committed successfully.
        return receipt
