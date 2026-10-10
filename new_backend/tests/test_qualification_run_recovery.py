"""Internal qualification scopes never become ordinary Agent retries."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.contracts.conversation import MessageRequest
from app.contracts.model_runtime import ModelRuntimeProfile
from app.domain.persistent_conversation import PersistentConversationStore
from app.services.model_qualification_driver import ModelQualificationDriver

USER = "synthetic-qualification-recovery"


@pytest.fixture
def store(tmp_path):
    instance = PersistentConversationStore(str(tmp_path / "qualification.sqlite3"))
    try:
        yield instance
    finally:
        instance.db.close()


def _driver_run(store):
    """Create the actual driver's private Run without any HTTP/provider access."""
    project = store.create_project(USER, "Recovery audit", "Fixed local fixture")
    session = store.create_session(USER, project.id, "Qualification scope")
    state = SimpleNamespace(
        profile=ModelRuntimeProfile(alias="synthetic-document", revision=1),
        session_id=session.id,
        deployment_fingerprint="a" * 64,
        evaluation_id="synthetic-evaluation",
    )
    driver = ModelQualificationDriver(
        catalog=None, model_policy=None, conversations=store, agent_service=None,
        qualification_user_id=USER,
    )
    return driver._new_run(state, "text"), driver._lease_owner


def _guarded_partial_usage(store, run_id, unknown_status="active"):
    """Retain a second unknown provider call after the first usage was reported."""
    store.reserve_resume_tokens(USER, run_id, 1)
    store.reserve_model_call(USER, run_id, "reported-" + run_id, 10, 30)
    store.record_model_attempt(USER, run_id, 7, "completed")
    store.reserve_model_call(USER, run_id, "unknown-" + run_id, 20, 80)
    if unknown_status == "unreported":
        store.record_unreported_model_call(USER, run_id)


def _ledger_snapshot(store, run_id):
    return (
        store.db.execute(
            "SELECT * FROM agent_token_entries WHERE run_id=? ORDER BY id", (run_id,),
        ).fetchall(),
        store.db.execute(
            "SELECT * FROM agent_model_call_guards WHERE run_id=? ORDER BY call_id", (run_id,),
        ).fetchall(),
        store.db.execute(
            "SELECT * FROM agent_token_usage WHERE user_id=? ORDER BY period", (USER,),
        ).fetchall(),
    )


def _expire(store, run_id):
    with store.db:
        store.db.execute(
            "UPDATE agent_runs SET lease_expires_at=? WHERE id=?",
            ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), run_id),
        )


def _assert_qualification_failed(store, run_id):
    assert store.run_status_for(USER, run_id).status == "failed"
    assert store.db.execute(
        "SELECT lease_owner,lease_expires_at,retry_after,active_resume_job_id,"
        "initial_attempts,resume_attempts FROM agent_runs WHERE id=?", (run_id,),
    ).fetchone() == (None, None, None, None, 0, 0)
    events = store.events_for(USER, run_id, None)
    assert [event.type for event in events] == ["run.failed"]
    assert events[0].data.code == "MODEL_QUALIFICATION_INTERRUPTED"
    assert store.claim_initial_run(run_id, "ordinary-worker") is False
    assert store.claim_queued_runs("ordinary-worker") == []
    assert store.claim_wakeups("ordinary-worker") == []


def _recovery_case(store, trigger, unknown_status):
    run_id, owner = _driver_run(store)
    _guarded_partial_usage(store, run_id, unknown_status)
    before = _ledger_snapshot(store, run_id)
    assert store.usage_for(USER).tokens.used == 127
    if trigger == "expired":
        _expire(store, run_id)
        store.recover_wakeups(retry_seconds=0)
    elif trigger == "shutdown":
        store.release_owned_runs(owner, retry_seconds=0)
    else:
        store.recover_transient_initial_failure(run_id, owner, retry_seconds=0)
    _assert_qualification_failed(store, run_id)
    assert _ledger_snapshot(store, run_id) == before
    # Recovery is idempotent and cannot append a second failure or a refund.
    store.recover_wakeups(retry_seconds=0)
    store.release_owned_runs(owner, retry_seconds=0)
    _assert_qualification_failed(store, run_id)
    assert _ledger_snapshot(store, run_id) == before


@pytest.mark.parametrize("trigger", ["expired", "shutdown", "transient"])
@pytest.mark.parametrize("unknown_status", ["active", "unreported"])
def test_real_driver_interruption_is_terminal_and_retains_unknown_charge(store, trigger, unknown_status):
    _recovery_case(store, trigger, unknown_status)


