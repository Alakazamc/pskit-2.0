from __future__ import annotations

import json
import hashlib
from typing import Iterable

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    Candidate,
    CandidateTrack,
    ResearchRun,
    ResearchTaskLink,
    StrategyPolicy,
    Task,
    User,
    now_utc,
)
from app.research.service import refresh_research_run_progress
from app.tools.catalog import TOOL_CATALOG


CONTROL_ACTIONS = frozenset(
    {
        "read_result_file",
        "generate_session_report",
        "generate_research_report",
        "generate_harness_report",
    }
)
# ADR 0012：阶段裁剪已删除，工具可用性不再受阶段限制。这里只保留「哪些工具的
# 真实执行可以成为科学证据」这一判定，它按工具性质划分，与任何阶段状态无关。
# 控制类与报告类工具不得成为科学证据。
SCIENTIFIC_ACTIONS: frozenset[str] = frozenset(
    {
        "search_pdb",
        "download_pdb_file",
        "fetch_pdb_info",
        "search_uniprot",
        "fetch_uniprot_entry",
        "serpapi_search",
        "search_rnacentral",
        "fetch_rnacentral_entry",
        "search_sequence_homologs",
        "search_structure_homologs",
        "split_pdb_by_chain",
        "split_complex",
        "extract_fragment",
        "calculate_contact_map",
        "annotate_binding_pairs",
        "predict_binding_sites",
        "predict_interaction",
        "extract_empirical_features",
        "generate_coral_candidates",
        "generate_pepccd_candidates",
        "score_research_candidates",
        "submit_research_top10_af3",
        "run_alphafold3",
    }
)


def state_key(state: dict) -> str:
    return json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def available_actions() -> list[str]:
    """返回全部已授权工具，按目录顺序；对话式内核不按阶段裁剪。"""

    return [item.name for item in TOOL_CATALOG]


def available_actions_snapshot() -> dict[str, list[str]]:
    """策略行记录的可用动作快照，取代按阶段分组的旧 action mask。"""

    return {"available": available_actions()}


def _stage_from_key(key: str) -> str:
    try:
        state = json.loads(key)
    except json.JSONDecodeError:
        return ""
    return str(state.get("stage") or "") if isinstance(state, dict) else ""


def update_q_value(
    q_table: dict,
    *,
    current_state_key: str,
    action: str,
    reward: float,
    next_state_key: str,
    alpha: float,
    gamma: float,
    allowed_next_actions: Iterable[str],
) -> tuple[float, float]:
    current_row = dict(q_table.get(current_state_key) or {})
    next_row = dict(q_table.get(next_state_key) or {})
    q_before = float(current_row.get(action, 0.0))
    next_values = [float(next_row.get(item, 0.0)) for item in allowed_next_actions]
    next_best = max(next_values, default=0.0)
    q_after = q_before + alpha * (float(reward) + gamma * next_best - q_before)
    q_after = round(q_after, 12)
    current_row[action] = q_after
    q_table[current_state_key] = current_row
    return q_before, q_after


def replay_transitions(
    transitions: list[dict],
    *,
    alpha: float,
    gamma: float,
    action_mask: dict[str, list[str]] | None = None,
) -> dict:
    q_table: dict[str, dict[str, float]] = {}
    for transition in transitions:
        next_key = str(transition["next_state_key"])
        stage = _stage_from_key(next_key)
        allowed_next = (action_mask or {}).get(next_key)
        if allowed_next is None:
            allowed_next = (action_mask or {}).get(stage)
        if allowed_next is None:
            allowed_next = (action_mask or {}).get("available", available_actions())
        update_q_value(
            q_table,
            current_state_key=str(transition["state_key"]),
            action=str(transition["action"]),
            reward=float(transition["reward"]),
            next_state_key=next_key,
            alpha=alpha,
            gamma=gamma,
            allowed_next_actions=allowed_next,
        )
    return q_table


