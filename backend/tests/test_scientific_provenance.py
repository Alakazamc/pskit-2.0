from app.api.research import update_research_run
from app.artifacts.service import resolve_owned_local_artifact
from app.db.models import Candidate, CandidateTrack, ResearchRun, User
from app.db.session import SessionLocal
from app.schemas.research import UpdateResearchRunRequest
from app.tasks.worker import candidate_generator_version
from app.tools.reports import generate_research_report
from app.tools.science_adapters import normalize_candidate_result


def test_manual_evidence_patch_preserves_server_records():
    db = SessionLocal()
    try:
        user = User(username="evidence_lead", password_hash="unused", role="research_lead")
        db.add(user)
        db.flush()
        server_record = {
            "evidence_id": "server-event",
            "kind": "agent_tool_execution",
            "provenance": {"kind": "agent_tool"},
        }
        old_manual = {
            "evidence_id": "old-manual",
            "claim": "old",
            "provenance": {"kind": "manual"},
        }
        research_run = ResearchRun(
            user_id=user.id,
            title="Evidence preservation",
            target_json={"sequence": "ACDE"},
            evidence_json=[server_record, old_manual],
        )
        db.add(research_run)
        db.commit()

        updated = update_research_run(
            research_run.id,
            UpdateResearchRunRequest(evidence=[{"claim": "new"}]),
            db,
            user,
        )
        assert updated.evidence[0] == server_record
        assert len(updated.evidence) == 2
        assert updated.evidence[1]["claim"] == "new"
        assert updated.evidence[1]["provenance"]["kind"] == "manual"

        cleared = update_research_run(
            research_run.id,
            UpdateResearchRunRequest(evidence=[]),
            db,
            user,
        )
        assert cleared.evidence == [server_record]
    finally:
        db.close()


def test_nested_mcp_result_version_reaches_candidate_generator():
    normalized = normalize_candidate_result(
        {"result": {"candidates": ["ACGU"], "model_version": "coral-3.2"}}
    )
    assert candidate_generator_version(normalized["metadata"]) == "coral-3.2"


def test_research_report_shows_candidate_provenance_and_evidence_omissions():
    db = SessionLocal()
    try:
        user = User(username="report_owner", password_hash="unused", role="user")
        db.add(user)
        db.flush()
        evidence = [
            {
                "evidence_id": f"event-{index}",
                "kind": "agent_tool_execution",
                "stage_at_execution": "candidate_generation",
                "target_hash": "target-digest",
                "tool_name": "generate_coral_candidates",
                "result_summary": {"status": "queued"},
                "provenance": {"kind": "agent_tool"},
                "payload": "x" * 1500,
            }
            for index in range(22)
        ]
        research_run = ResearchRun(
            user_id=user.id,
            title="Traceable report",
            target_json={"sequence": "ACDE"},
            evidence_json=evidence,
            metadata_json={"target_hash": "target-digest"},
        )
        db.add(research_run)
        db.flush()
        track = CandidateTrack(research_run_id=research_run.id, user_id=user.id, track="rna")
        db.add(track)
        db.flush()
        candidate = Candidate(
            research_run_id=research_run.id,
            candidate_track_id=track.id,
            user_id=user.id,
            track="rna",
            sequence="ACGU",
            sequence_length=4,
            generator_name="CORAL RNA MCP",
            generator_version="coral-3.2",
            parameters_hash="parameter-digest",
            raw_metrics_json={"score": 0.8},
            metadata_json={"target_hash": "target-digest"},
        )
        db.add(candidate)
        db.commit()

        generated = generate_research_report(
            db,
            user,
            research_run,
            operation_id="provenance-test",
        )
        report = resolve_owned_local_artifact(db, user, generated["artifact_id"]).read_text(
            encoding="utf-8"
        )
        assert "证据记录数：22" in report
        assert "另有 2 条证据未在本报告展开" in report
        assert "已截断" in report
        assert "coral-3.2" in report
        assert "parameter-digest" in report
        assert "target-digest" in report
        assert "原始指标" in report
    finally:
        db.close()
