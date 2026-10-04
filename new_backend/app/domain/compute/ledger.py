"""Integer resource admission and idempotent cumulative settlement in PostgreSQL."""

from datetime import UTC

from psycopg.types.json import Jsonb

from app.contracts.compute import CapabilityVersion, ComputeUsage, ResourceCounter
from app.domain.compute.common import admission_lock, payload_hash, utcnow
from app.domain.compute.metering import daily_slices, validate_usage


class ComputeLedger:
    def __init__(self, database, *, cpu_daily_limit_ms=0, gpu_daily_limit_ms=0, gpu_limit_for=None):
        if min(cpu_daily_limit_ms, gpu_daily_limit_ms) < 0:
            raise ValueError("Resource limits cannot be negative")
        self.database = database
        self.cpu_daily_limit_ms = cpu_daily_limit_ms
        self.gpu_daily_limit_ms = gpu_daily_limit_ms
        self.gpu_limit_for = gpu_limit_for

    def reserve(self, connection, user_id, job_id, budget, now):
        admission_lock(connection)
        usage = self.usage_for(user_id, now=now, connection=connection)
        if budget.cpu_core_ms > usage.cpu.remaining:
            raise ValueError("CPU_QUOTA_EXCEEDED")
        if budget.gpu_device_ms > usage.gpu.remaining:
            raise ValueError("GPU_QUOTA_EXCEEDED")
        connection.execute("INSERT INTO compute_reservations VALUES (%s,%s,%s,%s,%s,true)",
                           (job_id, user_id, now.date().isoformat(), budget.cpu_core_ms,
                            budget.gpu_device_ms))

    def extend(self, job_id, budget):
        """Increase a hold under the same admission lock; never reset deadline."""
        with self.database.transaction() as connection:
            admission_lock(connection)
            row = connection.execute("SELECT user_id,cpu_ms,gpu_ms FROM compute_reservations "
                                     "WHERE job_id=%s AND active FOR UPDATE", (job_id,)).fetchone()
            if not row:
                raise ValueError("RESERVATION_NOT_ACTIVE")
            usage = self.usage_for(row[0], connection=connection)
            cpu_delta, gpu_delta = budget.cpu_core_ms-row[1], budget.gpu_device_ms-row[2]
            if cpu_delta < 0 or gpu_delta < 0:
                raise ValueError("RESERVATION_DECREASED")
            if cpu_delta > usage.cpu.remaining or gpu_delta > usage.gpu.remaining:
                raise ValueError("RESOURCE_QUOTA_EXCEEDED")
            connection.execute("UPDATE compute_reservations SET cpu_ms=%s,gpu_ms=%s WHERE job_id=%s",
                               (budget.cpu_core_ms, budget.gpu_device_ms, job_id))

    def release(self, connection, job_id):
        connection.execute("UPDATE compute_reservations SET active=false WHERE job_id=%s", (job_id,))

    def accept_usage(self, connection, job_id, seq, usage, *, window=None, terminal=False):
        admission_lock(connection)
        row = connection.execute("SELECT user_id,period FROM compute_reservations WHERE job_id=%s "
                                 "FOR UPDATE", (job_id,)).fetchone()
        if row is None:
            raise ValueError("RESERVATION_NOT_FOUND")
        owner, period = row
        snapshot = connection.execute("SELECT capability_json FROM compute_job_data WHERE job_id=%s",
                                      (job_id,)).fetchone()[0]
        validate_usage(usage, CapabilityVersion.model_validate(snapshot), terminal=terminal)
        data = {"usage": usage.model_dump(mode="json"), "window": window.model_dump(mode="json")
                if window else None, "terminal": terminal}
        digest = payload_hash(data)
        prior = connection.execute("SELECT seq,payload_hash,usage_json,window_json,terminal "
                                   "FROM compute_usage_reports WHERE job_id=%s ORDER BY seq DESC LIMIT 1",
                                   (job_id,)).fetchone()
        if prior and seq <= prior[0]:
            existing = connection.execute("SELECT payload_hash FROM compute_usage_reports "
                                          "WHERE job_id=%s AND seq=%s", (job_id, seq)).fetchone()
            if not existing or existing[0] != digest:
                raise ValueError("USAGE_CONFLICT")
            return
        if prior:
            if prior[4]:
                raise ValueError("USAGE_ALREADY_FINAL")
            for metric in ("cpu_core_ms", "gpu_device_ms", "wall_ms", "peak_memory_bytes",
                           "peak_gpu_memory_bytes"):
                old, new = prior[2].get(metric), getattr(usage, metric)
                if old is not None and (new is None or new < old):
                    raise ValueError("USAGE_DECREASED")
            # Cumulative windows have one fixed start; expanding end re-apportions the total.
            if prior[3] and (window is None or window.start.isoformat() != prior[3]["start"]):
                raise ValueError("USAGE_WINDOW_CHANGED")
        connection.execute("INSERT INTO compute_usage_reports VALUES (%s,%s,%s,%s,%s,%s)",
                           (job_id, seq, digest, Jsonb(data["usage"]), Jsonb(data["window"])
                            if window else None, terminal))
        cpu = daily_slices(usage.cpu_core_ms, window, period)
        gpu = daily_slices(usage.gpu_device_ms, window, period)
        connection.execute("DELETE FROM compute_usage_daily WHERE job_id=%s", (job_id,))
        for day in cpu.keys() | gpu.keys():
            connection.execute("INSERT INTO compute_usage_daily VALUES (%s,%s,%s,%s,%s,%s)",
                               (job_id, owner, day, cpu.get(day), gpu.get(day), usage.source))
        if terminal:
            self.release(connection, job_id)

    def usage_for(self, user_id, *, now=None, connection=None):
        now = (now or utcnow()).astimezone(UTC)
        if connection is None:
            with self.database.connection() as conn:
                return self.usage_for(user_id, now=now, connection=conn)
        day = now.date().isoformat()
        used = connection.execute("SELECT COALESCE(SUM(cpu_ms),0),COALESCE(SUM(gpu_ms),0) "
                                  "FROM compute_usage_daily WHERE user_id=%s AND day=%s",
                                  (user_id, day)).fetchone()
        held = connection.execute(
            "SELECT COALESCE(SUM(GREATEST(r.cpu_ms-COALESCE(u.cpu_ms,0),0)),0),"
            "COALESCE(SUM(GREATEST(r.gpu_ms-COALESCE(u.gpu_ms,0),0)),0) "
            "FROM compute_reservations r LEFT JOIN (SELECT job_id,SUM(cpu_ms) cpu_ms,"
            "SUM(gpu_ms) gpu_ms FROM compute_usage_daily GROUP BY job_id) u USING(job_id) "
            "WHERE r.user_id=%s AND r.active", (user_id,),
        ).fetchone()
        legacy = connection.execute(
            "SELECT COALESCE(SUM(CASE WHEN gpu_accounting_status IN ('settled','reconciled') "
            "AND substr(created_at,1,10)=%s THEN actual_minutes*60000 ELSE 0 END),0),"
            "COALESCE(SUM(CASE WHEN gpu_accounting_status IN ('reserved','pending_reconciliation') "
            "THEN estimated_minutes*60000 ELSE 0 END),0),COUNT(*) FROM agent_jobs "
            "WHERE user_id=%s AND resource_requirements_json::jsonb->>'capability'='af3'",
            (day, user_id),
        ).fetchone()
        cpu_limit = connection.execute("SELECT limit_ms FROM compute_cpu_limits WHERE user_id=%s",
                                       (user_id,)).fetchone()
        gpu_limit = connection.execute("SELECT limit_value FROM agent_gpu_limits WHERE user_id=%s",
                                       (user_id,)).fetchone()
        cpu_max = cpu_limit[0] if cpu_limit else self.cpu_daily_limit_ms
        gpu_max = (self.gpu_limit_for(user_id)*60000 if self.gpu_limit_for else
                   gpu_limit[0]*60000 if gpu_limit else self.gpu_daily_limit_ms)
        sources = {row[0] for row in connection.execute(
            "SELECT DISTINCT source FROM compute_usage_daily WHERE user_id=%s AND day=%s", (user_id, day),
        )}
        if legacy[2]:
            sources.add("legacy_wall")
        def counter(limit, used, reserved):
            used, reserved = int(used), int(reserved)
            return ResourceCounter(limit=limit, used=used, reserved=reserved,
                                   remaining=max(0, limit-used-reserved))
        return ComputeUsage(day=day, cpu=counter(cpu_max, used[0], held[0]),
                            gpu=counter(gpu_max, used[1]+legacy[0], held[1]+legacy[1]),
                            sources=sorted(sources))
