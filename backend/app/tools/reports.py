from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.artifacts.service import register_local_artifact, research_artifact_dir, session_artifact_dir
from app.api.harness import _owned_projection_context
from app.db.harness_models import ResearchSession, ScientificEvidence
from app.harness.projection_bridge import ProjectionBridgeError, resolve_projection_session
from app.db.models import (
    AgentMessage,
    Artifact,
    Candidate,
    CandidateTrack,
    ResearchRun,
    ResearchTaskLink,
    StrategyPolicy,
    StrategyTransition,
    Task,
    User,
    now_utc,
)


def compact(value: object, limit: int = 1200) -> str:
    text = "" if value is None else str(value)
    return text if len(text) <= limit else text[:limit] + "\n..."


def generate_session_report(
    db: Session,
    user: User,
    session_id: UUID,
    tool_call_id: str,
    title: str | None = None,
) -> dict:
    messages = db.scalars(
        select(AgentMessage)
        .where(AgentMessage.session_id == session_id, AgentMessage.user_id == user.id)
        .order_by(AgentMessage.created_at.asc())
    ).all()
    tasks = db.scalars(
        select(Task)
        .where(Task.session_id == session_id, Task.user_id == user.id)
        .order_by(Task.created_at.asc())
    ).all()
    artifacts = db.scalars(
        select(Artifact)
        .where(Artifact.session_id == session_id, Artifact.user_id == user.id)
        .order_by(Artifact.created_at.asc())
    ).all()

    report_title = title or "PSKit 会话分析报告"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        f"# {report_title}",
        "",
        f"- 生成时间：{now}",
        f"- 会话 ID：`{session_id}`",
        f"- 用户：`{user.username}`",
        "",
        "## 1. 对话摘要",
        "",
    ]

    if messages:
        for message in messages:
            role = "用户" if message.role == "user" else "智能体" if message.role == "assistant" else message.role
            lines.extend(
                [
                    f"### {role} · {message.created_at.isoformat()}",
                    "",
                    compact(message.content, 1600),
                    "",
                ]
            )
            sources = (message.metadata_json or {}).get("sources") if message.metadata_json else None
            if sources:
                lines.append("检索来源：")
                for source in sources[:5]:
                    lines.append(
                        f"- `{source.get('source')}` / {source.get('heading') or '-'} "
                        f"(score={float(source.get('score') or 0):.3f})"
                    )
                lines.append("")
    else:
        lines.extend(["暂无对话消息。", ""])

    lines.extend(["## 2. 任务记录", ""])
    if tasks:
        for task in tasks:
            lines.extend(
                [
                    f"- `{task.task_type}` · `{task.status}` · progress={task.progress:.2f}",
                    f"  - task_id: `{task.id}`",
                    f"  - created_at: {task.created_at.isoformat()}",
                ]
            )
            if task.error_message:
                lines.append(
                    f"  - error: {task.error_type or 'error'} · "
                    "详情请按任务编号查看受控服务器日志"
                )
    else:
        lines.append("暂无长任务。")
    lines.append("")

    lines.extend(["## 3. 结果文件", ""])
    if artifacts:
        for artifact in artifacts:
            lines.extend(
                [
                    f"- `{artifact.filename}`",
                    f"  - artifact_id: `{artifact.id}`",
                    f"  - kind: `{artifact.kind}`",
                    f"  - size: {artifact.size_bytes or 0} bytes",
                    f"  - download: `/api/files/{artifact.id}/download`",
                ]
            )
    else:
        lines.append("暂无结果文件。")
    lines.append("")

    lines.extend(
        [
            "## 4. 建议下一步",
            "",
            "- 如果已有预测结果，读取结果文件并总结高分残基或关键相互作用。",
            "- 如果任务仍在排队或运行，稍后刷新任务状态后继续分析。",
            "- 如果需要展示或简历材料，可以基于本报告补充截图、模型说明和技术栈描述。",
            "",
        ]
    )

    out_dir = session_artifact_dir(user.id, session_id, tool_call_id)
    path = out_dir / "pskit_session_report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    artifact = register_local_artifact(
        db,
        user,
        session_id=session_id,
        task_id=None,
        path=path,
        kind="report",
        mime_type="text/markdown",
    )
    return {
        "artifact_id": str(artifact.id),
        "filename": artifact.filename,
        "download_url": f"/api/files/{artifact.id}/download",
        "kind": artifact.kind,
        "message": "会话报告已生成。",
    }


