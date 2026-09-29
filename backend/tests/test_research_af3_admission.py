from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import Candidate, CandidateTrack, ResearchRun, Task
from app.db.session import SessionLocal
from app.main import app


def test_af3_batch_quota_failure_is_clear_and_atomic(monkeypatch):
    monkeypatch.setattr(get_settings(), "max_active_af3_per_user", 1)
    client = TestClient(app)
    assert client.post("/api/auth/register", json={
        "username": "af3-batch-owner", "password": "password123",
    }).status_code == 200
    created = client.post("/api/research-runs", json={
        "title": "AF3 batch", "target": {"sequence": "ACDE"},
    })
    assert created.status_code == 200
    run_id = UUID(created.json()["id"])

    with SessionLocal() as db:
        research_run = db.get(ResearchRun, run_id)
        research_run.current_stage = "top10_af3"
        track = db.scalar(select(CandidateTrack).where(
            CandidateTrack.research_run_id == run_id,
            CandidateTrack.track == "rna",
        ))
        assert track is not None
        for rank, sequence in enumerate(("ACGU", "ACGUA"), start=1):
            db.add(Candidate(
                research_run_id=run_id, candidate_track_id=track.id,
                user_id=research_run.user_id, track="rna", sequence=sequence,
                sequence_length=len(sequence), generator_name="fixture",
                iteration=track.current_iteration, rank=rank,
                selection_status="qualified",
            ))
        db.commit()

    response = client.post(f"/api/research-runs/{run_id}/tracks/rna/af3-batch", json={
        "max_candidates": 2,
    })
    assert response.status_code == 429
    assert response.headers["retry-after"] == "30"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Task)) == 0
        candidates = db.scalars(select(Candidate).where(Candidate.research_run_id == run_id)).all()
        assert len(candidates) == 2
        assert all(candidate.af3_task_id is None for candidate in candidates)