def ensure_strategy_policy(
    db: Session,
    user: User,
    research_run: ResearchRun,
) -> StrategyPolicy:
    current_mask = available_actions_snapshot()
    policy = db.scalar(
        select(StrategyPolicy).where(
            StrategyPolicy.research_run_id == research_run.id,
            StrategyPolicy.user_id == user.id,
        )
    )
    if policy:
        if policy.action_mask_json != current_mask:
            expected_version = policy.version
            timestamp = now_utc()
            upgraded = db.execute(
                update(StrategyPolicy)
                .where(
                    StrategyPolicy.id == policy.id,
                    StrategyPolicy.user_id == user.id,
                    StrategyPolicy.version == expected_version,
                )
                .values(
                    action_mask_json=current_mask,
                    version=expected_version + 1,
                    updated_at=timestamp,
                )
            )
            if upgraded.rowcount != 1:
                db.expire(policy)
                db.refresh(policy)
                return ensure_strategy_policy(db, user, research_run)
            db.expire(policy)
            db.refresh(policy)
            research_run.policy_version = f"qtable-v{policy.version}"
            research_run.updated_at = timestamp
            db.flush()
        return policy
    policy = StrategyPolicy(
        research_run_id=research_run.id,
        user_id=user.id,
        action_mask_json=current_mask,
        q_table_json={},
        metrics_json={"feedback_count": 0},
    )
    try:
        with db.begin_nested():
            db.add(policy)
            db.flush()
        research_run.policy_version = f"qtable-v{policy.version}"
        research_run.updated_at = now_utc()
        db.flush()
        return policy
    except IntegrityError:
        # 另一并发请求已完成唯一策略初始化；只回滚保存点并复用权威行。
        existing = db.scalar(
            select(StrategyPolicy).where(
                StrategyPolicy.research_run_id == research_run.id,
                StrategyPolicy.user_id == user.id,
            )
        )
        if existing is None:
            raise
        return ensure_strategy_policy(db, user, research_run)


def backfill_strategy_policies(db: Session) -> int:
    """显式、幂等地迁移既有科研运行，避免用 GET 修复数据。"""

    repaired = 0
    research_runs = db.scalars(
        select(ResearchRun).order_by(ResearchRun.created_at.asc())
    ).all()
    for research_run in research_runs:
        user = db.get(User, research_run.user_id)
        if user is None:
            continue
        previous_version = research_run.policy_version
        policy = ensure_strategy_policy(db, user, research_run)
        expected_version = f"qtable-v{policy.version}"
        if research_run.policy_version != expected_version:
            research_run.policy_version = expected_version
            research_run.updated_at = now_utc()
        if previous_version != research_run.policy_version:
            repaired += 1
    db.commit()
    return repaired


def _task_scope_predicate(research_run: ResearchRun):
    """Include provenance-linked tasks and every task owned by this chat session."""

    linked_task_ids = select(ResearchTaskLink.task_id).where(
        ResearchTaskLink.research_run_id == research_run.id,
        ResearchTaskLink.user_id == research_run.user_id,
    )
    predicates = [Task.id.in_(linked_task_ids)]
    if research_run.session_id is not None:
        predicates.append(Task.session_id == research_run.session_id)
    return or_(*predicates)


