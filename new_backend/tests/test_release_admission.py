"""Durable release ownership and transaction-level admission exclusion."""

from dataclasses import replace

import pytest

from app.contracts.conversation import MessageRequest
from app.domain.persistent_conversation import PersistentConversationStore
from app.domain.release_admission import (
    AdmissionClosed,
    AdmissionConflict,
    AdmissionUnavailable,
    DurableAdmissionGate,
)


def identity(**changes):
    return {
        "adapter_id": "synthetic-adapter", "qualification_sha256": "a" * 64,
        "owner_nonce": "b" * 64, "environment": "staging", "project": "pskit-agent-staging",
        "previous_manifest_sha256": "c" * 64, "target_manifest_sha256": "d" * 64,
        "scopes": ["agent_runs", "compute_jobs", "workspace_attempts", "scheduler_claims"],
        **changes,
    }


def gated_store(tmp_path, clock=None):
    from app.db.release_admission_migrations import migrate_admission_sqlite

    store = PersistentConversationStore(str(tmp_path / "admission.sqlite3"))
    migrate_admission_sqlite(store.db)
    gate = DurableAdmissionGate(
        store.db, environment="staging", project="pskit-agent-staging",
        qualification_sha256="a" * 64, **({"clock": clock} if clock else {}),
    )
    gate.configure()
    store.admission_gate = gate
    return store, gate


def test_same_owner_hold_is_durable_and_expiry_never_opens(tmp_path):
    clock = [1000.0]
    store, gate = gated_store(tmp_path, lambda: clock[0])
    receipt = gate.enter(identity())
    assert receipt["state"] == "closed"
    assert receipt["hold_mode"] == "until_explicit_release"
    assert gate.enter(identity())["owner_nonce"] == "b" * 64
    clock[0] += 100_000
    assert receipt["valid_until"] < clock[0]
    with store._immediate_transaction(), pytest.raises(AdmissionClosed):
        gate.require_open(store.db)
    store.db.close()
    reopened = PersistentConversationStore(str(tmp_path / "admission.sqlite3"))
    after_restart = DurableAdmissionGate(
        reopened.db, environment="staging", project="pskit-agent-staging", qualification_sha256="a" * 64,
    )
    after_restart.configure()
    assert after_restart.verify(identity())["state"] == "closed"
    assert after_restart.release(identity())["state"] == "released"
    assert after_restart.release(identity())["state"] == "released"
    assert after_restart.enter(identity())["state"] == "closed"


@pytest.mark.parametrize("changes", [
    {"owner_nonce": "f" * 64}, {"adapter_id": "other"}, {"qualification_sha256": "f" * 64},
    {"target_manifest_sha256": "e" * 64}, {"previous_manifest_sha256": "f" * 64},
    {"environment": "prod"}, {"project": "pskit-agent-cloud"}, {"scopes": ["agent_runs"]},
])
def test_other_identity_never_steals_or_opens_hold(tmp_path, changes):
    _store, gate = gated_store(tmp_path)
    gate.enter(identity())
    for operation in (gate.enter, gate.verify, gate.release):
        with pytest.raises((AdmissionConflict, ValueError)):
            operation(identity(**changes))
    assert gate.verify(identity())["state"] == "closed"


def test_closed_gate_rejects_message_before_reservation_but_replays_ack(tmp_path):
    store, gate = gated_store(tmp_path)
    session = store.create_session("alice", store.project_for("alice").id, "Audit")
    accepted = store.accept_message(
        "alice", session.id, MessageRequest(content="existing"), None, 100, "existing", "old-hash",
    )
    before = store.db.execute("SELECT * FROM agent_token_entries ORDER BY id").fetchall()
    gate.enter(identity())
    assert store.accept_message(
        "alice", session.id, MessageRequest(content="existing"), None, 100, "existing", "old-hash",
    ) == (accepted[0], False)
    with pytest.raises(AdmissionClosed):
        store.accept_message("alice", session.id, MessageRequest(content="new"), None, 200, "new", "new")
    with pytest.raises(AdmissionClosed):
        store.send_message("alice", session.id, MessageRequest(content="new"))
    assert store.db.execute("SELECT * FROM agent_token_entries ORDER BY id").fetchall() == before
    assert len(store.messages_for("alice", session.id)) == 1
    assert store.claim_initial_run(accepted[0].run_id, "scheduler") is False
    assert store.claim_queued_runs("scheduler") == []
    assert store.claim_wakeups("scheduler") == []
    gate.release(identity())
    assert store.claim_initial_run(accepted[0].run_id, "scheduler")


def test_enabled_factory_requires_private_key_and_component(tmp_path):
    from app.config import Settings
    from app.main import create_app

    settings = replace(Settings(), release_admission_enabled=True)
    with pytest.raises(ValueError, match="admission"):
        create_app(settings)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, True, "invalid"])
def test_invalid_clock_cannot_modify_hold_state(tmp_path, value):
    store, gate = gated_store(tmp_path, lambda: value)
    before = store.db.execute("SELECT * FROM release_admission_state").fetchall()
    with pytest.raises(AdmissionUnavailable, match="clock"):
        gate.enter(identity())
    assert store.db.execute("SELECT * FROM release_admission_state").fetchall() == before


def test_enabled_factory_rejects_uncontrolled_legacy_manager():
    from app.config import Settings
    from app.main import create_app

    settings = replace(Settings(), mode="live", agent_runtime="pi", pi_execution="sandbox",
                       release_admission_enabled=True, release_admission_key="k" * 40)
    with pytest.raises(ValueError, match="admission"):
        create_app(settings)
