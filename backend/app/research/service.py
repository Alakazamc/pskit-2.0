from __future__ import annotations

from collections import Counter
import hashlib
import json
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    Artifact,
    Candidate,
    CandidateTrack,
    ResearchRun,
    ResearchTaskLink,
    StrategyTransition,
    Task,
    User,
    ensure_utc,
    now_utc,
)


RESEARCH_STAGES = (
    "target_analysis",
    "homology_analysis",
    "binding_assessment",
    "candidate_generation",
    "candidate_evaluation",
    "top10_af3",
    "strategy_feedback",
    "report",
)
STAGE_FOR_ROLE = {
    "homology": "homology_analysis",
    "binding": "binding_assessment",
    "candidate_generation": "candidate_generation",
    "candidate_evaluation": "candidate_evaluation",
    "af3": "top10_af3",
    "strategy_feedback": "strategy_feedback",
    "report": "report",
}
EXPECTED_ROLE_BY_TASK_TYPE = {
    "search_sequence_homologs": "homology",
    "search_structure_homologs": "homology",
    "predict_binding_sites": "binding",
    "predict_interaction": "binding",
    "extract_empirical_features": "binding",
    "generate_coral_candidates": "candidate_generation",
    "generate_pepccd_candidates": "candidate_generation",
    "run_alphafold3": "af3",
}
REQUIRED_TASK_TYPES = {
    "homology_analysis": {
        "search_sequence_homologs",
        "search_structure_homologs",
    },
    "candidate_generation": {
        "generate_coral_candidates",
        "generate_pepccd_candidates",
    },
}
AUTO_ADVANCE_STAGES = {
    "homology_analysis",
    "binding_assessment",
    "candidate_generation",
    "candidate_evaluation",
    "top10_af3",
}
ACTIVE_TASK_STATUSES = {"queued", "running"}
TERMINAL_TASK_STATUSES = {"succeeded", "failed"}
GENERATED_TRACK_STATUSES = {"evaluating", "ranked", "af3_running", "completed"}
RANKED_TRACK_STATUSES = {"ranked", "af3_running", "completed"}


def public_task_error_message(task: Task) -> str | None:
    """返回可进入 API 与科研阶段快照的稳定错误，不暴露服务器原始异常。"""

    if not task.error_type:
        return None
    messages = {
        "Af3GpuMemoryUnavailable": (
            "AlphaFold3 GPU 显存不足或当前无可用 GPU；"
            "请等待资源释放，或联系管理员切换计算设备后重新提交。"
        ),
        "Af3ExecutionTimeout": (
            "AlphaFold3 在受控时限内未完成；当前 GPU 可能繁忙。"
            "请记录任务编号，等待资源释放后重新提交。"
        ),
        "TaskAttemptsExhausted": "任务重试次数已用尽，请检查依赖和服务器日志后再重试。",
        "TaskLeaseExpired": "科研任务执行器失联；系统已保留原尝试并创建隔离重试。",
        "TaskWorkerError": "科研任务执行失败，请根据任务编号查看受控服务器日志。",
        "TimeoutError": "科研任务执行超时，可在确认依赖正常后重试。",
    }
    return messages.get(
        task.error_type,
        "任务执行失败，请记录任务编号并查看受控服务器日志。",
    )


