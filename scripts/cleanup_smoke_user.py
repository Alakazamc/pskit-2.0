"""Remove the uniquely named disposable user created by the live delivery smoke test."""

import sys

from sqlalchemy import delete, or_, select

from app.db.models import (
    AgentMessage,
    AgentSession,
    AgentTurn,
    Artifact,
    AuthSession,
    Candidate,
    CandidateTrack,
    ResearchRun,
    ResearchTaskLink,
    StrategyPolicy,
    StrategyTransition,
    Task,
    TaskExecutionLease,
    TaskRetry,
    User,
)
from app.db.session import SessionLocal


def main() -> None:
    allowed_prefixes = ("smoke_durable_", "smoke_fullchain_")
    if len(sys.argv) != 2 or not sys.argv[1].startswith(allowed_prefixes):
        raise SystemExit("refusing cleanup: expected one approved smoke-test username")

    username = sys.argv[1]
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == username))
        if user is None:
            print("already absent")
            return
        if user.role != "user":
            raise SystemExit("refusing cleanup: smoke account is not a regular user")

        task_ids = list(db.scalars(select(Task.id).where(Task.user_id == user.id)))
        if task_ids:
            db.execute(delete(TaskExecutionLease).where(TaskExecutionLease.task_id.in_(task_ids)))
            db.execute(
                delete(TaskRetry).where(
                    or_(
                        TaskRetry.parent_task_id.in_(task_ids),
                        TaskRetry.child_task_id.in_(task_ids),
                    )
                )
            )

        db.execute(delete(StrategyTransition).where(StrategyTransition.user_id == user.id))
        db.execute(delete(StrategyPolicy).where(StrategyPolicy.user_id == user.id))
        db.execute(delete(ResearchTaskLink).where(ResearchTaskLink.user_id == user.id))
        db.execute(delete(Candidate).where(Candidate.user_id == user.id))
        db.execute(delete(CandidateTrack).where(CandidateTrack.user_id == user.id))
        turns = db.execute(delete(AgentTurn).where(AgentTurn.user_id == user.id)).rowcount
        messages = db.execute(delete(AgentMessage).where(AgentMessage.user_id == user.id)).rowcount
        artifacts = db.execute(delete(Artifact).where(Artifact.user_id == user.id)).rowcount
        tasks = db.execute(delete(Task).where(Task.user_id == user.id)).rowcount
        research_runs = db.execute(
            delete(ResearchRun).where(ResearchRun.user_id == user.id)
        ).rowcount
        agent_sessions = db.execute(
            delete(AgentSession).where(AgentSession.user_id == user.id)
        ).rowcount
        auth_sessions = db.execute(delete(AuthSession).where(AuthSession.user_id == user.id)).rowcount
        users = db.execute(delete(User).where(User.id == user.id)).rowcount
        db.commit()
        print(
            f"removed users={users} auth_sessions={auth_sessions} "
            f"agent_sessions={agent_sessions} messages={messages} turns={turns} "
            f"research_runs={research_runs} tasks={tasks} artifacts={artifacts}"
        )


if __name__ == "__main__":
    main()
