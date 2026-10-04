"""Usage persistence methods for the conversation store."""

from datetime import UTC, datetime, timedelta

from app.contracts.models import GpuQuota, QuotaCounter, UsageEntry, UsageSnapshot
from app.domain.quota import TokenQuotaExceeded

from .common import current_time as _now


class UsageMixin:
    """Account for monthly Tokens and daily AF3 GPU minutes durably."""

    def _append_token_entry(
        self, user_id: str, period: str, kind: str, amount: int, run_id: str | None,
        status: str = "posted",
    ) -> None:
        """Append a Token ledger row within the caller's transaction."""
        self.db.execute(
            "INSERT INTO agent_token_entries (user_id,period,kind,amount,run_id,created_at,status) "
            "VALUES (?,?,?,?,?,?,?)",
            (user_id, period, kind, amount, run_id, _now().isoformat(), status),
        )

    def _gpu_limit_for(self, user_id: str) -> int:
        """Read a user's explicit GPU limit or the identity tier default."""
        row = self.db.execute(
            "SELECT limit_value FROM agent_gpu_limits WHERE user_id=?", (user_id,)
        ).fetchone()
        if row:
            return row[0]
        policy = getattr(self, "identity_policy", None)
        return policy.default_gpu_limit_for(user_id) if policy else 60

    def _token_limit_for(self, user_id: str) -> int:
        """Read a user's explicit Token limit or the identity tier default."""
        row = self.db.execute(
            "SELECT limit_value FROM agent_token_limits WHERE user_id=?", (user_id,)
        ).fetchone()
        if row:
            return row[0]
        policy = getattr(self, "identity_policy", None)
        return policy.default_token_limit_for(user_id) if policy else 1_000_000

    def _gpu_usage_values(self, user_id: str, day: str) -> tuple[int, int]:
        """Sum this day's settled usage and every still-unsettled GPU hold."""
        actual = self.db.execute(
            "SELECT COALESCE(SUM(actual_minutes),0) FROM agent_jobs "
            "WHERE user_id=? AND substr(created_at,1,10)=? "
            "AND gpu_accounting_status IN ('settled','reconciled')",
            (user_id, day),
        ).fetchone()[0]
        reserved = self.db.execute(
            "SELECT COALESCE(SUM(estimated_minutes),0) FROM agent_jobs "
            "WHERE user_id=? "
            "AND gpu_accounting_status IN ('reserved','pending_reconciliation')",
            (user_id,),
        ).fetchone()[0]
        if getattr(self, "database", None) is not None:
            # Generic entries use milliseconds; legacy admission rounds only its display
            # projection, so fractions cannot bypass the old minute-based quota gate.
            usage = self.db.execute(
                "SELECT COALESCE(SUM(gpu_ms),0) FROM compute_usage_daily "
                "WHERE user_id=? AND day=?", (user_id, day),
            ).fetchone()[0]
            held = self.db.execute(
                "SELECT COALESCE(SUM(GREATEST(r.gpu_ms-COALESCE(u.used,0),0)),0) "
                "FROM compute_reservations r LEFT JOIN (SELECT job_id,SUM(gpu_ms) used "
                "FROM compute_usage_daily GROUP BY job_id) u USING(job_id) "
                "WHERE r.user_id=? AND r.active", (user_id,),
            ).fetchone()[0]
            actual += (int(usage)+59999)//60000
            reserved += (int(held)+59999)//60000
        return actual, reserved

    def usage_for(self, user_id: str) -> UsageSnapshot:
        """Return current monthly Token and daily GPU quota counters.

        Token holds are included in ``used`` until settled. GPU reservations
        are shown separately from settled minutes.

        Args:
            user_id: Owner whose current quota is read.

        Returns:
            Counters and the next UTC reset times.
        """
        from datetime import date

        now = _now()
        day = now.date()
        period = now.strftime("%Y-%m")
        used_row = self.db.execute(
            "SELECT used FROM agent_token_usage WHERE user_id=? AND period=?", (user_id, period)
        ).fetchone()
        used_tokens = used_row[0] if used_row else 0
        limit = self._token_limit_for(user_id)
        actual, reserved = self._gpu_usage_values(user_id, day.isoformat())
        next_day = datetime.combine(day + timedelta(days=1), datetime.min.time(), UTC)
        next_month_day = date(day.year + 1, 1, 1) if day.month == 12 else date(day.year, day.month + 1, 1)
        next_month = datetime.combine(next_month_day, datetime.min.time(), UTC)
        return UsageSnapshot(
            tokens=QuotaCounter(
                limit=limit, used=used_tokens, reserved=0, remaining=max(0, limit-used_tokens),
                unit="tokens", period="month", resets_at=next_month,
            ),
            gpu=GpuQuota(
                limit=self._gpu_limit_for(user_id), used=actual, reserved=reserved,
                remaining=max(0, self._gpu_limit_for(user_id)-actual-reserved), unit="gpu_minutes",
                period="day", resets_at=next_day,
            ),
        )

    def usage_entries_for(self, user_id: str, limit: int = 100) -> list[UsageEntry]:
        """Read recent Token ledger entries and GPU job charges.

        Args:
            user_id: Owner whose usage history is read.
            limit: Maximum combined entries to return.

        Returns:
            Recent entries preserving each ledger's insertion order. Wall-clock
            timestamps merge the resources but cannot reverse a ledger after
            a clock adjustment; the original recorded timestamps stay intact.
        """
        token_rows = self.db.execute(
            "SELECT id,period,kind,amount,run_id,created_at,status FROM agent_token_entries "
            "WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit),
        ).fetchall()
        job_rows = self.db.execute(
            "SELECT id,run_id,status,estimated_minutes,actual_minutes,created_at,"
            "gpu_accounting_status "
            "FROM agent_jobs WHERE user_id=? ORDER BY rowid DESC LIMIT ?", (user_id, limit),
        ).fetchall()
        token_entries = [UsageEntry(
            id=f"token-{row[0]}", resource="tokens", kind=row[2], amount=row[3],
            status=row[6], period=row[1], run_id=row[4],
            created_at=datetime.fromisoformat(row[5]),
        ) for row in reversed(token_rows)]
        job_entries = [UsageEntry(
            id=f"job-{row[0]}", resource="gpu_minutes", kind="job",
            amount=(row[4] or 0) if row[6] in {"settled", "reconciled", "released"}
            else row[3],
            status=row[6] if row[6] in {"pending_reconciliation", "reconciled"} else row[2],
            period=row[5][:10], run_id=row[1], job_id=row[0],
            created_at=datetime.fromisoformat(row[5]),
        ) for row in reversed(job_rows)]
        timed_entries: list[tuple[datetime, UsageEntry]] = []
        for ledger in (token_entries, job_entries):
            sort_time = datetime.min.replace(tzinfo=UTC)
            for entry in ledger:
                # Ledger sequence is authoritative when the host clock moves back.
                sort_time = max(sort_time, entry.created_at)
                timed_entries.append((sort_time, entry))
        recent = sorted(timed_entries, key=lambda item: item[0])[-limit:]
        return [entry for _sort_time, entry in recent]

    def set_token_limit(self, user_id: str, limit: int) -> None:
        """Replace a user's monthly Token limit.

        Args:
            user_id: Account to update.
            limit: Nonnegative Token allowance.

        Raises:
            ValueError: The requested limit is negative.
        """
        if limit < 0:
            raise ValueError("Token limit cannot be negative")
        with self._immediate_transaction():
            self.db.execute(
                "INSERT INTO agent_token_limits (user_id,limit_value) VALUES (?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET limit_value=excluded.limit_value",
                (user_id, limit),
            )

    def seed_token_limit(self, user_id: str, limit: int) -> None:
        """Set an initial Token limit without overwriting an existing limit.

        Args:
            user_id: Account to initialize.
            limit: Nonnegative Token allowance.

        Raises:
            ValueError: The requested limit is negative.
        """
        if limit < 0:
            raise ValueError("Token limit cannot be negative")
        with self._immediate_transaction():
            self.db.execute(
                "INSERT INTO agent_token_limits (user_id,limit_value) VALUES (?,?) "
                "ON CONFLICT(user_id) DO NOTHING",
                (user_id, limit),
            )

    def set_gpu_limit(self, user_id: str, limit: int) -> None:
        """Replace a user's daily GPU minute limit.

        Args:
            user_id: Account to update.
            limit: Nonnegative GPU minute allowance.

        Raises:
            ValueError: The requested limit is negative.
        """
        if limit < 0:
            raise ValueError("GPU limit cannot be negative")
        with self._immediate_transaction():
            self.db.execute(
                "INSERT INTO agent_gpu_limits (user_id,limit_value) VALUES (?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET limit_value=excluded.limit_value",
                (user_id, limit),
            )

    def charge_tokens(self, user_id: str, count: int) -> None:
        """Atomically charge Tokens to the current UTC month.

        Args:
            user_id: Account to charge.
            count: Nonnegative Token amount.

        Raises:
            ValueError: ``count`` is negative.
            TokenQuotaExceeded: The charge exceeds the user's monthly limit.
        """
        if count < 0:
            raise ValueError("Token count cannot be negative")
        period = _now().strftime("%Y-%m")
        with self._immediate_transaction():
            used_row = self.db.execute(
                "SELECT used FROM agent_token_usage WHERE user_id=? AND period=?",
                (user_id, period),
            ).fetchone()
            used = used_row[0] if used_row else 0
            limit = self._token_limit_for(user_id)
            if used + count > limit:
                raise TokenQuotaExceeded
            self.db.execute(
                "INSERT INTO agent_token_usage VALUES (?,?,?) "
                "ON CONFLICT(user_id,period) DO UPDATE SET used=excluded.used",
                (user_id, period, used + count),
            )
            self._append_token_entry(user_id, period, "charge", count, None)

    def reserve_resume_tokens(self, user_id: str, run_id: str, count: int) -> str:
        """Atomically hold Tokens before a Pi Run resumes.

        Args:
            user_id: Account to charge.
            run_id: Run receiving the reservation ledger entry.
            count: Positive number of Tokens to hold.

        Returns:
            UTC month key to use for later settlement.

        Raises:
            ValueError: ``count`` is not positive.
            TokenQuotaExceeded: The hold exceeds the user's monthly limit.
        """
        if count < 1:
            raise ValueError("Token reservation must be positive")
        period = _now().strftime("%Y-%m")
        with self._immediate_transaction():
            used_row = self.db.execute(
                "SELECT used FROM agent_token_usage WHERE user_id=? AND period=?",
                (user_id, period),
            ).fetchone()
            used = used_row[0] if used_row else 0
            limit = self._token_limit_for(user_id)
            if used + count > limit:
                raise TokenQuotaExceeded
            self.db.execute(
                "INSERT INTO agent_token_usage VALUES (?,?,?) "
                "ON CONFLICT(user_id,period) DO UPDATE SET used=excluded.used",
                (user_id, period, used + count),
            )
            self._append_token_entry(user_id, period, "reservation", count, run_id)
        return period

    def adjust_tokens(
        self, user_id: str, delta: int, *, run_id: str | None = None,
        period_override: str | None = None,
    ) -> None:
        """Adjust a Token charge in its original reservation month.

        Negative adjustments cannot reduce the stored usage below zero.

        Args:
            user_id: Account whose usage changes.
            delta: Signed Token change.
            run_id: Optional Run whose reservation determines the month.
            period_override: Explicit UTC month when reconciling old work.
        """
        if delta == 0:
            return
        with self.db:
            reservation = self.db.execute(
                "SELECT period FROM agent_token_entries "
                "WHERE user_id=? AND run_id=? AND kind='reservation' ORDER BY id LIMIT 1",
                (user_id, run_id),
            ).fetchone() if run_id else None
            period = period_override or (reservation[0] if reservation else _now().strftime("%Y-%m"))
            if delta > 0:
                self.db.execute(
                    "INSERT INTO agent_token_usage VALUES (?,?,?) "
                    "ON CONFLICT(user_id,period) DO UPDATE SET "
                    "used=agent_token_usage.used+excluded.used",
                    (user_id, period, delta),
                )
            else:
                self.db.execute(
                    "UPDATE agent_token_usage SET used=CASE WHEN used+? < 0 "
                    "THEN 0 ELSE used+? END "
                    "WHERE user_id=? AND period=?",
                    (delta, delta, user_id, period),
                )
            self._append_token_entry(user_id, period, "adjustment", delta, run_id)

    def record_model_attempt(
        self, user_id: str, run_id: str, tokens: int, status: str,
        *, period_override: str | None = None,
    ) -> None:
        """Record terminal model usage and reconcile the Run's Token hold.

        Args:
            user_id: Run owner.
            run_id: Run whose model attempt completed.
            tokens: Positive observed Token count.
            status: ``completed`` or ``error``.
            period_override: Expected month of the Run reservation.

        Raises:
            ValueError: Usage, status, owner, or reservation month is invalid.
        """
        if tokens <= 0 or status not in {"completed", "error"}:
            raise ValueError("A model attempt needs positive tokens and a terminal status")
        with self._immediate_transaction():
            if self.db.execute(
                "SELECT 1 FROM agent_runs WHERE id=? AND user_id=?", (run_id, user_id),
            ).fetchone() is None:
                raise ValueError("Run does not belong to user")
            _reservation_id, period, reserved, observed, charged = self._latest_token_reservation(
                user_id, run_id,
            )
            if period_override is not None and period_override != period:
                raise ValueError("Model attempt period does not match its reservation")
            self._append_token_entry(
                user_id, period, "model_attempt", tokens, run_id, status,
            )
            guard = self.db.execute(
                "SELECT call_id FROM agent_model_call_guards "
                "WHERE user_id=? AND run_id=? AND status='active' ORDER BY rowid DESC LIMIT 1",
                (user_id, run_id),
            ).fetchone()
            if guard is not None:
                self.db.execute(
                    "UPDATE agent_model_call_guards SET status='reported' WHERE call_id=?",
                    (guard[0],),
                )
            pending = self._unreported_model_reserve(user_id, run_id)
            self._adjust_token_reservation(
                user_id, run_id, period, max(reserved, observed + tokens + pending) - charged,
            )

    def reserve_model_call(
        self, user_id: str, run_id: str, call_id: str, prompt_bytes: int,
        requested_output_tokens: int,
    ) -> int:
        """Reserve a model call's estimated Token budget before forwarding it.

        The output limit may be reduced to fit remaining quota. The guard is
        stored atomically so repeated or concurrent calls cannot over-admit.

        Args:
            user_id: Run owner.
            run_id: Active Run requesting the model call.
            call_id: Unique request guard identifier.
            prompt_bytes: Serialized request size used for the input estimate.
            requested_output_tokens: Maximum output Tokens requested by Pi.

        Returns:
            Maximum output Tokens admitted by the current quota.

        Raises:
            ValueError: The request or Run state is invalid.
            TokenQuotaExceeded: No output budget remains.
        """
        if not call_id or prompt_bytes < 1 or requested_output_tokens < 1:
            raise ValueError("Invalid model call budget")
        with self._immediate_transaction():
            run = self.db.execute(
                "SELECT status FROM agent_runs WHERE id=? AND user_id=?", (run_id, user_id),
            ).fetchone()
            if run is None or run[0] != "running":
                raise ValueError("Run is not active")
            existing = self.db.execute(
                "SELECT user_id,run_id,prompt_bytes,max_output_tokens,status "
                "FROM agent_model_call_guards WHERE call_id=?", (call_id,),
            ).fetchone()
            if existing is not None:
                if existing[:3] != (user_id, run_id, prompt_bytes) or existing[4] != "active":
                    raise ValueError("Model call ID conflicts with a previous request")
                return existing[3]
            _, period, reserved, observed, charged = self._latest_token_reservation(
                user_id, run_id,
            )
            used_row = self.db.execute(
                "SELECT used FROM agent_token_usage WHERE user_id=? AND period=?",
                (user_id, period),
            ).fetchone()
            used = used_row[0] if used_row else 0
            limit = self._token_limit_for(user_id)
            pending = self._unreported_model_reserve(user_id, run_id)
            # JSON request bytes include the prompt, tools and protocol fields. Twice that
            # size is a conservative admission estimate for common OpenAI-compatible models.
            input_budget = prompt_bytes * 2
            baseline = max(reserved, observed + pending)
            headroom = limit - used + charged - baseline - input_budget
            max_output = min(requested_output_tokens, headroom)
            if max_output < 1:
                raise TokenQuotaExceeded
            call_budget = input_budget + max_output
            target = max(reserved, observed + pending + call_budget)
            self._adjust_token_reservation(user_id, run_id, period, target - charged)
            self.db.execute(
                "INSERT INTO agent_model_call_guards VALUES (?,?,?,?,?,?,?,?)",
                (call_id, user_id, run_id, prompt_bytes, max_output, call_budget,
                 "active", _now().isoformat()),
            )
            return max_output

    def _unreported_model_reserve(self, user_id: str, run_id: str) -> int:
        """Sum active model call holds without reported provider usage."""
        return self.db.execute(
            "SELECT COALESCE(SUM(reserved_tokens),0) FROM agent_model_call_guards "
            "WHERE user_id=? AND run_id=? AND status IN ('active','unreported')",
            (user_id, run_id),
        ).fetchone()[0]

    def record_unreported_model_call(self, user_id: str, run_id: str) -> None:
        """Keep an active call's upper-bound hold when usage is unavailable.

        Args:
            user_id: Run owner.
            run_id: Run whose latest active model guard is marked unreported.
        """
        with self._immediate_transaction():
            row = self.db.execute(
                "SELECT call_id FROM agent_model_call_guards "
                "WHERE user_id=? AND run_id=? AND status='active' ORDER BY rowid DESC LIMIT 1",
                (user_id, run_id),
            ).fetchone()
            if row is not None:
                self.db.execute(
                    "UPDATE agent_model_call_guards SET status='unreported' WHERE call_id=?",
                    (row[0],),
                )

    def release_model_call(self, user_id: str, run_id: str, call_id: str) -> None:
        """Release a guard after a definite gateway rejection before execution.

        Args:
            user_id: Run owner.
            run_id: Run that made the model call.
            call_id: Active guard to reject and release.
        """
        with self._immediate_transaction():
            changed = self.db.execute(
                "UPDATE agent_model_call_guards SET status='rejected' "
                "WHERE call_id=? AND user_id=? AND run_id=? AND status='active'",
                (call_id, user_id, run_id),
            ).rowcount
            if not changed:
                return
            _reservation_id, period, reserved, observed, charged = self._latest_token_reservation(
                user_id, run_id,
            )
            target = max(reserved, observed + self._unreported_model_reserve(user_id, run_id))
            self._adjust_token_reservation(user_id, run_id, period, target - charged)

    def settle_current_tokens(
        self, user_id: str, run_id: str, *, refund_if_unreported: bool = False,
    ) -> None:
        """Settle the latest Pi turn from all durable model attempts.

        Prior retries and unreported provider calls are included. An empty
        turn is refunded only when explicitly requested.

        Args:
            user_id: Run owner.
            run_id: Run whose latest reservation is settled.
            refund_if_unreported: Refund an empty turn instead of retaining
                its original hold.
        """
        with self._immediate_transaction():
            _reservation_id, period, _reserved, observed, charged = self._latest_token_reservation(
                user_id, run_id,
            )
            pending = self._unreported_model_reserve(user_id, run_id)
            target = (observed + pending if observed or pending else
                      0 if refund_if_unreported else charged)
            self._adjust_token_reservation(user_id, run_id, period, target - charged)

    def _latest_token_reservation(self, user_id: str, run_id: str) -> tuple[int, str, int, int, int]:
        """Read the newest Run hold and its observed and adjusted totals.

        Returns:
            Reservation ID, month, original hold, observed usage, and charged total.

        Raises:
            ValueError: The Run has no Token reservation.
        """
        reservation = self.db.execute(
            "SELECT id,period,amount FROM agent_token_entries "
            "WHERE user_id=? AND run_id=? AND kind='reservation' ORDER BY id DESC LIMIT 1",
            (user_id, run_id),
        ).fetchone()
        if reservation is None:
            raise ValueError("Run has no Token reservation")
        reservation_id, period, reserved = reservation
        observed, adjustment = self.db.execute(
            "SELECT COALESCE(SUM(CASE WHEN kind='model_attempt' THEN amount END),0),"
            "COALESCE(SUM(CASE WHEN kind='adjustment' THEN amount END),0) "
            "FROM agent_token_entries WHERE user_id=? AND run_id=? AND id>?",
            (user_id, run_id, reservation_id),
        ).fetchone()
        return reservation_id, period, reserved, observed, reserved + adjustment

    def _adjust_token_reservation(
        self, user_id: str, run_id: str, period: str, delta: int,
    ) -> None:
        """Change a Run's persisted Token charge and append an audit entry."""
        if delta == 0:
            return
        if delta > 0:
            self.db.execute(
                "INSERT INTO agent_token_usage VALUES (?,?,?) "
                "ON CONFLICT(user_id,period) DO UPDATE SET "
                "used=agent_token_usage.used+excluded.used",
                (user_id, period, delta),
            )
        else:
            self.db.execute(
                "UPDATE agent_token_usage SET used=CASE WHEN used+? < 0 "
                "THEN 0 ELSE used+? END "
                "WHERE user_id=? AND period=?",
                (delta, delta, user_id, period),
            )
        self._append_token_entry(user_id, period, "adjustment", delta, run_id)
