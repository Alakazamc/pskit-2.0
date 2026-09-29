import hashlib
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.db.models import (
    AgentMessage,
    AgentSession,
    Artifact,
    Candidate,
    CandidateTrack,
    ResearchRun,
    ResearchTaskLink,
    StrategyPolicy,
    StrategyTransition,
    Task,
    TaskRetry,
    User,
    now_utc,
)
from app.db.session import get_db
from app.schemas.research import (
    CandidateResponse,
    CandidateTrackResponse,
    CreateCandidateRequest,
    CreateAf3BatchRequest,
    CreateResearchRunRequest,
    GenerateResearchReportRequest,
    LinkResearchTaskRequest,
    ManualStageSkipRequest,
    Af3BatchResponse,
    ResearchTaskResponse,
    ScoreTrackRequest,
    StrategyFeedbackResponse,
    StrategyFeedbackOptionResponse,
    StrategyPolicyResponse,
    StrategyReplayResponse,
    StrategyTransitionResponse,
    SubmitStrategyFeedbackRequest,
    TrackScoreResponse,
    ResearchRunResponse,
    UpdateCandidateRequest,
    UpdateResearchRunRequest,
    UpdateStrategyPolicyRequest,
)
from app.api.tasks import serialize_task
from app.agent.feedback import AgentActionTrace, extract_agent_action_traces
from app.agent.policy import (
    available_actions,
    build_policy_state,
    ensure_strategy_policy,
    replay_transitions,
    state_key,
    update_q_value,
)
from app.research.scoring import score_candidates
from app.research.service import (
    RESEARCH_STAGES,
    canonical_target_hash,
    ensure_research_target_hash,
    link_task_to_research_run,
    refresh_research_run_progress,
)
from app.tasks.service import create_queued_task
from app.tools.reports import generate_research_report


router = APIRouter(prefix="/api/research-runs", tags=["research-runs"])


def load_owned_run(db: Session, user: User, research_run_id: UUID) -> ResearchRun:
    research_run = db.get(ResearchRun, research_run_id)
    if not research_run or research_run.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Research run not found")
    return research_run


