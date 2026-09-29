import pytest
from fastapi import HTTPException

from app.api.research import create_af3_batch, create_candidate, score_candidate_track
from app.db.models import Candidate, CandidateTrack, ResearchRun, User
from app.db.session import SessionLocal
from app.schemas.research import (
    CreateAf3BatchRequest,
    CreateCandidateRequest,
    ScoreTrackRequest,
)


def test_scoring_second_track_persists_af3_stage_and_allows_batch():
    db = SessionLocal()
    try:
        user = User(username="scoring_stage_owner", password_hash="unused", role="user")
        db.add(user)
        db.flush()
        research_run = ResearchRun(
            user_id=user.id,
            title="Scoring stage transition",
            current_stage="candidate_evaluation",
            status="active",
            target_json={"sequence": "ACDEFGHIK"},
        )
        db.add(research_run)
        db.flush()
        rna_track = CandidateTrack(
            research_run_id=research_run.id,
            user_id=user.id,
            track="rna",
            status="ranked",
        )
        peptide_track = CandidateTrack(
            research_run_id=research_run.id,
            user_id=user.id,
            track="peptide",
            status="evaluating",
        )
        db.add_all([rna_track, peptide_track])
        db.flush()
        db.add_all(
            [
                Candidate(
                    research_run_id=research_run.id,
                    candidate_track_id=rna_track.id,
                    user_id=user.id,
                    track="rna",
                    sequence="ACGU",
                    sequence_length=4,
                    generator_name="test",
                    iteration=0,
                    selection_status="qualified",
                    rank=1,
                ),
                Candidate(
                    research_run_id=research_run.id,
                    candidate_track_id=peptide_track.id,
                    user_id=user.id,
                    track="peptide",
                    sequence="ACDE",
                    sequence_length=4,
                    generator_name="test",
                    iteration=0,
                    raw_metrics_json={"affinity": 0.8},
                ),
            ]
        )
        db.commit()

        score_candidate_track(
            research_run.id,
            "peptide",
            ScoreTrackRequest(
                config_version="test-v1",
                metrics=[{"name": "affinity", "weight": 1.0}],
                minimum_total_score=0.5,
            ),
            db,
            user,
        )
        db.expire_all()
        assert db.get(ResearchRun, research_run.id).current_stage == "top10_af3"

        batch = create_af3_batch(
            research_run.id,
            "peptide",
            CreateAf3BatchRequest(max_candidates=1),
            db,
            user,
        )
        assert batch.submitted_count == 1
        assert batch.tasks[0].task.status == "queued"

        with pytest.raises(HTTPException) as error:
            create_candidate(
                research_run.id,
                CreateCandidateRequest(
                    track="peptide",
                    sequence="ACDF",
                    iteration=1,
                ),
                db,
                user,
            )
        assert error.value.status_code == 409
        db.expire_all()
        assert db.get(CandidateTrack, peptide_track.id).current_iteration == 0
    finally:
        db.close()


def test_manual_import_can_still_advance_iteration_before_af3_submission():
    db = SessionLocal()
    try:
        user = User(username="pre_af3_import_owner", password_hash="unused", role="user")
        db.add(user)
        db.flush()
        research_run = ResearchRun(
            user_id=user.id,
            title="Pre-AF3 candidate import",
            target_json={"sequence": "ACDEFGHIK"},
        )
        db.add(research_run)
        db.flush()
        track = CandidateTrack(
            research_run_id=research_run.id,
            user_id=user.id,
            track="peptide",
            current_iteration=0,
        )
        db.add(track)
        db.commit()

        candidate = create_candidate(
            research_run.id,
            CreateCandidateRequest(track="peptide", sequence="ACDE", iteration=1),
            db,
            user,
        )
        assert candidate.iteration == 1
        db.expire_all()
        assert db.get(CandidateTrack, track.id).current_iteration == 1
    finally:
        db.close()