def generate_research_report(
    db: Session,
    user: User,
    research_run: ResearchRun,
    operation_id: str,
    title: str | None = None,
) -> dict:
    final_report = research_run.current_stage == "report"
    report_status = "completed" if final_report else research_run.status
    report_stage_state = deepcopy(research_run.stage_state_json or {})
    report_artifact_token = "__PSKIT_REPORT_ARTIFACT_ID__"
    if final_report:
        report_stage_state["report"] = {
            "status": "succeeded",
            "artifact_id": report_artifact_token,
        }
    tracks = db.scalars(
        select(CandidateTrack)
        .where(CandidateTrack.research_run_id == research_run.id)
        .order_by(CandidateTrack.track.asc())
    ).all()
    candidates = db.scalars(
        select(Candidate)
        .where(
            Candidate.research_run_id == research_run.id,
            Candidate.user_id == user.id,
        )
        .order_by(Candidate.track.asc(), Candidate.rank.asc(), Candidate.created_at.asc())
    ).all()
    task_links = db.scalars(
        select(ResearchTaskLink)
        .where(
            ResearchTaskLink.research_run_id == research_run.id,
            ResearchTaskLink.user_id == user.id,
        )
        .order_by(ResearchTaskLink.created_at.asc())
    ).all()
    policy = db.scalar(
        select(StrategyPolicy).where(StrategyPolicy.research_run_id == research_run.id)
    )
    transitions = []
    if policy:
        transitions = db.scalars(
            select(StrategyTransition)
            .where(StrategyTransition.policy_id == policy.id)
            .order_by(StrategyTransition.created_at.asc())
        ).all()

    report_title = title or f"{research_run.title} · 科研运行报告"
    lines = [
        f"# {report_title}",
        "",
        "> wzf：本报告由当前科研运行的持久化事实生成；未在真实服务器完成的科学工具不得视为已验证结论。",
        "",
        "## 1. 科研问题与靶标",
        "",
        f"- ResearchRun ID：`{research_run.id}`",
        f"- 运行状态：`{report_status}`",
        f"- 当前阶段：`{research_run.current_stage}`",
        f"- 生成时间：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"- 靶标事实：`{compact(research_run.target_json, 4000)}`",
        f"- 靶标摘要哈希：`{(research_run.metadata_json or {}).get('target_hash') or '未记录'}`",
        "",
        "## 2. 阶段状态与证据",
        "",
        f"- 阶段状态：`{compact(report_stage_state, 5000)}`",
        f"- 证据：`{compact(research_run.evidence_json, 5000)}`",
        "",
        "## 3. 候选生成、评分与筛选",
        "",
    ]
    for track_name, label in (("rna", "RNA 候选轨道"), ("peptide", "多肽候选轨道")):
        track = next((item for item in tracks if item.track == track_name), None)
        rows = [item for item in candidates if item.track == track_name]
        lines.extend(
            [
                f"### {label}",
                "",
                f"- 轨道状态：`{track.status if track else 'missing'}`",
                f"- 当前迭代：{track.current_iteration if track else 0}",
                f"- 评分配置：`{compact(track.score_config_json if track else {}, 4000)}`",
                "",
                "| 排名 | 候选 ID | 序列 | 生成器 | 总分 | 筛选状态 | AF3 状态 |",
                "| --- | --- | --- | --- | ---: | --- | --- |",
            ]
        )
        if not rows:
            lines.append("| — | — | 暂无候选 | — | — | — | — |")
        for candidate in rows:
            total_score = "—" if candidate.total_score is None else f"{candidate.total_score:.4f}"
            lines.append(
                "| "
                f"{candidate.rank or '—'} | `{candidate.id}` | `{candidate.sequence}` | "
                f"{candidate.generator_name} | {total_score} | {candidate.selection_status} | "
                f"{candidate.af3_status or '未提交'} |"
            )
        lines.append("")

    lines.extend(["## 4. 任务、产物与 AlphaFold3", ""])
    if not task_links:
        lines.append("暂无关联任务。")
    for link in task_links:
        task = db.get(Task, link.task_id)
        if not task:
            lines.append(f"- 缺失任务：`{link.task_id}`")
            continue
        lines.append(
            f"- `{link.role}` · `{task.task_type}` · `{task.status}` · task_id=`{task.id}`"
        )
        if task.error_message:
            lines.append(
                f"  - 失败：{task.error_type or 'error'} · "
                "详情请按任务编号查看受控服务器日志"
            )
        artifacts = db.scalars(
            select(Artifact).where(Artifact.task_id == task.id, Artifact.user_id == user.id)
        ).all()
        for artifact in artifacts:
            lines.append(
                f"  - 产物：`{artifact.filename}` · `/api/files/{artifact.id}/download`"
            )
    lines.append("")

    lines.extend(["## 5. Agent 策略反馈", ""])
    if not policy:
        lines.append("尚未初始化策略反馈。")
    else:
        lines.extend(
            [
                f"- 策略版本：`{policy.version}`",
                f"- 是否启用：`{policy.enabled}`",
                f"- α / γ / ε：`{policy.alpha}` / `{policy.gamma}` / `{policy.epsilon}`",
                f"- 反馈条数：{len(transitions)}",
                f"- Q 表：`{compact(policy.q_table_json, 6000)}`",
            ]
        )
    lines.extend(
        [
            "",
            "## 6. 可复现性、失败与局限",
            "",
            "- 候选评分配置按轨道保存；临时配置不得表述为导师已确认的科学标准。",
            "- 本地 mock 或契约测试只验证编排胶水，不证明 CORAL、PepCCD MCP、Foldseek、序列同源或 AF3 的真实科研能力。",
            "- 真实科学工具必须由团队在可访问 CORAL/PepCCD MCP 和 A6000 资源的隔离环境中执行，并回填版本、输入摘要、任务状态、产物和错误日志。",
            "- RNA 与多肽候选只在各自轨道内排名，不产生跨类别总榜。",
            "- 每个候选对应独立 AF3 任务；失败任务应单独复核或重试。",
            "",
        ]
    )

    out_dir = research_artifact_dir(user.id, research_run.id, operation_id)
    path = out_dir / "pskit_research_report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    artifact = register_local_artifact(
        db,
        user,
        session_id=research_run.session_id,
        task_id=None,
        path=path,
        kind="research_report",
        mime_type="text/markdown",
        commit=False,
    )
    if final_report:
        # wzf：产物 UUID 只有 flush 后才确定；在同一事务提交前回写正文，
        # 使最终报告与数据库 completed/succeeded 终态及自身产物 ID 一致。
        content = path.read_text(encoding="utf-8").replace(
            report_artifact_token,
            str(artifact.id),
        )
        path.write_text(content, encoding="utf-8")
        artifact.size_bytes = path.stat().st_size
    artifact.metadata_json = {"research_run_id": str(research_run.id)}
    metadata = dict(research_run.metadata_json or {})
    metadata["last_report_artifact_id"] = str(artifact.id)
    research_run.metadata_json = metadata
    if final_report:
        stage_state = dict(research_run.stage_state_json or {})
        stage_state["report"] = {
            "status": "succeeded",
            "artifact_id": str(artifact.id),
        }
        research_run.stage_state_json = stage_state
        research_run.status = "completed"
    research_run.updated_at = now_utc()
    db.commit()
    return {
        "artifact_id": str(artifact.id),
        "filename": artifact.filename,
        "download_url": f"/api/files/{artifact.id}/download",
        "kind": artifact.kind,
        "research_run_id": str(research_run.id),
        "message": "科研运行报告已生成。",
    }