def load_owned_candidate(
    db: Session,
    user: User,
    research_run: ResearchRun,
    candidate_id: UUID,
) -> Candidate:
    candidate = db.get(Candidate, candidate_id)
    if (
        not candidate
        or candidate.user_id != user.id
        or candidate.research_run_id != research_run.id
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Candidate not found")
    return candidate


def load_track(db: Session, research_run: ResearchRun, track: str) -> CandidateTrack:
    candidate_track = db.scalar(
        select(CandidateTrack).where(
            CandidateTrack.research_run_id == research_run.id,
            CandidateTrack.track == track,
        )
    )
    if not candidate_track:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Candidate track is missing")
    return candidate_track


def serialize_track(candidate_track: CandidateTrack) -> CandidateTrackResponse:
    return CandidateTrackResponse(
        id=str(candidate_track.id),
        track=candidate_track.track,
        status=candidate_track.status,
        current_iteration=candidate_track.current_iteration,
        score_config=candidate_track.score_config_json,
        summary=candidate_track.summary_json,
        created_at=candidate_track.created_at.isoformat(),
        updated_at=candidate_track.updated_at.isoformat(),
    )


def serialize_run(
    db: Session,
    research_run: ResearchRun,
    *,
    progress: dict | None = None,
) -> ResearchRunResponse:
    tracks = db.scalars(
        select(CandidateTrack)
        .where(CandidateTrack.research_run_id == research_run.id)
        .order_by(CandidateTrack.track.asc())
    ).all()
    return ResearchRunResponse(
        id=str(research_run.id),
        session_id=str(research_run.session_id) if research_run.session_id else None,
        title=research_run.title,
        status=str(progress["status"]) if progress else research_run.status,
        current_stage=(
            str(progress["current_stage"])
            if progress
            else research_run.current_stage
        ),
        target=research_run.target_json,
        stage_state=(
            dict(progress["stage_state"])
            if progress
            else research_run.stage_state_json
        ),
        evidence=research_run.evidence_json,
        policy_version=research_run.policy_version,
        metadata=research_run.metadata_json,
        tracks=[serialize_track(item) for item in tracks],
        created_at=research_run.created_at.isoformat(),
        updated_at=research_run.updated_at.isoformat(),
    )


def serialize_candidate(candidate: Candidate) -> CandidateResponse:
    return CandidateResponse(
        id=str(candidate.id),
        research_run_id=str(candidate.research_run_id),
        candidate_track_id=str(candidate.candidate_track_id),
        schema_version=candidate.schema_version,
        track=candidate.track,
        sequence=candidate.sequence,
        sequence_length=candidate.sequence_length,
        generator_name=candidate.generator_name,
        generator_version=candidate.generator_version,
        generation_task_id=str(candidate.generation_task_id) if candidate.generation_task_id else None,
        raw_artifact_id=str(candidate.raw_artifact_id) if candidate.raw_artifact_id else None,
        parent_candidate_id=str(candidate.parent_candidate_id) if candidate.parent_candidate_id else None,
        iteration=candidate.iteration,
        seed=candidate.seed,
        parameters_hash=candidate.parameters_hash,
        raw_metrics=candidate.raw_metrics_json,
        normalized_metrics=candidate.normalized_metrics_json,
        total_score=candidate.total_score,
        score_config_version=candidate.score_config_version,
        rank=candidate.rank,
        selection_status=candidate.selection_status,
        selection_reason=candidate.selection_reason,
        af3_task_id=str(candidate.af3_task_id) if candidate.af3_task_id else None,
        af3_status=candidate.af3_status,
        af3_artifact_id=str(candidate.af3_artifact_id) if candidate.af3_artifact_id else None,
        af3_result_artifact_id=(
            str((candidate.metadata_json or {}).get("af3_result_artifact_id"))
            if (candidate.metadata_json or {}).get("af3_result_artifact_id")
            else None
        ),
        metadata=candidate.metadata_json,
        created_at=candidate.created_at.isoformat(),
        updated_at=candidate.updated_at.isoformat(),
    )


def validate_owned_task(db: Session, user: User, task_id: UUID | None) -> None:
    if task_id is None:
        return
    task = db.get(Task, task_id)
    if not task or task.user_id != user.id:
        raise HTTPException(status_code=422, detail="Task not found")


def validate_owned_artifact(db: Session, user: User, artifact_id: UUID | None) -> None:
    if artifact_id is None:
        return
    artifact = db.get(Artifact, artifact_id)
    if not artifact or artifact.user_id != user.id:
        raise HTTPException(status_code=422, detail="Artifact not found")


def serialize_research_task(db: Session, link: ResearchTaskLink) -> ResearchTaskResponse:
    task = db.get(Task, link.task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Linked task is missing")
    return ResearchTaskResponse(
        id=str(link.id),
        role=link.role,
        candidate_id=str(link.candidate_id) if link.candidate_id else None,
        task=serialize_task(db, task),
        created_at=link.created_at.isoformat(),
    )


def serialize_strategy_policy(policy: StrategyPolicy) -> StrategyPolicyResponse:
    return StrategyPolicyResponse(
        id=str(policy.id),
        research_run_id=str(policy.research_run_id),
        version=policy.version,
        enabled=policy.enabled,
        alpha=policy.alpha,
        gamma=policy.gamma,
        epsilon=policy.epsilon,
        q_table=policy.q_table_json,
        action_mask=policy.action_mask_json,
        metrics=policy.metrics_json,
        created_at=policy.created_at.isoformat(),
        updated_at=policy.updated_at.isoformat(),
    )


def serialize_strategy_transition(
    transition: StrategyTransition,
) -> StrategyTransitionResponse:
    return StrategyTransitionResponse(
        id=str(transition.id),
        state_key=transition.state_key,
        action=transition.action,
        reward=transition.reward,
        next_state_key=transition.next_state_key,
        q_before=transition.q_before,
        q_after=transition.q_after,
        outcome=transition.outcome_json,
        policy_version=transition.policy_version,
        created_at=transition.created_at.isoformat(),
    )


def feedback_trace_key(agent_message_id: UUID | str, tool_call_id: str) -> tuple[str, str]:
    return str(agent_message_id), tool_call_id


def feedback_message_research_run_id(metadata: dict | None) -> str:
    """Resolve private message ownership, with compatibility for legacy rows."""

    if not isinstance(metadata, dict):
        return ""
    value = metadata.get("_internal_research_run_id")
    if value is None:
        value = metadata.get("research_run_id")
    return str(value or "")


def feedback_transition_id(
    research_run_id: UUID,
    agent_message_id: UUID,
    tool_call_id: str,
) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"pskit-feedback:{research_run_id}:{agent_message_id}:{tool_call_id}",
    )


def used_feedback_trace_keys(
    db: Session,
    research_run: ResearchRun,
    user: User,
) -> set[tuple[str, str]]:
    transitions = db.scalars(
        select(StrategyTransition).where(
            StrategyTransition.research_run_id == research_run.id,
            StrategyTransition.user_id == user.id,
        )
    ).all()
    result: set[tuple[str, str]] = set()
    for transition in transitions:
        outcome = transition.outcome_json or {}
        message_id = outcome.get("agent_message_id")
        tool_call_id = outcome.get("tool_call_id")
        if message_id and tool_call_id:
            result.add(feedback_trace_key(str(message_id), str(tool_call_id)))
    return result


def load_feedback_message(
    db: Session,
    user: User,
    research_run: ResearchRun,
    agent_message_id: UUID,
) -> AgentMessage:
    message = db.get(AgentMessage, agent_message_id)
    metadata = message.metadata_json if message else {}
    if (
        message is None
        or message.user_id != user.id
        or message.role != "assistant"
        or feedback_message_research_run_id(metadata) != str(research_run.id)
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent action trace not found",
        )
    return message


def find_message_trace(message: AgentMessage, tool_call_id: str) -> AgentActionTrace:
    for trace in extract_agent_action_traces(message.metadata_json):
        if trace.tool_call_id == tool_call_id:
            return trace
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Agent action trace not found",
    )


