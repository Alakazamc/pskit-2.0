import pytest

from app.domain.quota import QuotaLedger


def test_wrong_owner_cannot_consume_or_destroy_gpu_reservation():
    ledger = QuotaLedger()
    ledger.reserve_gpu("alice", "job-1", 20)
    with pytest.raises(ValueError, match="owner"):
        ledger.settle_gpu("bob", "job-1", 18)
    assert ledger.usage_for("alice").gpu.reserved == 20
    ledger.settle_gpu("alice", "job-1", 18)
    assert ledger.usage_for("alice").gpu.reserved == 0


def test_wrong_owner_cannot_release_gpu_reservation():
    ledger = QuotaLedger()
    ledger.reserve_gpu("alice", "job-1", 20)
    with pytest.raises(ValueError, match="owner"):
        ledger.release_gpu("bob", "job-1")
    assert ledger.usage_for("alice").gpu.reserved == 20


def test_invalid_settlement_keeps_reservation_available_for_retry():
    ledger = QuotaLedger()
    ledger.reserve_gpu("alice", "job-1", 20)
    with pytest.raises(ValueError, match="exceeds"):
        ledger.settle_gpu("alice", "job-1", 21)
    assert ledger.usage_for("alice").gpu.reserved == 20
    ledger.settle_gpu("alice", "job-1", 18)