# wzf：Harness 报告只读取新会话投影和持久化证据；它不创建 Artifact、不提交事务，
# 也不把未验证或失败的状态改写成科学结论。
_HARNESS_FAILED_ATTEMPT_STATES = frozenset({"failed", "timed_out", "interrupted", "cancelled"})
_HARNESS_FAILED_EVIDENCE_STATES = frozenset({"failed", "invalid", "rejected"})


def _harness_safe_line(value: object, limit: int = 800) -> str:
    return compact(value, limit).replace("\r", " ").replace("\n", " ").strip()


def _harness_safe_error(value: object) -> str | None:
    if value is None:
        return None
    text = _harness_safe_line(value, 1000)
    if not text:
        return None
    if "/" in text or "\\" in text:
        return "execution failed; inspect controlled diagnostics"
    return text


def _harness_evidence_artifact_ids(item: ScientificEvidence) -> tuple[str, ...]:
    values = [str(value) for value in (item.artifact_ids or []) if value is not None]
    if item.artifact_id is not None and str(item.artifact_id) not in values:
        values.append(str(item.artifact_id))
    return tuple(values)


def _harness_evidence_state(item: ScientificEvidence) -> str:
    raw_status = str(item.status or "").strip().lower()
    if raw_status in _HARNESS_FAILED_EVIDENCE_STATES:
        return "failed"
    if raw_status == "verified" and bool(item.sufficient) and bool(item.content_hash):
        return "verified"
    return "unverified"


