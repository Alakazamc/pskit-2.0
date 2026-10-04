"""Real database accounting: concurrent admission, cumulative usage and legacy quotas."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from compute_support import request

from app.contracts.compute import (
    UsageReport,
    UsageWindow,
)


def test_two_submissions_cannot_overspend_last_gpu_window(ledger_system):
    _database, ledger, jobs = ledger_system

    def submit(key):
        try:
            return jobs.submit("alice", request(), key)
        except ValueError as exc:
            assert str(exc) == "GPU_QUOTA_EXCEEDED"
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, ["one", "two"]))
    assert sum(item is not None for item in results) == 1
    usage = ledger.usage_for("alice")
    assert usage.gpu.reserved == 40000
    assert usage.gpu.remaining == 20000


def test_cumulative_usage_is_not_double_charged(ledger_system):
    database, ledger, jobs = ledger_system
    job = jobs.submit("alice", request(), "one")
    for seq, amount in [(1, 20000), (2, 30000), (2, 30000)]:
        with database.transaction() as connection:
            ledger.accept_usage(connection, job.id, seq, UsageReport(
                gpu_device_ms=amount, source="service_reported"))
    usage = ledger.usage_for("alice")
    assert usage.gpu.used == 30000
    assert usage.gpu.reserved == 10000
    assert usage.sources == ["service_reported"]
    with database.transaction() as connection, pytest.raises(ValueError, match="USAGE_CONFLICT"):
        ledger.accept_usage(connection, job.id, 2, UsageReport(
            gpu_device_ms=40000, source="service_reported"))
    with database.transaction() as connection, pytest.raises(ValueError, match="USAGE_DECREASED"):
        ledger.accept_usage(connection, job.id, 3, UsageReport(
            gpu_device_ms=1000, source="service_reported"))


def test_cross_day_keeps_unsettled_reservation_and_splits_explicit_window(ledger_system):
    database, ledger, jobs = ledger_system
    job = jobs.submit("alice", request(), "one")
    tomorrow = datetime(2030, 1, 2, 0, 0, 0, tzinfo=UTC)
    assert ledger.usage_for("alice", now=tomorrow).gpu.reserved == 40000
    with database.transaction() as connection:
        ledger.accept_usage(connection, job.id, 1, UsageReport(
            gpu_device_ms=20000, source="service_reported"), window=UsageWindow(
                start=datetime(2030, 1, 1, 23, 59, 50, tzinfo=UTC),
                end=datetime(2030, 1, 2, 0, 0, 10, tzinfo=UTC)), terminal=True)
    assert ledger.usage_for("alice", now=tomorrow).gpu.used == 10000
    assert ledger.usage_for("alice", now=tomorrow).gpu.reserved == 0
    assert ledger.usage_for("alice", now=datetime(2030, 1, 1, tzinfo=UTC)).gpu.used == 10000


def test_unknown_final_usage_keeps_hold_and_failed_known_usage_settles(ledger_system):
    database, ledger, jobs = ledger_system
    job = jobs.submit("alice", request(), "one")
    with database.transaction() as connection, pytest.raises(ValueError, match="REQUIRED_USAGE_MISSING"):
        ledger.accept_usage(connection, job.id, 1, UsageReport(source="unknown"), terminal=True)
    assert ledger.usage_for("alice").gpu.reserved == 40000
    with database.transaction() as connection:
        ledger.accept_usage(connection, job.id, 1, UsageReport(
            gpu_device_ms=23000, source="service_reported"), terminal=True)
    assert ledger.usage_for("alice").gpu.used == 23000
    assert ledger.usage_for("alice").gpu.reserved == 0


def test_af3_and_generic_admission_share_gpu_budget(ledger_system):
    from app.domain.persistent_conversation import PersistentConversationStore

    database, ledger, jobs = ledger_system
    legacy = PersistentConversationStore(database)
    legacy.set_gpu_limit("alice", 1)
    jobs.submit("alice", request(), "generic")
    from app.domain.quota import GpuQuotaExceeded
    with pytest.raises(GpuQuotaExceeded):
        legacy.create_af3_job("alice", 1)
    jobs.cancel("alice", jobs.submit("alice", request(), "generic").id)
    legacy.create_af3_job("alice", 1)
    assert ledger.usage_for("alice").gpu.reserved == 60000
    assert "legacy_wall" in ledger.usage_for("alice").sources


@pytest.mark.parametrize("accounting", ["reserved", "pending_reconciliation"])
def test_legacy_cross_day_hold_still_blocks_af3_admission(ledger_system, accounting):
    from app.domain.persistent_conversation import PersistentConversationStore
    from app.domain.quota import GpuQuotaExceeded

    database, ledger, _jobs = ledger_system
    legacy = PersistentConversationStore(database)
    legacy.set_gpu_limit("alice", 1)
    old_job = legacy.create_af3_job("alice", 1)
    with database.transaction() as connection:
        connection.execute("UPDATE agent_jobs SET created_at='2000-01-01T00:00:00+00:00',"
            "gpu_accounting_status=%s WHERE id=%s", (accounting, old_job.id))
    assert ledger.usage_for("alice").gpu.reserved == 60000
    assert legacy.usage_for("alice").gpu.reserved == 1
    with pytest.raises(GpuQuotaExceeded):
        legacy.create_af3_job("alice", 1)
