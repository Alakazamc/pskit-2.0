"""A calendar projection of measured usage, independent of recent-history limits."""

from datetime import date, timedelta

from app.contracts.models import DailyUsage, UsageActivity


def build_activity(
    end: date, count: int, totals: dict[str, tuple[int, int]],
) -> UsageActivity:
    """Fill every UTC day, clamping net corrections to nonnegative display totals."""
    start = end - timedelta(days=count - 1)
    days = []
    for offset in range(count):
        day = start + timedelta(days=offset)
        tokens, gpu_ms = totals.get(day.isoformat(), (0, 0))
        days.append(DailyUsage(date=day, tokens=max(0, tokens), gpu_ms=max(0, gpu_ms)))
    return UsageActivity(start_date=start, end_date=end, days=days)