def canonical_target_hash(target: dict) -> str:
    canonical = json.dumps(
        target or {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def ensure_research_target_hash(research_run: ResearchRun) -> str:
    target_hash = canonical_target_hash(research_run.target_json or {})
    metadata = dict(research_run.metadata_json or {})
    if metadata.get("target_hash") != target_hash:
        metadata["target_hash"] = target_hash
        research_run.metadata_json = metadata
        research_run.updated_at = now_utc()
    return target_hash


def _task_record(task: Task) -> dict:
    output = task.output_json or {}
    artifacts = output.get("artifacts") if isinstance(output, dict) else []
    artifact_ids = [
        str(item.get("artifact_id"))
        for item in artifacts or []
        if isinstance(item, dict) and item.get("artifact_id")
    ]
    return {
        "task_type": task.task_type,
        "status": task.status,
        "progress": task.progress,
        "error_type": task.error_type,
        "error": public_task_error_message(task),
        "artifact_ids": artifact_ids,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
    }


def _latest_tasks_by_type(tasks: list[Task]) -> dict[str, Task]:
    latest: dict[str, Task] = {}
    for task in sorted(
        tasks,
        key=lambda item: (ensure_utc(item.created_at), str(item.id)),
    ):
        latest[task.task_type] = task
    return latest


def _task_gate(stage: str, tasks: list[Task]) -> dict:
    latest = _latest_tasks_by_type(tasks)
    required = sorted(REQUIRED_TASK_TYPES.get(stage, set()))
    relevant = [latest[name] for name in required if name in latest] if required else list(latest.values())
    missing = [name for name in required if name not in latest]
    active = [task.task_type for task in relevant if task.status in ACTIVE_TASK_STATUSES]
    failed = [task.task_type for task in relevant if task.status == "failed"]
    succeeded = [task.task_type for task in relevant if task.status == "succeeded"]

    if stage == "binding_assessment" and not relevant:
        missing = ["至少一个结合判断任务"]

    ready = bool(relevant) and not missing and not active and not failed and all(
        task.status == "succeeded" for task in relevant
    )
    blockers = [
        *[f"缺少任务：{name}" for name in missing],
        *[f"任务仍在运行：{name}" for name in active],
        *[f"任务失败：{name}" for name in failed],
    ]
    return {
        "ready": ready,
        "required_task_types": required,
        "missing": missing,
        "active": active,
        "failed": failed,
        "succeeded": succeeded,
        "blockers": blockers,
    }


def _track_snapshots(db: Session, research_run: ResearchRun) -> dict[str, dict]:
    tracks = db.scalars(
        select(CandidateTrack)
        .where(CandidateTrack.research_run_id == research_run.id)
        .order_by(CandidateTrack.track.asc())
    ).all()
    counts = {
        (track_name, int(iteration)): int(count)
        for track_name, iteration, count in db.execute(
            select(Candidate.track, Candidate.iteration, func.count(Candidate.id))
            .where(Candidate.research_run_id == research_run.id)
            .group_by(Candidate.track, Candidate.iteration)
        ).all()
    }
    total_counts = dict(
        db.execute(
            select(Candidate.track, func.count(Candidate.id))
            .where(Candidate.research_run_id == research_run.id)
            .group_by(Candidate.track)
        ).all()
    )
    qualified_counts = {
        (track_name, int(iteration)): int(count)
        for track_name, iteration, count in db.execute(
            select(Candidate.track, Candidate.iteration, func.count(Candidate.id))
            .where(
                Candidate.research_run_id == research_run.id,
                Candidate.selection_status.in_(["qualified", "selected"]),
            )
            .group_by(Candidate.track, Candidate.iteration)
        ).all()
    }
    for track in tracks:
        selected = db.scalars(
            select(Candidate).where(
                Candidate.research_run_id == research_run.id,
                Candidate.track == track.track,
                Candidate.iteration == track.current_iteration,
                Candidate.selection_status == "selected",
                Candidate.af3_task_id.is_not(None),
            )
        ).all()
        if not selected:
            continue
        af3_statuses: list[str] = []
        for candidate in selected:
            task = db.get(Task, candidate.af3_task_id) if candidate.af3_task_id else None
            effective_status = task.status if task else "failed"
            if effective_status == "succeeded":
                structure_artifact = (
                    db.get(Artifact, candidate.af3_artifact_id)
                    if candidate.af3_artifact_id is not None
                    else None
                )
                raw_result_id = (candidate.metadata_json or {}).get(
                    "af3_result_artifact_id"
                )
                try:
                    result_artifact = (
                        db.get(Artifact, UUID(str(raw_result_id)))
                        if raw_result_id
                        else None
                    )
                except (TypeError, ValueError):
                    result_artifact = None
                if (
                    structure_artifact is None
                    or result_artifact is None
                    or structure_artifact.task_id != candidate.af3_task_id
                    or result_artifact.task_id != candidate.af3_task_id
                ):
                    effective_status = "missing_artifact"
            af3_statuses.append(effective_status)
            if candidate.af3_status != effective_status:
                candidate.af3_status = effective_status
                candidate.updated_at = now_utc()
        if all(item in TERMINAL_TASK_STATUSES | {"missing_artifact"} for item in af3_statuses):
            desired_status = (
                "completed"
                if all(item == "succeeded" for item in af3_statuses)
                else "failed"
            )
        else:
            desired_status = "af3_running"
        desired_summary = {
            **(track.summary_json or {}),
            "af3_status_counts": dict(Counter(af3_statuses)),
        }
        if track.status != desired_status or track.summary_json != desired_summary:
            track.status = desired_status
            track.summary_json = desired_summary
            track.updated_at = now_utc()
    return {
        track.track: {
            "status": track.status,
            "current_iteration": track.current_iteration,
            "candidate_count": counts.get(
                (track.track, track.current_iteration),
                0,
            ),
            "total_candidate_count": int(total_counts.get(track.track, 0)),
            "qualified_count": qualified_counts.get(
                (track.track, track.current_iteration),
                0,
            ),
            "summary": track.summary_json or {},
        }
        for track in tracks
    }


def _track_gate(stage: str, tracks: dict[str, dict]) -> dict:
    blockers: list[str] = []
    active: list[str] = []
    failed: list[str] = []
    ready = True
    for track_name in ("rna", "peptide"):
        snapshot = tracks.get(track_name)
        if not snapshot:
            ready = False
            blockers.append(f"缺少候选轨道：{track_name}")
            continue
        status = str(snapshot.get("status") or "pending")
        if status == "failed":
            failed.append(track_name)
        elif status in {"generating", "evaluating", "af3_running"}:
            active.append(track_name)
        candidate_count = int(snapshot.get("candidate_count") or 0)
        qualified_count = int(snapshot.get("qualified_count") or 0)
        if stage == "candidate_generation":
            accepted = status in GENERATED_TRACK_STATUSES and candidate_count > 0
            reason = (
                f"{track_name} 轨道尚未完成候选生成"
                if candidate_count > 0
                else f"{track_name} 轨道尚无候选"
            )
        elif stage == "candidate_evaluation":
            accepted = status in RANKED_TRACK_STATUSES and qualified_count > 0
            reason = (
                f"{track_name} 轨道尚未完成评分"
                if status not in RANKED_TRACK_STATUSES
                else f"{track_name} 轨道没有合格候选"
            )
        elif stage == "top10_af3":
            accepted = status == "completed"
            reason = f"{track_name} 轨道 AF3 尚未全部成功完成"
        else:
            accepted = True
            reason = ""
        if not accepted:
            ready = False
            blockers.append(reason)
    return {
        "ready": ready,
        "blockers": blockers,
        "active": active,
        "failed": failed,
        "tracks": tracks,
    }


def _stage_gate(
    stage: str,
    tasks_by_stage: dict[str, list[Task]],
    tracks: dict[str, dict],
    feedback_count: int,
) -> dict:
    if stage in {"homology_analysis", "binding_assessment"}:
        return _task_gate(stage, tasks_by_stage.get(stage, []))
    if stage in {"candidate_generation", "candidate_evaluation", "top10_af3"}:
        track_gate = _track_gate(stage, tracks)
        if stage == "candidate_generation":
            task_gate = _task_gate(stage, tasks_by_stage.get(stage, []))
            return {
                **track_gate,
                "ready": track_gate["ready"] and task_gate["ready"],
                "blockers": [*task_gate["blockers"], *track_gate["blockers"]],
                "required_task_types": task_gate["required_task_types"],
                "missing": task_gate["missing"],
                "active": [*task_gate["active"], *track_gate["active"]],
                "failed": [*task_gate["failed"], *track_gate["failed"]],
                "succeeded": task_gate["succeeded"],
            }
        return track_gate
    if stage == "strategy_feedback":
        return {
            "ready": feedback_count > 0,
            "blockers": (
                []
                if feedback_count > 0
                else ["至少记录一条可追溯的 Agent 策略反馈"]
            ),
            "feedback_count": feedback_count,
        }
    return {"ready": False, "blockers": []}


def _status_from_gate(gate: dict) -> str:
    if gate.get("ready"):
        return "succeeded"
    if gate.get("failed"):
        return "failed"
    if gate.get("active"):
        return "running"
    return "pending"


def refresh_research_run_progress(
    db: Session,
    research_run: ResearchRun,
    *,
    allow_auto_advance: bool = True,
    persist: bool = True,
) -> dict:
    """根据持久化任务与双轨状态重建阶段事实，并只在满足汇合门时自动推进。"""

    if persist:
        ensure_research_target_hash(research_run)
    original_stage = research_run.current_stage
    original_status = research_run.status
    original_state = research_run.stage_state_json or {}
    current_stage = original_stage
    stage_state = {
        str(key): (
            dict(value)
            if isinstance(value, dict)
            else {"status": str(value)}
        )
        for key, value in original_state.items()
    }
    links = db.scalars(
        select(ResearchTaskLink)
        .where(ResearchTaskLink.research_run_id == research_run.id)
        .order_by(ResearchTaskLink.created_at.asc())
    ).all()
    tasks_by_stage: dict[str, list[Task]] = {}
    for link in links:
        stage = STAGE_FOR_ROLE.get(link.role)
        task = db.get(Task, link.task_id)
        if stage and task:
            tasks_by_stage.setdefault(stage, []).append(task)

    for stage, tasks in tasks_by_stage.items():
        existing = dict(stage_state.get(stage) or {})
        existing["tasks"] = {str(task.id): _task_record(task) for task in tasks}
        existing["task_counts"] = dict(Counter(task.status for task in tasks))
        stage_state[stage] = existing

    tracks = _track_snapshots(db, research_run)
    feedback_count = int(
        db.scalar(
            select(func.count(StrategyTransition.id)).where(
                StrategyTransition.research_run_id == research_run.id
            )
        )
        or 0
    )
    for stage in ("candidate_generation", "candidate_evaluation", "top10_af3"):
        existing = dict(stage_state.get(stage) or {})
        existing["tracks"] = tracks
        stage_state[stage] = existing

    while current_stage in RESEARCH_STAGES:
        existing = dict(stage_state.get(current_stage) or {})
        if current_stage == "report":
            report_ready = bool(
                existing.get("status") == "succeeded"
                and existing.get("artifact_id")
            )
            gate = {
                "ready": report_ready,
                "blockers": (
                    []
                    if report_ready
                    else ["尚未生成本次科研运行的最终报告"]
                ),
            }
        else:
            gate = _stage_gate(
                current_stage,
                tasks_by_stage,
                tracks,
                feedback_count,
            )
        if existing.get("status") != "skipped":
            existing["status"] = _status_from_gate(gate)
        existing["ready"] = bool(gate.get("ready"))
        existing["blockers"] = list(gate.get("blockers") or [])
        for key in (
            "required_task_types",
            "missing",
            "active",
            "failed",
            "succeeded",
            "feedback_count",
        ):
            if key in gate:
                existing[key] = gate[key]
        stage_state[current_stage] = existing

        if (
            not allow_auto_advance
            or current_stage not in AUTO_ADVANCE_STAGES
            or not gate.get("ready")
        ):
            break
        current_index = RESEARCH_STAGES.index(current_stage)
        if current_index >= len(RESEARCH_STAGES) - 1:
            break
        next_stage = RESEARCH_STAGES[current_index + 1]
        current_stage = next_stage
        stage_state.setdefault(
            next_stage,
            {"status": "pending", "ready": False, "blockers": []},
        )

    current_record = stage_state.get(current_stage) or {}
    if current_stage == "target_analysis" and original_status == "draft":
        next_status = "draft"
    elif current_record.get("status") == "failed":
        next_status = "failed"
    elif original_status not in {"completed", "archived"}:
        next_status = "active"
    else:
        next_status = original_status

    if persist:
        research_run.current_stage = current_stage
        research_run.stage_state_json = stage_state
        research_run.status = next_status
        if (
            current_stage != original_stage
            or next_status != original_status
            or stage_state != original_state
        ):
            research_run.updated_at = now_utc()
    return {
        "current_stage": current_stage,
        "status": next_status,
        "stage_state": stage_state,
        "tracks": tracks,
    }


def _is_session_native_context(research_run: ResearchRun) -> bool:
    """会话原生上下文由服务端创建，不把旧科研阶段作为用户工作流门槛。"""

    return (research_run.metadata_json or {}).get("origin") == "session_native"


def link_task_to_research_run(
    db: Session,
    user: User,
    research_run: ResearchRun,
    task: Task,
    role: str,
    candidate: Candidate | None = None,
) -> ResearchTaskLink:
    if task.user_id != user.id or research_run.user_id != user.id:
        raise ValueError("Task and research run must have the same owner")
    expected_role = EXPECTED_ROLE_BY_TASK_TYPE.get(task.task_type)
    if expected_role and role != expected_role:
        raise ValueError(
            f"Task type '{task.task_type}' must use research role '{expected_role}'"
        )
    expected_stage = STAGE_FOR_ROLE.get(role)
    if (
        expected_stage
        and research_run.current_stage != expected_stage
        and not _is_session_native_context(research_run)
    ):
        raise ValueError(
            f"Research role '{role}' is only allowed during stage "
            f"'{expected_stage}'"
        )
    if role == "af3" and candidate is None:
        raise ValueError("AF3 research tasks must be linked to a candidate")
    if candidate is not None and (
        candidate.user_id != user.id
        or candidate.research_run_id != research_run.id
    ):
        raise ValueError("Candidate does not belong to this research run")
    target_hash = ensure_research_target_hash(research_run)
    task_input = dict(task.input_json or {})
    existing_target_hash = task_input.get("target_hash")
    if existing_target_hash and existing_target_hash != target_hash:
        raise ValueError("Task target hash does not match the research run")
    task_input["research_run_id"] = str(research_run.id)
    task_input["target_hash"] = target_hash
    if candidate is not None:
        candidate_hash = hashlib.sha256(
            candidate.sequence.encode("utf-8")
        ).hexdigest()
        existing_candidate_hash = task_input.get("candidate_sequence_hash")
        if (
            existing_candidate_hash
            and existing_candidate_hash != candidate_hash
        ):
            raise ValueError("Task candidate hash does not match the linked candidate")
        task_input["candidate_id"] = str(candidate.id)
        task_input["candidate_sequence_hash"] = candidate_hash
    task.input_json = task_input
    existing = db.scalar(select(ResearchTaskLink).where(ResearchTaskLink.task_id == task.id))
    if existing:
        expected_candidate_id = candidate.id if candidate else None
        if (
            existing.research_run_id != research_run.id
            or existing.user_id != user.id
            or existing.role != role
            or existing.candidate_id != expected_candidate_id
        ):
            raise ValueError("Task is already linked to a different research context")
        return existing
    link = ResearchTaskLink(
        research_run_id=research_run.id,
        task_id=task.id,
        candidate_id=candidate.id if candidate else None,
        user_id=user.id,
        role=role,
    )
    db.add(link)
    db.flush()

    if role == "candidate_generation":
        track_name = "rna" if task.task_type == "generate_coral_candidates" else "peptide"
        track = db.scalar(
            select(CandidateTrack).where(
                CandidateTrack.research_run_id == research_run.id,
                CandidateTrack.track == track_name,
            )
        )
        if track and track.status in {"pending", "failed"}:
            track.status = "generating"
            track.updated_at = now_utc()
    elif role == "af3" and candidate is not None:
        candidate.af3_status = task.status
        candidate.updated_at = now_utc()

    refresh_research_run_progress(db, research_run, allow_auto_advance=False)
    return link
