from app.domain.quota import QuotaLedger


def test_mock_token_ledger_can_refund_a_failed_message_admission():
    ledger = QuotaLedger()
    ledger.charge_tokens("alice", 10)
    ledger.adjust_tokens("alice", -10)
    usage = ledger.usage_for("alice").tokens
    assert usage.used == 0 and usage.remaining == usage.limit