def reconcile_action_trace(
    db: Session,
    user: User,
    research_run: ResearchRun,
    trace: AgentActionTrace,
) -> dict:
    """用当前数据库终态重建行动证据，消息事件只作为调度审计来源。"""
    parsed_task_ids: list[UUID] = []
    malformed_task_ids: list[str] = []
    for raw_task_id in trace.task_ids:
        try:
            parsed_task_ids.append(UUID(str(raw_task_id)))
        except (TypeError, ValueError):
            malformed_task_ids.append(str(raw_task_id))

    task_chains: dict[UUID, list[UUID]] = {}
    all_task_ids: list[UUID] = []
    for root_id in parsed_task_ids:
        chain = [root_id]
        seen = {root_id}
        current_id = root_id
        for _index in range(20):
            retry = db.get(TaskRetry, current_id)
            if retry is None or retry.child_task_id in seen:
                break
            current_id = retry.child_task_id
            chain.append(current_id)
            seen.add(current_id)
        task_chains[root_id] = chain
        all_task_ids.extend(chain)
    all_task_ids = list(dict.fromkeys(all_task_ids))

    tasks_by_id: dict[UUID, Task] = {}
    linked_task_ids: set[UUID] = set()
    if all_task_ids:
        tasks_by_id = {
            task.id: task
            for task in db.scalars(
                select(Task).where(
                    Task.id.in_(all_task_ids),
                    Task.user_id == user.id,
                )
            ).all()
        }
        linked_task_ids = set(
            db.scalars(
                select(ResearchTaskLink.task_id).where(
                    ResearchTaskLink.research_run_id == research_run.id,
                    ResearchTaskLink.user_id == user.id,
                    ResearchTaskLink.task_id.in_(all_task_ids),
                )
            ).all()
        )

    task_statuses: dict[str, str] = {}
    evidence_complete = not malformed_task_ids
    for task_id in all_task_ids:
        task = tasks_by_id.get(task_id)
        if task is None:
            task_statuses[str(task_id)] = "missing"
            evidence_complete = False
        elif task_id not in linked_task_ids:
            task_statuses[str(task_id)] = "unlinked"
            evidence_complete = False
        else:
            task_statuses[str(task_id)] = task.status

    artifact_ids = []
    if all_task_ids:
        artifact_ids.extend(
            str(artifact_id)
            for artifact_id in db.scalars(
                select(Artifact.id).where(
                    Artifact.user_id == user.id,
                    Artifact.task_id.in_(all_task_ids),
                )
            ).all()
        )
    for raw_artifact_id in trace.artifact_ids:
        try:
            artifact_id = UUID(str(raw_artifact_id))
        except (TypeError, ValueError):
            evidence_complete = False
            continue
        artifact = db.get(Artifact, artifact_id)
        if artifact is None or artifact.user_id != user.id:
            evidence_complete = False
            continue
        artifact_ids.append(str(artifact.id))

    statuses = []
    for root_id in parsed_task_ids:
        chain = task_chains.get(root_id, [root_id])
        latest_id = chain[-1]
        statuses.append(task_statuses.get(str(latest_id), "missing"))
    if trace.invalid_call or trace.error_type:
        execution_status = "failed"
        success: bool | None = False
    elif not evidence_complete:
        execution_status = "unverified"
        success = None
    elif statuses and any(item == "failed" for item in statuses):
        execution_status = "failed"
        success = False
    elif statuses and all(item == "succeeded" for item in statuses):
        execution_status = "completed"
        success = True
    elif statuses:
        execution_status = "pending"
        success = None
    elif trace.execution_status == "completed":
        execution_status = "completed"
        success = True
    elif trace.execution_status == "failed":
        execution_status = "failed"
        success = False
    else:
        execution_status = "interrupted"
        success = None

    error_type = trace.error_type
    if error_type is None and execution_status == "failed":
        failed_task = next(
            (task for task in tasks_by_id.values() if task.status == "failed"),
            None,
        )
        error_type = failed_task.error_type if failed_task is not None else "task_failed"
    if not evidence_complete and error_type is None:
        error_type = "incomplete_server_evidence"
    return {
        "dispatch_status": trace.execution_status,
        "execution_status": execution_status,
        "success": success,
        "invalid_call": trace.invalid_call,
        "error_type": error_type,
        "task_ids": [str(item) for item in all_task_ids],
        "task_statuses": task_statuses,
        "artifact_ids": sorted(set(artifact_ids)),
        "event_index": trace.event_index,
        "policy_version_at_action": trace.policy_version,
        "action_rank_at_action": trace.action_rank,
        "allowed_actions_at_action": list(trace.allowed_actions),
    }


def serialize_feedback_option(
    db: Session,
    user: User,
    research_run: ResearchRun,
    message: AgentMessage,
    trace: AgentActionTrace,
    used_keys: set[tuple[str, str]],
) -> StrategyFeedbackOptionResponse:
    outcome = reconcile_action_trace(db, user, research_run, trace)
    return StrategyFeedbackOptionResponse(
        agent_message_id=str(message.id),
        tool_call_id=trace.tool_call_id,
        action=trace.action,
        stage=trace.state["stage"],
        state_key=trace.state_key,
        dispatch_status=outcome["dispatch_status"],
        execution_status=outcome["execution_status"],
        success=outcome["success"],
        invalid_call=outcome["invalid_call"],
        error_type=outcome["error_type"],
        task_ids=outcome["task_ids"],
        task_statuses=outcome["task_statuses"],
        artifact_ids=outcome["artifact_ids"],
        policy_version_at_action=outcome["policy_version_at_action"],
        action_rank_at_action=outcome["action_rank_at_action"],
        already_feedback=feedback_trace_key(message.id, trace.tool_call_id) in used_keys,
        created_at=message.created_at.isoformat(),
    )