def _unknown_charge_case(store):
    run_id, _owner = _driver_run(store)
    _guarded_partial_usage(store, run_id)
    before = _ledger_snapshot(store, run_id)
    _expire(store, run_id)
    store.recover_wakeups(retry_seconds=0)
    assert store.usage_for(USER).tokens.used == 127
    assert _ledger_snapshot(store, run_id) == before


def test_partial_observation_cannot_refund_unknown_qualification_guard(store):
    _unknown_charge_case(store)


def _queued_prefix_case(store):
    now = datetime.now(UTC).isoformat()
    with store.db:
        for index in range(105):
            store.db.execute(
                "INSERT INTO agent_runs (id,user_id,session_id,status,created_at,context_json) "
                "VALUES (?,?,?,'queued',?,?)",
                (f"qualification-{index}", USER, f"qualification-session-{index}", now,
                 json.dumps({"purpose": "model_qualification", "user_prompt": "must not run"})),
            )
        store.db.execute(
            "INSERT INTO agent_runs (id,user_id,session_id,status,created_at,context_json) "
            "VALUES ('ordinary',?,'ordinary-session','queued',?,?)",
            (USER, now, json.dumps({"user_prompt": "ordinary request"})),
        )
    assert store.claim_initial_run("qualification-0", "ordinary-worker") is False
    assert store.claim_queued_runs("ordinary-worker", max_active=1) == [
        ("ordinary", USER, "ordinary-session", "ordinary request"),
    ]
    assert store.db.execute(
        "SELECT COUNT(*) FROM agent_runs WHERE id LIKE ? AND status='queued'", ("qualification-%",),
    ).fetchone() == (105,)


def test_qualification_prefix_is_excluded_before_initial_candidate_limit(store):
    _queued_prefix_case(store)


def _resume_prefix_case(store):
    now = datetime.now(UTC).isoformat()
    with store.db:
        for index in range(106):
            name = f"qualification-{index}" if index < 105 else "ordinary"
            context = {"purpose": "model_qualification"} if index < 105 else {}
            store.db.execute(
                "INSERT INTO agent_runs (id,user_id,session_id,status,created_at,context_json) "
                "VALUES (?,?,?,'waiting',?,?)",
                (name, USER, "session-" + name, now, json.dumps(context)),
            )
            store.db.execute(
                "INSERT INTO agent_jobs (id,user_id,run_id,tool_call_id,status,progress,"
                "estimated_minutes,created_at) VALUES (?,?,?,?,'completed',100,1,?)",
                ("job-" + name, USER, name, "call-" + name, now),
            )
    assert store.claim_wakeups("ordinary-worker", max_active=1) == [
        ("ordinary", USER, "session-ordinary", "job-ordinary"),
    ]
    assert store.db.execute(
        "SELECT COUNT(*) FROM agent_runs WHERE id LIKE ? AND status='waiting'", ("qualification-%",),
    ).fetchone() == (105,)


def test_qualification_prefix_is_excluded_before_resume_candidate_limit(store):
    _resume_prefix_case(store)


def _ordinary_recovery_case(store):
    project = store.create_project(USER, "Ordinary audit", "Fixed fixture")
    session = store.create_session(USER, project.id, "Ordinary conversation")
    run = store.send_message(USER, session.id, MessageRequest(content="ordinary request"))
    assert store.claim_initial_run(run.run_id, "ordinary-worker")
    _expire(store, run.run_id)
    store.recover_wakeups(retry_seconds=0)
    assert store.run_status_for(USER, run.run_id).status == "queued"
    assert [event.type for event in store.events_for(USER, run.run_id, None)] == ["run.retrying"]
    assert store.claim_queued_runs("ordinary-worker") == [(run.run_id, USER, session.id, "")]


def test_ordinary_expired_run_keeps_existing_retry_behavior(store):
    _ordinary_recovery_case(store)


def test_qualification_recovery_preserves_live_and_other_owner_leases(store):
    run_id, owner = _driver_run(store)
    store.recover_wakeups()
    store.release_owned_runs("different-owner")
    store.recover_transient_initial_failure(run_id, "different-owner", 0)
    assert store.run_status_for(USER, run_id).status == "running"
    assert store.owns_lease(run_id, owner)
    assert store.events_for(USER, run_id, None) == []


@pytest.mark.parametrize("status", ["running", "resume_queued"])
def test_qualification_without_token_hold_or_with_checkpoint_still_fails_closed(store, status):
    run_id, _owner = _driver_run(store)
    with store.db:
        store.db.execute(
            "UPDATE agent_runs SET status=?,checkpoint_file='/synthetic/checkpoint',"
            "active_resume_job_id='prior-job',retry_after='future' WHERE id=?", (status, run_id),
        )
    _expire(store, run_id)
    store.recover_wakeups()
    _assert_qualification_failed(store, run_id)

