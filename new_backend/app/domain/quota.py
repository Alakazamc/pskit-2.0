from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from app.contracts.models import GpuQuota, QuotaCounter, UsageActivity, UsageEntry, UsageSnapshot
from app.domain.usage_activity import build_activity


class GpuQuotaExceeded(Exception):
    """A GPU reservation exceeds the user's daily allowance."""


class TokenQuotaExceeded(Exception):
    """A Token charge exceeds the user's monthly allowance."""


@dataclass
class _DailyBucket:
    """Mock GPU usage and outstanding job holds for one UTC day."""
    used: int = 0
    reservations: dict[str, int] = field(default_factory=dict)


class QuotaLedger:
    """Track mock-mode Token and GPU usage in process memory."""

    def __init__(self) -> None:
        """Initialize empty monthly Token and daily GPU ledgers."""
        self.identity_policy = None
        self._gpu_buckets: dict[tuple[str, date], _DailyBucket] = {}
        self._job_bucket: dict[str, tuple[str, date]] = {}
        self._token_limits: dict[str, int] = {}
        self._gpu_limits: dict[str, int] = {}
        self._token_used: dict[tuple[str, int, int], int] = {}
        self._entries: dict[str, list[UsageEntry]] = {}
        self._gpu_entries: dict[str, UsageEntry] = {}
        self._next_token_entry_id = 1

    def _record_token_entry(
        self, user_id: str, kind: str, amount: int, day: date, run_id: str | None = None,
    ) -> None:
        """Append a mock Token usage entry for an account and month."""
        entry = UsageEntry(
            id=f"token-{self._next_token_entry_id}", resource="tokens", kind=kind,
            amount=amount, status="posted", period=f"{day.year:04d}-{day.month:02d}",
            run_id=run_id, created_at=datetime.now(UTC),
        )
        self._next_token_entry_id += 1
        self._entries.setdefault(user_id, []).append(entry)

    @staticmethod
    def _today() -> date:
        """Return the current UTC calendar day for quota resets."""
        return datetime.now(UTC).date()

    def _bucket(self, user_id: str, day: date) -> _DailyBucket:
        """Get or create one account's daily GPU accounting bucket."""
        return self._gpu_buckets.setdefault((user_id, day), _DailyBucket())

    def _token_limit_for(self, user_id: str) -> int:
        """Read an explicit Token limit or the identity tier default."""
        default = (self.identity_policy.default_token_limit_for(user_id)
                   if self.identity_policy else 1_000_000)
        return self._token_limits.get(user_id, default)

    def _gpu_limit_for(self, user_id: str) -> int:
        """Read an explicit GPU limit or the identity tier default."""
        default = (self.identity_policy.default_gpu_limit_for(user_id)
                   if self.identity_policy else 60)
        return self._gpu_limits.get(user_id, default)

    def usage_for(self, user_id: str) -> UsageSnapshot:
        """Read current monthly Token and daily GPU mock quota counters.

        Args:
            user_id: Owner whose quota is requested.

        Returns:
            Usage, reservations, remaining allowance, and UTC reset times.
        """
        now = datetime.now(UTC)
        day = now.date()
        bucket = self._bucket(user_id, day)
        reserved = sum(bucket.reservations.values())
        next_day = datetime.combine(day + timedelta(days=1), datetime.min.time(), UTC)
        next_month_day = (
            date(day.year + 1, 1, 1) if day.month == 12 else date(day.year, day.month + 1, 1)
        )
        next_month = datetime.combine(next_month_day, datetime.min.time(), UTC)
        token_limit = self._token_limit_for(user_id)
        token_used = self._token_used.get((user_id, day.year, day.month), 0)
        gpu_limit = self._gpu_limit_for(user_id)
        return UsageSnapshot(
            tokens=QuotaCounter(
                limit=token_limit,
                used=token_used,
                reserved=0,
                remaining=max(0, token_limit - token_used),
                unit="tokens",
                period="month",
                resets_at=next_month,
            ),
            gpu=GpuQuota(
                limit=gpu_limit,
                used=bucket.used,
                reserved=reserved,
                remaining=max(0, gpu_limit - bucket.used - reserved),
                unit="gpu_minutes",
                period="day",
                resets_at=next_day,
            ),
        )

    def usage_entries_for(self, user_id: str, limit: int = 100) -> list[UsageEntry]:
        """Copy the user's latest mock usage ledger entries.

        Args:
            user_id: Ledger owner.
            limit: Maximum recent entries; nonpositive values return none.

        Returns:
            Detached usage entries in chronological order.
        """
        if limit <= 0:
            return []
        return [entry.model_copy(deep=True) for entry in self._entries.get(user_id, [])[-limit:]]

    def activity_for(self, user_id: str, days: int = 365) -> UsageActivity:
        """Aggregate actual mock ledger entries, excluding queued GPU reservations."""
        totals: dict[str, tuple[int, int]] = {}
        for entry in self._entries.get(user_id, []):
            day = entry.created_at.astimezone(UTC).date().isoformat()
            tokens, gpu_ms = totals.get(day, (0, 0))
            if entry.resource == "tokens" and entry.kind in {"charge", "adjustment"}:
                tokens += entry.amount
            elif entry.resource == "gpu_minutes" and entry.status in {"completed", "reconciled"}:
                gpu_ms += entry.amount * 60_000
            totals[day] = tokens, gpu_ms
        return build_activity(self._today(), days, totals)

    def set_token_limit(self, user_id: str, limit: int) -> None:
        """Replace a mock user's monthly Token limit.

        Raises:
            ValueError: The limit is negative.
        """
        if limit < 0:
            raise ValueError("Token limit cannot be negative")
        self._token_limits[user_id] = limit

    def seed_token_limit(self, user_id: str, limit: int) -> None:
        """Set a mock Token limit only when no explicit limit exists.

        Raises:
            ValueError: A new limit is negative.
        """
        if user_id not in self._token_limits:
            self.set_token_limit(user_id, limit)

    def set_gpu_limit(self, user_id: str, limit: int) -> None:
        """Replace a mock user's daily GPU minute limit.

        Raises:
            ValueError: The limit is negative.
        """
        if limit < 0:
            raise ValueError("GPU limit cannot be negative")
        self._gpu_limits[user_id] = limit

    def charge_tokens(self, user_id: str, count: int) -> None:
        """Charge mock Tokens to the current UTC month.

        Args:
            user_id: Account to charge.
            count: Nonnegative Token amount.

        Raises:
            ValueError: ``count`` is negative.
            TokenQuotaExceeded: The monthly allowance is insufficient.
        """
        if count < 0:
            raise ValueError("Token count cannot be negative")
        today = self._today()
        key = (user_id, today.year, today.month)
        used = self._token_used.get(key, 0)
        if used + count > self._token_limit_for(user_id):
            raise TokenQuotaExceeded
        self._token_used[key] = used + count
        self._record_token_entry(user_id, "charge", count, today)

    def adjust_tokens(self, user_id: str, delta: int, *, run_id: str | None = None) -> None:
        """Apply a signed Token correction to the current mock month.

        Args:
            user_id: Account to correct.
            delta: Signed Token adjustment.
            run_id: Optional Run to reference in the ledger.
        """
        if delta == 0:
            return
        today = self._today()
        key = (user_id, today.year, today.month)
        self._token_used[key] = max(0, self._token_used.get(key, 0) + delta)
        self._record_token_entry(user_id, "adjustment", delta, today, run_id)

    def reserve_gpu(
        self, user_id: str, job_id: str, estimated_minutes: int,
        *, run_id: str | None = None,
    ) -> None:
        """Hold mock GPU minutes for a job on the current UTC day.

        Args:
            user_id: Job owner.
            job_id: Job receiving the reservation.
            estimated_minutes: GPU minutes to reserve.
            run_id: Optional Agent Run attached to the job.

        Raises:
            GpuQuotaExceeded: The daily allowance cannot cover the hold.
        """
        day = self._today()
        bucket = self._bucket(user_id, day)
        available = self.available_gpu(user_id)
        if estimated_minutes > available:
            raise GpuQuotaExceeded
        bucket.reservations[job_id] = estimated_minutes
        self._job_bucket[job_id] = (user_id, day)
        entry = UsageEntry(
            id=f"job-{job_id}", resource="gpu_minutes", kind="job",
            amount=estimated_minutes, status="queued", period=day.isoformat(),
            run_id=run_id, job_id=job_id, created_at=datetime.now(UTC),
        )
        self._gpu_entries[job_id] = entry
        self._entries.setdefault(user_id, []).append(entry)

    def mark_gpu_running(self, user_id: str, job_id: str) -> None:
        """Mark an owned mock GPU reservation as running.

        Raises:
            ValueError: The reservation belongs to another user.
        """
        if self._job_bucket[job_id][0] != user_id:
            raise ValueError("Job owner mismatch")
        self._gpu_entries[job_id].status = "running"

    def available_gpu(self, user_id: str) -> int:
        """Return unspent mock GPU minutes after usage and reservations."""
        bucket = self._bucket(user_id, self._today())
        return max(0, self._gpu_limit_for(user_id) - bucket.used
                   - sum(bucket.reservations.values()))

    def settle_gpu(self, user_id: str, job_id: str, actual_minutes: int) -> None:
        """Convert an owned mock GPU hold to settled usage.

        Args:
            user_id: Job owner.
            job_id: Reserved GPU job.
            actual_minutes: Nonnegative minutes no greater than the hold.

        Raises:
            ValueError: Owner or actual minutes conflict with the reservation.
        """
        owner_and_day = self._job_bucket[job_id]
        if owner_and_day[0] != user_id:
            raise ValueError("Job owner mismatch")
        bucket = self._bucket(*owner_and_day)
        reserved = bucket.reservations[job_id]
        if actual_minutes < 0 or actual_minutes > reserved:
            raise ValueError("Actual GPU use exceeds reservation")
        self._job_bucket.pop(job_id)
        bucket.reservations.pop(job_id)
        bucket.used += actual_minutes
        self._gpu_entries[job_id].amount = actual_minutes
        self._gpu_entries[job_id].status = "completed"

    def release_gpu(self, user_id: str, job_id: str) -> None:
        """Cancel an owned mock GPU reservation without charging minutes.

        Raises:
            ValueError: The reservation belongs to another user.
        """
        owner_and_day = self._job_bucket[job_id]
        if owner_and_day[0] != user_id:
            raise ValueError("Job owner mismatch")
        self._job_bucket.pop(job_id)
        self._bucket(*owner_and_day).reservations.pop(job_id)
        self._gpu_entries[job_id].amount = 0
        self._gpu_entries[job_id].status = "cancelled"
