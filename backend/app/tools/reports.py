from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.artifacts.service import register_local_artifact, session_artifact_dir
from app.db.models import AgentMessage, Artifact, Task, User


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
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
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
                lines.append(f"  - error: {task.error_type or 'error'} · {task.error_message}")
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