def build_policy_state(
    db: Session,
    research_run: ResearchRun,
) -> dict:
    refresh_research_run_progress(db, research_run)
    candidate_counts = dict(
        db.execute(
            select(Candidate.track, func.count(Candidate.id))
            .where(Candidate.research_run_id == research_run.id)
            .group_by(Candidate.track)
        ).all()
    )
    track_statuses = dict(
        db.execute(
            select(CandidateTrack.track, CandidateTrack.status).where(
                CandidateTrack.research_run_id == research_run.id
            )
        ).all()
    )
    task_counts = dict(
        db.execute(
            select(Task.status, func.count(Task.id))
            .where(
                Task.user_id == research_run.user_id,
                _task_scope_predicate(research_run),
            )
            .group_by(Task.status)
        ).all()
    )
    stage_record = dict(
        (research_run.stage_state_json or {}).get(research_run.current_stage) or {}
    )
    return {
        "stage": research_run.current_stage,
        "target_available": bool(research_run.target_json),
        "rna_track_status": str(track_statuses.get("rna", "pending")),
        "peptide_track_status": str(track_statuses.get("peptide", "pending")),
        "rna_candidate_count": int(candidate_counts.get("rna", 0)),
        "peptide_candidate_count": int(candidate_counts.get("peptide", 0)),
        "queued_task_count": int(task_counts.get("queued", 0)),
        "running_task_count": int(task_counts.get("running", 0)),
        "failed_task_count": int(task_counts.get("failed", 0)),
        "stage_ready": bool(stage_record.get("ready")),
        "stage_blocker_count": len(stage_record.get("blockers") or []),
    }


def build_policy_decision(
    db: Session,
    user: User,
    research_run: ResearchRun,
) -> dict:
    policy = ensure_strategy_policy(db, user, research_run)
    state = build_policy_state(db, research_run)
    allowed = available_actions()
    stage_gate = dict(
        (research_run.stage_state_json or {}).get(research_run.current_stage) or {}
    )
    required_actions = []
    for action in [
        *(stage_gate.get("failed") or []),
        *(stage_gate.get("missing") or []),
    ]:
        if (
            isinstance(action, str)
            and action in allowed
            and action not in required_actions
        ):
            required_actions.append(action)
    row = dict((policy.q_table_json or {}).get(state_key(state)) or {})
    recommended = list(allowed)
    selection_mode = "disabled"
    if policy.enabled:
        recommended = sorted(
            allowed,
            key=lambda action: (-float(row.get(action, 0.0)), allowed.index(action)),
        )
        selection_mode = "greedy"
    if policy.enabled and policy.epsilon > 0 and len(recommended) > 1:
        digest = hashlib.sha256(
            f"{policy.id}:{policy.version}:{state_key(state)}".encode("utf-8")
        ).hexdigest()
        sample = int(digest[:8], 16) / 0xFFFFFFFF
        if sample < policy.epsilon:
            offset = int(digest[8:16], 16) % len(recommended)
            recommended = [*recommended[offset:], *recommended[:offset]]
            selection_mode = "deterministic_exploration"
    if required_actions:
        # ADR 0012：这些缺失项只是推荐排序提示，不再作为工具可用性的硬门；
        # 它们仍来自持久化事实，因此优先于 Q 值探索排序。
        recommended = [
            *required_actions,
            *(action for action in recommended if action not in required_actions),
        ]
    db.commit()
    active_task_ids = [
        str(task_id)
        for task_id in db.scalars(
            select(Task.id)
            .where(
                Task.user_id == research_run.user_id,
                _task_scope_predicate(research_run),
                Task.status.in_(["queued", "running"]),
            )
            .order_by(Task.created_at.asc())
        ).all()
    ]
    return {
        "enabled": policy.enabled,
        "version": policy.version,
        "state": state,
        "state_key": state_key(state),
        "allowed_actions": allowed,
        "required_actions": required_actions,
        "recommended_actions": recommended,
        "q_values": {action: float(row.get(action, 0.0)) for action in allowed},
        "selection_mode": selection_mode,
        "stage_gate": {
            "status": stage_gate.get("status"),
            "ready": bool(stage_gate.get("ready")),
            "blockers": list(stage_gate.get("blockers") or []),
            "required_task_types": list(stage_gate.get("required_task_types") or []),
            "missing": list(stage_gate.get("missing") or []),
            "active": list(stage_gate.get("active") or []),
            "failed": list(stage_gate.get("failed") or []),
            "succeeded": list(stage_gate.get("succeeded") or []),
        },
        "active_task_ids": active_task_ids,
    }