@router.post("", response_model=ResearchRunResponse)
def create_research_run(
    payload: CreateResearchRunRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if payload.session_id:
        agent_session = db.get(AgentSession, payload.session_id)
        if not agent_session or agent_session.user_id != user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    research_run = ResearchRun(
        user_id=user.id,
        session_id=payload.session_id,
        title=payload.title,
        target_json=payload.target,
        stage_state_json={"target_analysis": {"status": "pending"}},
        metadata_json={
            **payload.metadata,
            "target_hash": canonical_target_hash(payload.target),
        },
    )
    db.add(research_run)
    db.flush()
    db.add_all(
        [
            CandidateTrack(research_run_id=research_run.id, user_id=user.id, track="rna"),
            CandidateTrack(research_run_id=research_run.id, user_id=user.id, track="peptide"),
        ]
    )
    # wzf：策略行随科研运行在同一事务创建，使后续 GET 保持纯读。
    ensure_strategy_policy(db, user, research_run)
    db.commit()
    db.refresh(research_run)
    return serialize_run(db, research_run)


@router.get("", response_model=list[ResearchRunResponse])
def list_research_runs(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    research_runs = db.scalars(
        select(ResearchRun)
        .where(ResearchRun.user_id == user.id)
        .order_by(ResearchRun.updated_at.desc())
    ).all()
    return [
        serialize_run(
            db,
            item,
            progress=refresh_research_run_progress(db, item, persist=False),
        )
        for item in research_runs
    ]


@router.get("/{research_run_id}", response_model=ResearchRunResponse)
def get_research_run(
    research_run_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    research_run = load_owned_run(db, user, research_run_id)
    progress = refresh_research_run_progress(db, research_run, persist=False)
    return serialize_run(db, research_run, progress=progress)


@router.patch("/{research_run_id}", response_model=ResearchRunResponse)
def update_research_run(
    research_run_id: UUID,
    payload: UpdateResearchRunRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    research_run = load_owned_run(db, user, research_run_id)
    updates = payload.model_dump(exclude_unset=True)
    if "target" in updates:
        new_target = updates.pop("target")
        if canonical_target_hash(new_target) != canonical_target_hash(
            research_run.target_json or {}
        ):
            has_task = db.scalar(
                select(ResearchTaskLink.id)
                .where(ResearchTaskLink.research_run_id == research_run.id)
                .limit(1)
            )
            has_candidate = db.scalar(
                select(Candidate.id)
                .where(Candidate.research_run_id == research_run.id)
                .limit(1)
            )
            target_record = dict(
                (research_run.stage_state_json or {}).get("target_analysis")
                or {}
            )
            if (
                research_run.status != "draft"
                or research_run.current_stage != "target_analysis"
                or has_task is not None
                or has_candidate is not None
                or bool(research_run.evidence_json)
                or target_record.get("status") not in {None, "pending"}
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Research target is immutable after scientific evidence "
                        "or downstream work has started"
                    ),
                )
            research_run.target_json = new_target
    if "evidence" in updates:
        if user.role not in {"admin", "research_lead"}:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Manual evidence updates require a research lead or administrator",
            )
        timestamp = now_utc().isoformat()
        manual_evidence = updates.pop("evidence")
        target_hash = canonical_target_hash(research_run.target_json or {})
        research_run.evidence_json = [
            {
                **item,
                "evidence_id": str(
                    uuid5(
                        NAMESPACE_URL,
                        (
                            f"manual-evidence:{research_run.id}:"
                            f"{research_run.current_stage}:{target_hash}:{index}:{timestamp}"
                        ),
                    )
                ),
                "research_run_id": str(research_run.id),
                "stage_at_execution": research_run.current_stage,
                "target_hash": target_hash,
                "provenance": {
                    "kind": "manual",
                    "user_id": str(user.id),
                    "created_at": timestamp,
                },
            }
            for index, item in enumerate(manual_evidence or [])
        ]
    if "metadata" in updates:
        incoming_metadata = dict(updates.pop("metadata") or {})
        reserved = {
            "target_hash",
            "last_report_artifact_id",
            "manual_stage_audit",
        }
        metadata = dict(research_run.metadata_json or {})
        metadata.update(
            {
                key: value
                for key, value in incoming_metadata.items()
                if key not in reserved and not key.startswith("_")
            }
        )
        research_run.metadata_json = metadata
    for field, value in updates.items():
        setattr(research_run, field, value)
    ensure_research_target_hash(research_run)
    research_run.updated_at = now_utc()
    db.commit()
    db.refresh(research_run)
    return serialize_run(db, research_run)


@router.post("/{research_run_id}/candidates", response_model=CandidateResponse)
def create_candidate(
    research_run_id: UUID,
    payload: CreateCandidateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    research_run = load_owned_run(db, user, research_run_id)
    candidate_track = load_track(db, research_run, payload.track)
    if payload.parent_candidate_id:
        parent = load_owned_candidate(db, user, research_run, payload.parent_candidate_id)
        if parent.track != payload.track:
            raise HTTPException(
                status_code=422,
                detail="Parent candidate must belong to the same track",
            )
    candidate = Candidate(
        research_run_id=research_run.id,
        candidate_track_id=candidate_track.id,
        user_id=user.id,
        schema_version="1.0",
        track=payload.track,
        sequence=payload.sequence,
        sequence_length=len(payload.sequence),
        generator_name="manual_import",
        generator_version=None,
        generation_task_id=None,
        raw_artifact_id=None,
        parent_candidate_id=payload.parent_candidate_id,
        iteration=payload.iteration,
        seed=None,
        parameters_hash=None,
        raw_metrics_json={},
        metadata_json={
            **payload.metadata,
            "target_hash": ensure_research_target_hash(research_run),
            "provenance": "manual",
            "manual_import": {
                "user_id": str(user.id),
                "note": payload.note,
                "created_at": now_utc().isoformat(),
            },
        },
    )
    db.add(candidate)
    candidate_track.current_iteration = max(candidate_track.current_iteration, payload.iteration)
    candidate_track.updated_at = now_utc()
    research_run.updated_at = now_utc()
    db.commit()
    db.refresh(candidate)
    return serialize_candidate(candidate)


@router.get("/{research_run_id}/candidates", response_model=list[CandidateResponse])
def list_candidates(
    research_run_id: UUID,
    track: Literal["rna", "peptide"] | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    research_run = load_owned_run(db, user, research_run_id)
    query = select(Candidate).where(
        Candidate.research_run_id == research_run.id,
        Candidate.user_id == user.id,
    )
    if track:
        query = query.where(Candidate.track == track)
    candidates = db.scalars(query.order_by(Candidate.created_at.asc())).all()
    return [serialize_candidate(item) for item in candidates]


@router.post(
    "/{research_run_id}/tracks/{track}/score",
    response_model=TrackScoreResponse,
)
def score_candidate_track(
    research_run_id: UUID,
    track: Literal["rna", "peptide"],
    payload: ScoreTrackRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    research_run = load_owned_run(db, user, research_run_id)
    if research_run.current_stage != "candidate_evaluation":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Candidate scoring is only allowed during candidate_evaluation",
        )
    candidate_track = load_track(db, research_run, track)
    query = select(Candidate).where(
        Candidate.research_run_id == research_run.id,
        Candidate.user_id == user.id,
        Candidate.track == track,
    )
    effective_iteration = (
        payload.iteration
        if payload.iteration is not None
        else candidate_track.current_iteration
    )
    query = query.where(Candidate.iteration == effective_iteration)
    candidates = list(db.scalars(query.order_by(Candidate.created_at.asc())).all())
    if not candidates:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="No candidates to score")
    scored = score_candidates(
        candidates,
        payload.metrics,
        config_version=payload.config_version,
        minimum_total_score=payload.minimum_total_score,
    )
    candidate_track.score_config_json = {
        **payload.model_dump(mode="json"),
        "effective_iteration": effective_iteration,
    }
    candidate_track.status = "ranked"
    candidate_track.summary_json = {
        **(candidate_track.summary_json or {}),
        "candidate_count": len(scored),
        "qualified_count": sum(item.selection_status == "qualified" for item in scored),
        "rejected_count": sum(item.selection_status == "rejected" for item in scored),
        "config_version": payload.config_version,
        "iteration": effective_iteration,
    }
    candidate_track.updated_at = now_utc()
    refresh_research_run_progress(db, research_run)
    db.commit()
    return TrackScoreResponse(
        track=track,
        iteration=effective_iteration,
        config_version=payload.config_version,
        candidate_count=len(scored),
        qualified_count=sum(item.selection_status == "qualified" for item in scored),
        rejected_count=sum(item.selection_status == "rejected" for item in scored),
        candidates=[serialize_candidate(item) for item in scored],
    )


@router.post(
    "/{research_run_id}/tracks/{track}/af3-batch",
    response_model=Af3BatchResponse,
)
def create_af3_batch(
    research_run_id: UUID,
    track: Literal["rna", "peptide"],
    payload: CreateAf3BatchRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    research_run = load_owned_run(db, user, research_run_id)
    if research_run.current_stage != "top10_af3":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="AF3 submission is only allowed during top10_af3",
        )
    candidate_track = load_track(db, research_run, track)
    target_sequence = "".join(
        str(
            (research_run.target_json or {}).get("sequence")
            or (research_run.target_json or {}).get("protein_sequence")
            or ""
        ).split()
    ).upper()
    if not target_sequence:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Research target has no protein sequence",
        )
    target_hash = ensure_research_target_hash(research_run)
    candidates = db.scalars(
        select(Candidate)
        .where(
            Candidate.research_run_id == research_run.id,
            Candidate.user_id == user.id,
            Candidate.track == track,
            Candidate.iteration == candidate_track.current_iteration,
            Candidate.selection_status.in_(["qualified", "selected"]),
            Candidate.rank.is_not(None),
        )
        .order_by(Candidate.rank.asc(), Candidate.created_at.asc())
        .limit(payload.max_candidates)
    ).all()
    if not candidates:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No ranked and qualified candidates are available",
        )
    links: list[ResearchTaskLink] = []
    submitted_count = 0
    reused_count = 0
    for candidate in candidates:
        task = db.get(Task, candidate.af3_task_id) if candidate.af3_task_id else None
        if task and task.user_id == user.id:
            reused_count += 1
        else:
            candidate_entity_type = "rna" if track == "rna" else "protein"
            task = create_queued_task(
                db,
                user,
                "run_alphafold3",
                {
                    "research_run_id": str(research_run.id),
                    "candidate_id": str(candidate.id),
                    "target_hash": target_hash,
                    "candidate_sequence_hash": hashlib.sha256(
                        candidate.sequence.encode("utf-8")
                    ).hexdigest(),
                    "job_name": f"{track}_{str(candidate.id)[:8]}",
                    "entities": [
                        {"type": "protein", "sequence": target_sequence},
                        {"type": candidate_entity_type, "sequence": candidate.sequence},
                    ],
                    "model_seed": payload.model_seed,
                    "num_diffusion_samples": payload.num_diffusion_samples,
                },
                session_id=research_run.session_id,
                commit=False,
            )
            candidate.af3_task_id = task.id
            submitted_count += 1
        try:
            link = link_task_to_research_run(
                db,
                user,
                research_run,
                task,
                "af3",
                candidate,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        candidate.selection_status = "selected"
        candidate.selection_reason = "轨道内排名进入 AF3 批次"
        candidate.af3_status = task.status
        candidate.updated_at = now_utc()
        links.append(link)
    candidate_track.status = "af3_running"
    candidate_track.summary_json = {
        **(candidate_track.summary_json or {}),
        "af3_candidate_count": len(candidates),
        "af3_submitted_count": submitted_count,
        "af3_reused_count": reused_count,
    }
    candidate_track.updated_at = now_utc()
    refresh_research_run_progress(db, research_run, allow_auto_advance=False)
    db.commit()
    for link in links:
        db.refresh(link)
    return Af3BatchResponse(
        track=track,
        submitted_count=submitted_count,
        reused_count=reused_count,
        tasks=[serialize_research_task(db, item) for item in links],
    )


@router.post(
    "/{research_run_id}/tasks/{task_id}",
    response_model=ResearchTaskResponse,
)
def link_research_task(
    research_run_id: UUID,
    task_id: UUID,
    payload: LinkResearchTaskRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.role not in {"admin", "research_lead"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Manual task linking requires a research lead or administrator",
        )
    research_run = load_owned_run(db, user, research_run_id)
    task = db.get(Task, task_id)
    if not task or task.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    candidate = None
    if payload.candidate_id:
        candidate = load_owned_candidate(db, user, research_run, payload.candidate_id)
    try:
        link = link_task_to_research_run(
            db,
            user,
            research_run,
            task,
            payload.role,
            candidate,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    db.commit()
    db.refresh(link)
    return serialize_research_task(db, link)


@router.post("/{research_run_id}/stages/skip", response_model=ResearchRunResponse)
def manually_skip_research_stage(
    research_run_id: UUID,
    payload: ManualStageSkipRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if user.role not in {"admin", "research_lead"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Stage skipping requires a research lead or administrator",
        )
    research_run = load_owned_run(db, user, research_run_id)
    if research_run.current_stage not in RESEARCH_STAGES:
        raise HTTPException(status_code=409, detail="Research run has an unknown stage")
    current_index = RESEARCH_STAGES.index(research_run.current_stage)
    if current_index >= len(RESEARCH_STAGES) - 1:
        raise HTTPException(status_code=409, detail="Research run is already at the final stage")
    expected_next = RESEARCH_STAGES[current_index + 1]
    if payload.next_stage != expected_next:
        raise HTTPException(
            status_code=422,
            detail=f"Manual skip can only move one stage to '{expected_next}'",
        )
    timestamp = now_utc()
    stage_state = dict(research_run.stage_state_json or {})
    current_record = dict(stage_state.get(research_run.current_stage) or {})
    current_record.update(
        {
            "status": "skipped",
            "ready": False,
            "manual_skip": {
                "reason": payload.reason.strip(),
                "user_id": str(user.id),
                "user_role": user.role,
                "created_at": timestamp.isoformat(),
            },
        }
    )
    stage_state[research_run.current_stage] = current_record
    next_record = dict(stage_state.get(expected_next) or {})
    next_record.update({"status": "running", "ready": False})
    stage_state[expected_next] = next_record
    metadata = dict(research_run.metadata_json or {})
    audit = list(metadata.get("manual_stage_audit") or [])
    audit.append(
        {
            "from_stage": research_run.current_stage,
            "to_stage": expected_next,
            "reason": payload.reason.strip(),
            "user_id": str(user.id),
            "user_role": user.role,
            "created_at": timestamp.isoformat(),
        }
    )
    metadata["manual_stage_audit"] = audit
    research_run.stage_state_json = stage_state
    research_run.metadata_json = metadata
    research_run.current_stage = expected_next
    research_run.status = "active"
    research_run.updated_at = timestamp
    db.commit()
    db.refresh(research_run)
    return serialize_run(db, research_run)


@router.get("/{research_run_id}/tasks", response_model=list[ResearchTaskResponse])
def list_research_tasks(
    research_run_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    research_run = load_owned_run(db, user, research_run_id)
    links = db.scalars(
        select(ResearchTaskLink)
        .where(
            ResearchTaskLink.research_run_id == research_run.id,
            ResearchTaskLink.user_id == user.id,
        )
        .order_by(ResearchTaskLink.created_at.asc())
    ).all()
    return [serialize_research_task(db, item) for item in links]


@router.get("/{research_run_id}/strategy", response_model=StrategyPolicyResponse)
def get_strategy_policy(
    research_run_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    research_run = load_owned_run(db, user, research_run_id)
    policy = db.scalar(
        select(StrategyPolicy).where(
            StrategyPolicy.research_run_id == research_run.id,
            StrategyPolicy.user_id == user.id,
        )
    )
    if policy is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Strategy policy is not initialized for this research run",
        )
    return serialize_strategy_policy(policy)


@router.patch("/{research_run_id}/strategy", response_model=StrategyPolicyResponse)
def update_strategy_policy(
    research_run_id: UUID,
    payload: UpdateStrategyPolicyRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    research_run = load_owned_run(db, user, research_run_id)
    policy = ensure_strategy_policy(db, user, research_run)
    expected_version = policy.version
    updates = payload.model_dump(exclude_unset=True)
    next_values = {
        "enabled": updates.get("enabled", policy.enabled),
        "alpha": updates.get("alpha", policy.alpha),
        "gamma": updates.get("gamma", policy.gamma),
        "epsilon": updates.get("epsilon", policy.epsilon),
    }
    changed_learning_parameters = any(
        field in updates for field in {"alpha", "gamma"}
    )
    q_table = policy.q_table_json
    if changed_learning_parameters:
        rows = db.scalars(
            select(StrategyTransition)
            .where(StrategyTransition.policy_id == policy.id)
            .order_by(StrategyTransition.created_at.asc(), StrategyTransition.id.asc())
        ).all()
        q_table = replay_transitions(
            [
                {
                    "state_key": item.state_key,
                    "action": item.action,
                    "reward": item.reward,
                    "next_state_key": item.next_state_key,
                }
                for item in rows
            ],
            alpha=next_values["alpha"],
            gamma=next_values["gamma"],
            action_mask=policy.action_mask_json,
        )
    timestamp = now_utc()
    updated_policy = db.execute(
        update(StrategyPolicy)
        .where(
            StrategyPolicy.id == policy.id,
            StrategyPolicy.user_id == user.id,
            StrategyPolicy.version == expected_version,
        )
        .values(
            **next_values,
            q_table_json=q_table,
            version=expected_version + 1,
            updated_at=timestamp,
        )
    )
    if updated_policy.rowcount != 1:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Strategy policy changed concurrently; reload and retry",
        )
    research_run.policy_version = f"qtable-v{expected_version + 1}"
    research_run.updated_at = timestamp
    db.commit()
    policy = db.get(StrategyPolicy, policy.id)
    if policy is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Strategy policy disappeared during update",
        )
    db.refresh(policy)
    return serialize_strategy_policy(policy)


@router.get(
    "/{research_run_id}/strategy/feedback-options",
    response_model=list[StrategyFeedbackOptionResponse],
)
def list_strategy_feedback_options(
    research_run_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    research_run = load_owned_run(db, user, research_run_id)
    query = select(AgentMessage).where(
        AgentMessage.user_id == user.id,
        AgentMessage.role == "assistant",
    )
    if research_run.session_id is not None:
        query = query.where(AgentMessage.session_id == research_run.session_id)
    messages = db.scalars(
        query.order_by(AgentMessage.created_at.desc(), AgentMessage.id.desc())
    ).all()
    used_keys = used_feedback_trace_keys(db, research_run, user)
    options: list[StrategyFeedbackOptionResponse] = []
    for message in messages:
        metadata = message.metadata_json or {}
        if feedback_message_research_run_id(metadata) != str(research_run.id):
            continue
        for trace in reversed(extract_agent_action_traces(metadata)):
            stage = str(trace.state.get("stage") or "")
            if stage not in RESEARCH_STAGES:
                continue
            # ADR 0012：不再按阶段裁剪，只要求动作是当前目录中的已授权工具。
            if trace.action not in available_actions():
                continue
            options.append(
                serialize_feedback_option(
                    db,
                    user,
                    research_run,
                    message,
                    trace,
                    used_keys,
                )
            )
    return options


@router.post(
    "/{research_run_id}/strategy/feedback",
    response_model=StrategyFeedbackResponse,
)
def submit_strategy_feedback(
    research_run_id: UUID,
    payload: SubmitStrategyFeedbackRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    research_run = load_owned_run(db, user, research_run_id)
    policy = ensure_strategy_policy(db, user, research_run)
    if not policy.enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Strategy feedback is disabled for this research run",
        )
    message = load_feedback_message(
        db,
        user,
        research_run,
        payload.agent_message_id,
    )
    trace = find_message_trace(message, payload.tool_call_id)
    trace_key = feedback_trace_key(message.id, trace.tool_call_id)
    transition_id = feedback_transition_id(
        research_run.id,
        message.id,
        trace.tool_call_id,
    )
    if db.get(StrategyTransition, transition_id) is not None or (
        trace_key in used_feedback_trace_keys(db, research_run, user)
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Feedback has already been submitted for this Agent action",
        )
    trace_stage = str(trace.state.get("stage") or "")
    if trace_stage not in RESEARCH_STAGES:
        raise HTTPException(status_code=422, detail="Unknown Agent action trace stage")
    allowed_actions = available_actions()
    if trace.action not in allowed_actions:
        raise HTTPException(
            status_code=422,
            detail="Action is not an authorized tool in the current catalog",
        )
    trace_outcome = reconcile_action_trace(db, user, research_run, trace)
    next_state = build_policy_state(db, research_run)
    current_key = trace.state_key
    next_key = state_key(next_state)
    q_table = {
        key: dict(value)
        for key, value in (policy.q_table_json or {}).items()
    }
    q_before, q_after = update_q_value(
        q_table,
        current_state_key=current_key,
        action=trace.action,
        reward=payload.reward,
        next_state_key=next_key,
        alpha=policy.alpha,
        gamma=policy.gamma,
        allowed_next_actions=allowed_actions,
    )
    old_policy_version = policy.version
    new_policy_version = old_policy_version + 1
    metrics = dict(policy.metrics_json or {})
    metrics["feedback_count"] = int(metrics.get("feedback_count", 0)) + 1
    metrics["reward_sum"] = float(metrics.get("reward_sum", 0.0)) + payload.reward
    if payload.reward > 0:
        metrics["positive_reward_count"] = int(metrics.get("positive_reward_count", 0)) + 1
    outcome = {
        "source": "human",
        "agent_message_id": str(message.id),
        "tool_call_id": trace.tool_call_id,
        "action": trace.action,
        **trace_outcome,
    }
    if payload.comment and payload.comment.strip():
        outcome["comment"] = payload.comment.strip()
    if outcome["success"] is True:
        metrics["success_count"] = int(metrics.get("success_count", 0)) + 1
    if outcome["invalid_call"] is True:
        metrics["invalid_call_count"] = int(metrics.get("invalid_call_count", 0)) + 1
    policy_update = db.execute(
        update(StrategyPolicy)
        .where(
            StrategyPolicy.id == policy.id,
            StrategyPolicy.version == old_policy_version,
        )
        .values(
            version=new_policy_version,
            q_table_json=q_table,
            metrics_json=metrics,
            updated_at=now_utc(),
        )
    )
    if policy_update.rowcount != 1:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Strategy policy changed concurrently; reload and retry feedback",
        )
    transition = StrategyTransition(
        id=transition_id,
        policy_id=policy.id,
        research_run_id=research_run.id,
        user_id=user.id,
        state_key=current_key,
        action=trace.action,
        reward=payload.reward,
        next_state_key=next_key,
        q_before=q_before,
        q_after=q_after,
        outcome_json=outcome,
        policy_version=new_policy_version,
    )
    db.add(transition)
    db.flush()
    research_run.policy_version = f"qtable-v{new_policy_version}"
    refresh_research_run_progress(db, research_run, allow_auto_advance=False)
    research_run.updated_at = now_utc()
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Feedback has already been submitted for this Agent action",
        ) from exc
    db.refresh(policy)
    db.refresh(transition)
    return StrategyFeedbackResponse(
        policy=serialize_strategy_policy(policy),
        transition=serialize_strategy_transition(transition),
    )


@router.get(
    "/{research_run_id}/strategy/transitions",
    response_model=list[StrategyTransitionResponse],
)
def list_strategy_transitions(
    research_run_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    research_run = load_owned_run(db, user, research_run_id)
    rows = db.scalars(
        select(StrategyTransition)
        .where(
            StrategyTransition.research_run_id == research_run.id,
            StrategyTransition.user_id == user.id,
        )
        .order_by(StrategyTransition.created_at.asc(), StrategyTransition.id.asc())
    ).all()
    return [serialize_strategy_transition(item) for item in rows]


@router.post(
    "/{research_run_id}/strategy/replay",
    response_model=StrategyReplayResponse,
)
def replay_strategy_policy(
    research_run_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    research_run = load_owned_run(db, user, research_run_id)
    policy = ensure_strategy_policy(db, user, research_run)
    rows = db.scalars(
        select(StrategyTransition)
        .where(
            StrategyTransition.policy_id == policy.id,
            StrategyTransition.user_id == user.id,
        )
        .order_by(StrategyTransition.created_at.asc(), StrategyTransition.id.asc())
    ).all()
    replayed = replay_transitions(
        [
            {
                "state_key": item.state_key,
                "action": item.action,
                "reward": item.reward,
                "next_state_key": item.next_state_key,
            }
            for item in rows
        ],
        alpha=policy.alpha,
        gamma=policy.gamma,
        action_mask=policy.action_mask_json,
    )
    db.commit()
    return StrategyReplayResponse(
        matches_current=replayed == (policy.q_table_json or {}),
        transition_count=len(rows),
        q_table=replayed,
    )


@router.post("/{research_run_id}/report")
def create_research_report(
    research_run_id: UUID,
    payload: GenerateResearchReportRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    research_run = load_owned_run(db, user, research_run_id)
    return generate_research_report(
        db,
        user,
        research_run,
        operation_id=f"report_{uuid4().hex[:12]}",
        title=payload.title,
    )


@router.patch(
    "/{research_run_id}/candidates/{candidate_id}",
    response_model=CandidateResponse,
)
def update_candidate(
    research_run_id: UUID,
    candidate_id: UUID,
    payload: UpdateCandidateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    research_run = load_owned_run(db, user, research_run_id)
    candidate = load_owned_candidate(db, user, research_run, candidate_id)
    metadata = dict(candidate.metadata_json or {})
    prior_curation = metadata.get("curation")
    previous_status = (
        prior_curation.get("previous_selection_status")
        if isinstance(prior_curation, dict)
        else candidate.selection_status
    )
    if payload.curation_decision == "clear":
        candidate.selection_status = str(previous_status or "pending")
        candidate.selection_reason = None
        metadata.pop("curation", None)
    else:
        if not isinstance(prior_curation, dict):
            previous_status = candidate.selection_status
        candidate.selection_status = payload.curation_decision
        candidate.selection_reason = "人工筛选决定（不等同于服务端计算评分）"
        metadata["curation"] = {
            "decision": payload.curation_decision,
            "comment": payload.curator_comment,
            "user_id": str(user.id),
            "created_at": now_utc().isoformat(),
            "previous_selection_status": previous_status,
        }
    candidate.metadata_json = metadata
    candidate.updated_at = now_utc()
    research_run.updated_at = now_utc()
    db.commit()
    db.refresh(candidate)
    return serialize_candidate(candidate)
