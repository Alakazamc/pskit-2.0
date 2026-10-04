"""Business quota, cancellation and reconciliation operations with atomic audit."""

import json
import sqlite3
import uuid

from app.contracts.admin import AdminJob, AdminOperation, AdminUser
from app.contracts.compute import ResourceCounter
from app.domain.admin.roles import RevisionConflict


class AdminOperations:
    def __init__(
        self,
        store,
        compute_jobs=None,
        compute_ledger=None,
        sandbox_operations=None,
        *,
        default_concurrency_limit=2,
    ):
        self.store = store
        self.default_concurrency_limit = default_concurrency_limit
        self.compute_jobs = compute_jobs
        self.compute_ledger = compute_ledger or (compute_jobs.ledger if compute_jobs else None)
        self.sandbox_operations = sandbox_operations

    def user(self, user_id):
        policy = self.store.identity_policy
        if policy and policy.last_seen_for(user_id) is None:
            raise LookupError("USER_NOT_FOUND")
        usage = self.store.quotas.usage_for(user_id)
        row = self.store.db.execute(
            "SELECT cpu_daily_core_ms,concurrency_limit,storage_limit_bytes FROM admin_user_limits WHERE user_id=?",
            (user_id,),
        ).fetchone()
        cpu_max, concurrency, storage = row if row else (0, self.default_concurrency_limit, None)
        cpu = ResourceCounter(limit=cpu_max, used=0, reserved=0, remaining=cpu_max)
        gpu = ResourceCounter(
            limit=usage.gpu.limit,
            used=usage.gpu.used,
            reserved=usage.gpu.reserved,
            remaining=usage.gpu.remaining,
        )
        if self.compute_ledger:
            combined = self.compute_ledger.usage_for(user_id)
            cpu = combined.cpu
            # The legacy GPU table is minutes; managed operations expose minutes too.
            gpu = ResourceCounter(
                limit=combined.gpu.limit // 60000,
                used=(combined.gpu.used + 59999) // 60000,
                reserved=(combined.gpu.reserved + 59999) // 60000,
                remaining=combined.gpu.remaining // 60000,
            )
        return AdminUser(
            user_id=user_id,
            revision=self.store.revision("limits", user_id),
            tier=policy.tier_for(user_id) if policy else "member",
            token_monthly_limit=usage.tokens.limit,
            gpu_daily_minutes=usage.gpu.limit,
            cpu_daily_core_ms=cpu.limit,
            concurrency_limit=concurrency,
            storage_limit_bytes=storage,
            tokens=ResourceCounter(
                limit=usage.tokens.limit,
                used=usage.tokens.used,
                reserved=usage.tokens.reserved,
                remaining=usage.tokens.remaining,
            ),
            gpu=gpu,
            cpu=cpu,
        )

    def users(self, *, limit=50, cursor=None):
        if self.store.identity_policy is None:
            return {"items": [], "next_cursor": None}
        rows = self.store.identity_policy.db.execute(
            "SELECT user_id FROM account_tiers WHERE user_id>? ORDER BY user_id LIMIT ?",
            (cursor or "", limit + 1),
        ).fetchall()
        items = [self.user(r[0]) for r in rows[:limit]]
        return {"items": items, "next_cursor": items[-1].user_id if len(rows) > limit else None}

    def update_limits(
        self, actor, user_id, payload, *, request_id=None, preserve_resource_limits=False
    ):
        """Keep usage and holds unchanged while committing all limit fields and audit."""
        with self.store.transaction():
            before = self.user(user_id)
            revision = self.store.advance("limits", user_id, payload.expected_revision)
            if not preserve_resource_limits:
                self.store.db.execute(
                    "INSERT INTO admin_user_limits VALUES (?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET cpu_daily_core_ms=excluded.cpu_daily_core_ms,concurrency_limit=excluded.concurrency_limit,storage_limit_bytes=excluded.storage_limit_bytes",
                    (
                        user_id,
                        payload.cpu_daily_core_ms,
                        payload.concurrency_limit,
                        payload.storage_limit_bytes,
                    ),
                )
            persistent = getattr(self.store.quotas, "db", None) is not None
            if persistent:
                for table, value in [
                    ("agent_token_limits", payload.token_monthly_limit),
                    ("agent_gpu_limits", payload.gpu_daily_minutes),
                ]:
                    self.store.db.execute(
                        f"INSERT INTO {table}(user_id,limit_value) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET limit_value=excluded.limit_value",
                        (user_id, value),
                    )
            if self.store.database and not preserve_resource_limits:
                self.store.db.execute(
                    "INSERT INTO compute_cpu_limits VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET limit_ms=excluded.limit_ms",
                    (user_id, payload.cpu_daily_core_ms),
                )

            def changed(counter, limit):
                return counter.model_copy(
                    update={
                        "limit": limit,
                        "remaining": max(0, limit - counter.used - counter.reserved),
                    }
                )

            after = before.model_copy(
                update={
                    "revision": revision,
                    "token_monthly_limit": payload.token_monthly_limit,
                    "gpu_daily_minutes": payload.gpu_daily_minutes,
                    "cpu_daily_core_ms": payload.cpu_daily_core_ms,
                    "concurrency_limit": payload.concurrency_limit,
                    "storage_limit_bytes": before.storage_limit_bytes
                    if preserve_resource_limits
                    else payload.storage_limit_bytes,
                    "tokens": changed(before.tokens, payload.token_monthly_limit),
                    "gpu": changed(before.gpu, payload.gpu_daily_minutes),
                    "cpu": changed(before.cpu, payload.cpu_daily_core_ms),
                }
            )
            self.store.audit(
                actor.user_id,
                "quotas:write",
                user_id,
                payload.reason,
                before.model_dump(mode="json"),
                after.model_dump(mode="json"),
                request_id,
            )
        if not persistent:
            # Mock ledger has no durable bills; only apply after the audit committed.
            self.store.quotas.set_token_limit(user_id, payload.token_monthly_limit)
            self.store.quotas.set_gpu_limit(user_id, payload.gpu_daily_minutes)
        return after

    def job(self, job_id, *, for_update=False):
        if self.store.database:
            row = self.store.db.execute(
                "SELECT j.user_id,j.status,j.gpu_accounting_status,j.progress,d.service_id,d.capability_json FROM agent_jobs j LEFT JOIN compute_job_data d ON d.job_id=j.id WHERE j.id=?"
                + (" FOR UPDATE OF j" if for_update else ""),
                (job_id,),
            ).fetchone()
        else:
            # Legacy SQLite contains AF3 jobs and remains an operational source.
            try:
                row = self.store.db.execute(
                    "SELECT user_id,status,gpu_accounting_status,progress,NULL,NULL FROM agent_jobs WHERE id=?",
                    (job_id,),
                ).fetchone()
            except sqlite3.OperationalError:
                row = None
        if not row:
            raise LookupError("JOB_NOT_FOUND")
        capability = row[5]
        if isinstance(capability, str):
            capability = json.loads(capability)
        status = row[1]
        return AdminJob(
            job_id=job_id,
            user_id=row[0],
            status=status,
            accounting_status=row[2],
            progress=row[3],
            service_id=row[4],
            capability_id=capability.get("id") if capability else "af3",
            revision=self.store.revision("job", job_id),
            cancellation_state="confirmed"
            if status == "cancelled" and row[2] != "pending_reconciliation"
            else "requested"
            if status == "cancelling"
            or status == "cancelled"
            and row[2] == "pending_reconciliation"
            else "none",
        )

    def jobs(self, actor, *, limit=50, cursor=None, reconciliation=False):
        if self.store.database is None and getattr(self.store.quotas, "db", None) is None:
            return {"items": [], "next_cursor": None}
        status = " AND gpu_accounting_status='pending_reconciliation'" if reconciliation else ""
        ids = [
            r[0]
            for r in self.store.db.execute(
                "SELECT id FROM agent_jobs WHERE id>?" + status + " ORDER BY id", (cursor or "",)
            )
        ]
        items = []
        for job_id in ids:
            job = self.job(job_id)
            if (
                "service_maintainer" in actor.roles
                and not ({"platform_admin", "auditor", "quota_operator"} & set(actor.roles))
                and job.service_id not in actor.service_ids
            ):
                continue
            items.append(job)
            if len(items) > limit:
                break
        return {
            "items": items[:limit],
            "next_cursor": items[limit - 1].job_id if len(items) > limit else None,
        }

    def cancel(self, actor, job_id, payload, *, request_id=None):
        with self.store.transaction():
            before = self.job(job_id, for_update=True)
            if before.revision != payload.expected_revision:
                raise RevisionConflict("REVISION_CONFLICT")
            if (
                before.status in {"completed", "failed", "cancelled"}
                and before.accounting_status != "pending_reconciliation"
            ):
                raise ValueError("JOB_ALREADY_FINAL")
            if before.service_id and self.compute_jobs:
                self.compute_jobs.cancel(before.user_id, job_id, connection=self.store.db)
            elif before.service_id is None and before.capability_id == "af3":
                self.store.quotas.cancel_af3_job(before.user_id, job_id, connection=self.store.db)
            else:
                raise ValueError("JOB_CANCEL_UNAVAILABLE")
            after = self.job(job_id)
            self.store.audit(
                actor.user_id,
                "jobs:cancel",
                job_id,
                payload.reason,
                before.model_dump(mode="json"),
                after.model_dump(mode="json"),
                request_id,
            )
            return AdminOperation(
                operation_id=uuid.uuid4().hex,
                resource_id=job_id,
                kind="cancel",
                state="confirmed" if after.cancellation_state == "confirmed" else "requested",
                revision=after.revision,
            )

    def reconcile(self, actor, job_id, payload, *, request_id=None):
        if not self.compute_ledger or not self.compute_jobs:
            raise RuntimeError("COMPUTE_REGISTRY_REQUIRES_POSTGRES")
        with self.store.transaction():
            before = self.job(job_id)
            if not before.service_id:
                raise ValueError("LEGACY_JOB_USE_AF3_RECONCILIATION")
            if before.accounting_status != "pending_reconciliation":
                raise ValueError("JOB_RECONCILIATION_NOT_PENDING")
            if before.revision != payload.expected_revision:
                raise RevisionConflict("REVISION_CONFLICT")
            if (
                before.status in {"completed", "failed", "cancelled"}
                and before.accounting_status != "pending_reconciliation"
            ):
                raise ValueError("JOB_ALREADY_FINAL")
            job = self.compute_jobs.get(before.user_id, job_id, connection=self.store.db)
            latest = self.store.db.execute(
                "SELECT COALESCE(MAX(seq),0) FROM compute_usage_reports WHERE job_id=?", (job_id,)
            ).fetchone()[0]
            self.compute_ledger.accept_usage(
                self.store.db, job_id, latest + 1, payload.usage, terminal=True
            )
            self.store.db.execute(
                "UPDATE agent_jobs SET status=?,gpu_accounting_status='settled',progress=100 WHERE id=?",
                (payload.terminal_status, job_id),
            )
            self.store.db.execute("DELETE FROM compute_device_leases WHERE job_id=?", (job_id,))
            self.store.db.execute(
                "INSERT INTO compute_outbox(job_id,run_id) VALUES (?,?) ON CONFLICT(job_id) DO NOTHING",
                (job_id, job.run_id),
            )
            after = self.job(job_id)
            self.store.audit(
                actor.user_id,
                "usage:reconcile",
                job_id,
                payload.reason,
                {**before.model_dump(mode="json")},
                {
                    **after.model_dump(mode="json"),
                    "usage": payload.usage.model_dump(mode="json"),
                    "evidence": payload.evidence,
                },
                request_id,
            )
            return after

    def reconcile_legacy_af3(self, actor, job_id, payload, *, request_id=None):
        """Settle native legacy minutes after the operator attests terminal stop evidence."""
        with self.store.transaction():
            before = self.job(job_id, for_update=True)
            if before.service_id is not None or before.capability_id != "af3":
                raise ValueError("LEGACY_AF3_JOB_REQUIRED")
            if before.revision != payload.expected_revision:
                raise RevisionConflict("REVISION_CONFLICT")
            if before.accounting_status != "pending_reconciliation":
                raise ValueError("JOB_RECONCILIATION_NOT_PENDING")
            if before.status not in {"completed", "failed", "cancelled"}:
                raise ValueError("LEGACY_AF3_JOB_NOT_TERMINAL")
            self.store.quotas.reconcile_af3_gpu_usage(
                job_id, payload.actual_gpu_minutes, payload.reason, connection=self.store.db
            )
            after = self.job(job_id)
            self.store.audit(
                actor.user_id,
                "usage:reconcile-af3",
                job_id,
                payload.reason,
                before.model_dump(mode="json"),
                {
                    **after.model_dump(mode="json"),
                    "actual_gpu_minutes": payload.actual_gpu_minutes,
                    "source": payload.source,
                    "stopped": payload.stopped,
                    "evidence": payload.evidence,
                },
                request_id,
            )
            return after