def _resolve_harness_report_session(
    db: Session,
    user: User,
    requested_session_id: UUID | None,
) -> tuple[object | None, ResearchSession | None]:
    if requested_session_id is None:
        return None, None
    try:
        reference = resolve_projection_session(db, user, requested_session_id)
    except (ProjectionBridgeError, TypeError, ValueError):
        return None, None
    if reference is None or not reference.available or reference.projection_session_id is None:
        return reference, None
    session = db.get(ResearchSession, reference.projection_session_id)
    if session is None or session.user_id != user.id or session.archived_at is not None:
        return reference, None
    return reference, session


def generate_harness_report(
    db: Session,
    user: User,
    session_id: UUID | None,
    title: str | None = None,
) -> dict:
    """生成新 Harness 的只读证据报告，不需要 ``research_run_id``。

    报告内容来自当前用户可见的持久化 ``ScientificEvidence`` 及其任务归属。
    ``verified`` 只有在数据库同时记录 ``status=verified``、``sufficient`` 和
    ``content_hash`` 时才成立；其他行保留为未验证或失败，不补写任何科学结论。
    """

    _reference, session = _resolve_harness_report_session(db, user, session_id)
    requested_text = str(session_id) if session_id is not None else None
    if session is None:
        report_status = "unmapped"
        mapping_status = "unmapped"
        goals: tuple[object, ...] = ()
        skills: tuple[object, ...] = ()
        graphs: tuple[object, ...] = ()
        tasks: tuple[object, ...] = ()
        attempts: tuple[object, ...] = ()
        evidence: tuple[ScientificEvidence, ...] = ()
        resolved_text = requested_text
        session_title = None
    else:
        goals, skills, graphs, tasks, attempts, _dependencies, evidence = _owned_projection_context(
            db, user, session
        )
        evidence_states = [_harness_evidence_state(item) for item in evidence]
        failed_attempts = [
            item for item in attempts
            if str(item.status or "").strip().lower() in _HARNESS_FAILED_ATTEMPT_STATES
        ]
        if failed_attempts or "failed" in evidence_states:
            report_status = "failed"
        elif not evidence or "unverified" in evidence_states:
            report_status = "unverified"
        else:
            report_status = "evidence_backed"
        mapping_status = "mapped"
        resolved_text = str(session.id)
        session_title = session.title

    evidence_states = [_harness_evidence_state(item) for item in evidence]
    verified_count = evidence_states.count("verified")
    unverified_count = evidence_states.count("unverified")
    failed_count = evidence_states.count("failed")
    failed_attempt_count = sum(
        1 for item in attempts
        if str(item.status or "").strip().lower() in _HARNESS_FAILED_ATTEMPT_STATES
    )
    report_title = title or f"Harness 证据报告 · {session_title or '未命名会话'}"
    generated_at = datetime.now(timezone.utc).isoformat()
    lines = [
        f"# {_harness_safe_line(report_title, 240)}",
        "",
        "> wzf：本报告只汇总当前用户可见的 Harness 持久化事实和离散状态；未验证、拒绝或失败的记录不构成科学结论。",
        "",
        f"- 报告状态：`{report_status}`",
        f"- 映射状态：`{mapping_status}`",
        f"- 请求会话 ID：`{requested_text or '未提供'}`",
        f"- Harness 会话 ID：`{resolved_text or '未映射'}`",
        f"- 生成时间：`{generated_at}`",
        "",
        "## 1. 持久化范围",
        "",
    ]
    if session is None:
        lines.extend([
            "当前请求没有可用的、属于当前用户的 Harness ResearchSession 映射；本报告未查询其他会话的证据，也未据此推断失败或科学结论。",
            "",
        ])
    else:
        lines.extend([
            f"- 会话标题：{_harness_safe_line(session.title or '未命名会话', 400)}",
            f"- 会话原始状态：`{_harness_safe_line(session.status, 80)}`",
            f"- 目标记录：{len(goals)}；Skill 执行：{len(skills)}；任务图：{len(graphs)}；任务：{len(tasks)}；执行尝试：{len(attempts)}",
            f"- ScientificEvidence：{len(evidence)}（verified={verified_count}，unverified={unverified_count}，failed={failed_count}）",
            "",
        ])

    lines.extend(["## 2. 目标与任务状态", ""])
    if goals:
        for goal in goals:
            lines.extend([
                f"- 目标 `{goal.id}`：{_harness_safe_line(goal.title, 300)}；状态=`{_harness_safe_line(goal.status, 80)}`",
                f"  - objective（持久化原文，未作推断）：{_harness_safe_line(goal.objective, 800)}",
            ])
    else:
        lines.append("- 未发现与当前 Harness 会话关联的目标记录。")
    if tasks:
        lines.append("")
        for task in tasks:
            lines.append(
                f"- 任务 `{task.id}`：{_harness_safe_line(task.name, 300)}；类型=`{_harness_safe_line(task.task_type, 100)}`；状态=`{_harness_safe_line(task.status, 80)}`"
            )
    if attempts:
        lines.append("")
        for attempt in attempts:
            line = f"- 执行尝试 `{attempt.id}`（task=`{attempt.scientific_task_id}`）：状态=`{_harness_safe_line(attempt.status, 80)}`"
            if attempt.error_type:
                line += f"；error_type=`{_harness_safe_line(attempt.error_type, 160)}`"
            safe_error = _harness_safe_error(attempt.error_message)
            if safe_error:
                line += f"；error=`{safe_error}`"
            lines.append(line)
    lines.append("")

    lines.extend(["## 3. ScientificEvidence 记录", ""])
    if not evidence:
        lines.extend([
            "- 当前映射范围没有 ScientificEvidence 持久化记录；这表示证据缺失或尚未映射，不表示科学任务已失败。",
            "",
        ])
    else:
        for item, state in zip(evidence, evidence_states, strict=True):
            raw_status = _harness_safe_line(item.status or "unknown", 80)
            item_title = _harness_safe_line(item.title or item.evidence_type or "未命名证据", 300)
            lines.extend([
                f"### {item_title} · `{item.id}`",
                "",
                f"- evidence_type=`{_harness_safe_line(item.evidence_type, 120)}`；原始 status=`{raw_status}`；报告状态=`{state}`",
                f"- sufficient=`{bool(item.sufficient)}`；content_hash=`{_harness_safe_line(item.content_hash or '缺失', 160)}`",
            ])
            artifact_ids = _harness_evidence_artifact_ids(item)
            if artifact_ids:
                lines.append(f"- artifact_ids=`{', '.join(artifact_ids)}`")
            if item.source_uri:
                lines.append(f"- source_uri=`{_harness_safe_line(item.source_uri, 500)}`")
            if item.statement:
                lines.extend([
                    "- statement（持久化原文，未作推断）：",
                    "",
                    _harness_safe_line(item.statement, 1000),
                ])
            lines.append("")

    lines.extend([
        "## 4. 解释边界",
        "",
        "- `verified` 仅表示该行同时具有显式 verified 状态、充分性标记和内容哈希；它不等同于实测亲和力、特异性、广谱性能或实验复现。",
        "- `unverified`、`rejected`、执行失败和缺失映射均原样保留；本报告不会把它们补成成功、排名或因果结论。",
        "- 本函数只读数据库，不写 Artifact、文件、会话状态或事务。",
        "",
    ])
    return {
        "report_type": "harness_evidence",
        "status": report_status,
        "mapping_status": mapping_status,
        "requested_session_id": requested_text,
        "session_id": resolved_text,
        "title": report_title,
        "generated_at": generated_at,
        "evidence_count": len(evidence),
        "verified_evidence_count": verified_count,
        "unverified_evidence_count": unverified_count,
        "failed_evidence_count": failed_count,
        "failed_attempt_count": failed_attempt_count,
        "report_markdown": "\n".join(lines),
        "message": {
            "unmapped": "Harness 会话尚未映射，未生成科学结论。",
            "unverified": "报告包含未验证或缺失的持久化证据，未生成科学结论。",
            "failed": "报告检测到失败或拒绝状态，未生成科学结论。",
            "evidence_backed": "报告仅汇总显式验证的持久化证据，仍不等同于实验结论。",
        }[report_status],
    }
