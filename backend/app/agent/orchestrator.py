from __future__ import annotations

import json
import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, StateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.llm import LlmUnavailable, OpenAICompatibleClient, default_system_prompt
from app.agent.leases import AgentLeaseRevoked
from app.agent.policy import SCIENTIFIC_ACTIONS, available_actions, build_policy_decision
from app.agent.state import AgentState
from app.config import get_settings
from app.db.models import AgentSession, Artifact, ResearchRun, Task, User, now_utc
from app.harness.capabilities import get_capability_manifest
from app.harness.runtime_wiring import resolve_runtime_wiring
from app.rag.retriever import retrieve
from app.research.context import resolve_session_research_context
from app.research.service import canonical_target_hash
from app.tasks.service import LONG_RUNNING_TOOL_NAMES
from app.tools.runner import ToolContext, execute_tool
from app.tools.catalog import openai_tool_schemas


logger = logging.getLogger(__name__)


class AgentPlanningContractError(RuntimeError):
    def __init__(self, required_tool: str, actual_tools: list[str]) -> None:
        self.required_tool = required_tool
        self.actual_tools = actual_tools
        super().__init__(
            f"Planner 未调用要求工具 {required_tool}；实际工具："
            f"{', '.join(actual_tools) or '无'}"
        )


@dataclass
class AgentRunRecord:
    events: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    execution_facts: dict[str, Any] = field(default_factory=dict)
    continuation: dict[str, Any] | None = None
    pending_approval: dict[str, Any] | None = None
    rag_backend: str = "none"
    final_answer: str = ""
    suggestions: list[str] = field(default_factory=list)
    failed: bool = False
    error_code: str | None = None


@dataclass
class AgentRuntime:
    db: Session
    user: User
    session: AgentSession
    research_run: ResearchRun | None = None
    turn_id: UUID | None = None
    llm: OpenAICompatibleClient = field(default_factory=OpenAICompatibleClient)
    lease_guard: Any | None = None
    # 服务端生成/持久化的跨 Agent、API、Worker 关联标识；普通对话不依赖
    # research_run_id。默认不提供 Harness 执行器，因而安全回落旧路径。
    correlation_id: str | None = None
    harness_executor: Any | None = None
    harness_wiring_context: Any | None = None
    # 由服务端桥接层生成；仅用于跨 Agent/API/Worker 传递摘要。
    dispatch_summary: dict[str, Any] = field(default_factory=dict)
    harness_dispatch_factory: Any | None = None
    # 仅由服务端从上一条 assistant 元数据解析；模型和客户端不能设置。
    approved_tool_call: dict[str, Any] | None = None
    approved_tool_fingerprint: str | None = None


def chunk_text(text: str, size: int = 90):
    for index in range(0, len(text), size):
        yield text[index : index + size]


def normalize_chat_history(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "")
        content = str(message.get("content") or "")
        if role == "user":
            normalized.append({"role": "user", "content": HumanMessage(content=content).content})
        elif role == "assistant":
            normalized.append({"role": "assistant", "content": content})
        elif role == "system":
            normalized.append({"role": "system", "content": content})
    return normalized


def normalize_assistant_message(message: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {
        "role": "assistant",
        "content": message.get("content") or "",
    }
    if message.get("tool_calls"):
        normalized["tool_calls"] = message["tool_calls"]
    return normalized


def tool_call_name_and_args(tool_call: dict[str, Any]) -> tuple[str, str]:
    function = tool_call.get("function") or {}
    return function.get("name") or "", function.get("arguments") or "{}"


def make_event(event_type: str, **payload: Any) -> dict[str, Any]:
    return {
        "type": event_type,
        "generated_at": now_utc().isoformat(),
        **payload,
    }


def explicit_required_tool(
    latest_user_message: str,
    allowed_actions: list[str],
) -> str | None:
    """只把用户对唯一已授权工具的明确命令升级为执行契约。"""

    matched: list[str] = []
    directive_prefix = r"(?:必须|务必|(?:只|仅)(?:需|要)?)\s*(?:实际\s*)?(?:调用|执行)"
    for action in allowed_actions:
        pattern = (
            rf"{directive_prefix}\s*[`'\"“”]?"
            rf"{re.escape(action)}(?![A-Za-z0-9_])"
        )
        if re.search(pattern, latest_user_message, flags=re.IGNORECASE):
            matched.append(action)
    return matched[0] if len(matched) == 1 else None


def tool_call_fingerprint(name: str, raw_args: str) -> str:
    try:
        normalized_args = json.dumps(
            json.loads(raw_args or "{}"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except json.JSONDecodeError:
        normalized_args = raw_args.strip()
    return hashlib.sha256(f"{name}\0{normalized_args}".encode("utf-8")).hexdigest()


def pending_approval_for_call(
    runtime: AgentRuntime,
    *,
    tool_name: str,
    raw_arguments: str,
    fingerprint: str,
) -> dict[str, Any]:
    approval_id = hashlib.sha256(
        f"{runtime.session.id}:{runtime.turn_id}:{fingerprint}".encode("utf-8")
    ).hexdigest()
    return {
        "approval_id": approval_id,
        "tool_name": tool_name,
        "raw_arguments": raw_arguments,
        "arguments_hash": fingerprint,
        "argument_keys": sorted(parse_tool_argument_keys(raw_arguments)),
        "created_at": now_utc().isoformat(),
        "consumed_by_turn_id": None,
    }


def public_approval_payload(approval: dict[str, Any]) -> dict[str, Any]:
    return {
        "approval_id": approval.get("approval_id"),
        "tool_name": approval.get("tool_name"),
        "argument_keys": list(approval.get("argument_keys") or []),
        "arguments_hash": approval.get("arguments_hash"),
        "message": "该工具可能消耗较多计算资源，需要你确认后才会执行。",
    }


def parse_tool_argument_keys(raw_args: str) -> set[str]:
    try:
        value = json.loads(raw_args or "{}")
    except json.JSONDecodeError:
        return set()
    if not isinstance(value, dict):
        return set()
    return {str(key) for key in value}


def model_visible_tool_result(value: Any) -> Any:
    """Remove internal research-runtime and policy fields before LLM/UI exposure."""

    hidden_keys = {
        "research_run_id",
        "current_stage",
        "stage",
        "stage_state",
        "stage_state_json",
        "stage_gate",
        "policy_state",
        "q_values",
        "state_key",
    }
    if isinstance(value, dict):
        return {
            key: model_visible_tool_result(item)
            for key, item in value.items()
            if key not in hidden_keys
        }
    if isinstance(value, list):
        return [model_visible_tool_result(item) for item in value]
    return value


def compact_tool_result(result: dict, max_chars: int) -> str:
    visible_result = model_visible_tool_result(result)
    assert isinstance(visible_result, dict)
    raw = json.dumps(visible_result, ensure_ascii=False, default=str)
    if len(raw) <= max_chars:
        return raw
    summary_keys = (
        "task_id",
        "task_type",
        "status",
        "artifact_id",
        "filename",
        "download_url",
        "kind",
        "message",
        "error",
        "hit_count",
        "candidate_count",
        "track",
        "submitted_count",
        "reused_count",
    )
    summary = {
        key: visible_result[key]
        for key in summary_keys
        if key in visible_result
    }
    if visible_result.get("tasks"):
        summary["tasks"] = task_summaries_from_result(visible_result)
    return json.dumps(
        {
            "truncated": True,
            "original_character_count": len(raw),
            "summary": summary,
            "instruction": "结果已截断；需要细节时读取对应任务/结果文件，或使用更窄的查询参数。",
        },
        ensure_ascii=False,
        default=str,
    )


def safe_tool_event_result(result: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = (
        "task_id",
        "task_type",
        "status",
        "progress",
        "track",
        "hit_count",
        "candidate_count",
        "submitted_count",
        "reused_count",
        "artifact_id",
        "filename",
        "kind",
    )
    return {
        key: result[key]
        for key in allowed_keys
        if key in result
    }


def public_orchestration_snapshot(policy_decision: dict[str, Any]) -> dict[str, Any]:
    """Expose only execution-relevant facts, never internal stage/Q-table state."""

    state = dict(policy_decision.get("state") or {})
    return {
        "target_available": bool(state.get("target_available")),
        "candidate_tracks": {
            "rna": {
                "status": state.get("rna_track_status"),
                "candidate_count": int(state.get("rna_candidate_count") or 0),
            },
            "peptide": {
                "status": state.get("peptide_track_status"),
                "candidate_count": int(state.get("peptide_candidate_count") or 0),
            },
        },
        "tasks": {
            "queued": int(state.get("queued_task_count") or 0),
            "running": int(state.get("running_task_count") or 0),
            "failed": int(state.get("failed_task_count") or 0),
        },
    }


def public_policy_event_payload(policy_decision: dict[str, Any]) -> dict[str, Any]:
    return {
        "execution_snapshot": public_orchestration_snapshot(policy_decision),
    }


def public_agent_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Project an internal event onto the client-safe event stream."""

    # 路由选择、相关 ID 与服务端接线理由只用于内部审计；前端没有该契约，
    # 历史 API 和实时 SSE 都必须在同一投影边界抑制它。
    if event.get("type") == "harness_route":
        return None
    if event.get("type") != "strategy_policy":
        return dict(event)
    snapshot = event.get("execution_snapshot")
    if not isinstance(snapshot, dict):
        snapshot = public_orchestration_snapshot(event)
    return {
        key: value
        for key, value in {
            "type": "strategy_policy",
            "generated_at": event.get("generated_at"),
            "turn_id": event.get("turn_id"),
            "execution_snapshot": snapshot,
        }.items()
        if value is not None
    }


def execution_facts_from_state(state: AgentState) -> dict[str, Any]:
    """Build a bounded, server-derived fact envelope for UI and later turns."""

    tool_facts: list[dict[str, Any]] = []
    task_facts: list[dict[str, Any]] = []
    seen_task_ids: set[str] = set()
    for item in state.get("tool_results", []) or []:
        if not isinstance(item, dict):
            continue
        result = item.get("result")
        if isinstance(result, dict) and result.get("truncated"):
            result = result.get("summary")
        summary = safe_tool_event_result(result) if isinstance(result, dict) else {}
        tool_facts.append({"name": str(item.get("tool") or ""), "result": summary})
        if isinstance(result, dict):
            for task in task_summaries_from_result(result):
                task_id = str(task["task_id"])
                if task_id not in seen_task_ids:
                    task_facts.append(task)
                    seen_task_ids.add(task_id)
    for task_id_value in state.get("active_task_ids", []) or []:
        task_id = str(task_id_value)
        if task_id not in seen_task_ids:
            task_facts.append({"task_id": task_id})
            seen_task_ids.add(task_id)
    artifacts = [
        {
            key: artifact.get(key)
            for key in ("artifact_id", "filename", "download_url", "kind")
            if artifact.get(key) is not None
        }
        for artifact in state.get("artifacts", []) or []
        if isinstance(artifact, dict)
    ]
    errors = [
        {
            key: diagnostic.get(key)
            for key in ("error_type", "tool", "detail_code", "message")
            if diagnostic.get(key) is not None
        }
        for diagnostic in state.get("diagnostics", []) or []
        if isinstance(diagnostic, dict)
    ]
    return {
        "tools": tool_facts,
        "tasks": task_facts,
        "artifacts": artifacts,
        "errors": errors,
        "waiting_for_tasks": bool(state.get("waiting_for_tasks")),
    }


def continuation_from_state(state: AgentState) -> dict[str, Any] | None:
    if not state.get("waiting_for_tasks"):
        return None
    task_ids = list(dict.fromkeys(str(item) for item in state.get("active_task_ids", []) or []))
    if not task_ids:
        return None
    return {
        "state": "background_task_running",
        "task_ids": task_ids,
        "resume_action": "review_completed_tasks",
        "updated_at": now_utc().isoformat(),
    }


def deterministic_waiting_answer(state: AgentState) -> str:
    task_count = len(set(str(item) for item in state.get("active_task_ids", []) or []))
    noun = f"{task_count} 个" if task_count else "相关"
    return (
        f"服务端已记录并开始处理 {noun}后台任务。本轮已安全暂停，"
        "不会把尚未完成的任务当作结果。任务结束后，页面会自动读取已完成任务的结果并继续当前对话；"
        "如果你正在输入，页面会保留草稿并显示手动继续按钮。"
    )


def runtime_research_context(runtime: AgentRuntime, *, create: bool = False):
    """按会话解析研究上下文并缓存到 runtime（ADR 0012）。

    普通对话在没有科研调用之前不会建立上下文，因此 ``create`` 默认为 False。
    """

    if runtime.research_run is not None:
        return runtime.research_run
    if runtime.session is None:
        return None
    resolved = resolve_session_research_context(
        runtime.db,
        runtime.user,
        runtime.session.id,
        create=create,
    )
    if resolved is not None:
        runtime.research_run = resolved
    return resolved


def active_session_task_ids(runtime: AgentRuntime) -> list[str]:
    """Return real queued/running tasks without creating an internal context."""

    if runtime.session is None:
        return []
    return [
        str(task_id)
        for task_id in runtime.db.scalars(
            select(Task.id)
            .where(
                Task.user_id == runtime.user.id,
                Task.session_id == runtime.session.id,
                Task.status.in_(["queued", "running"]),
            )
            .order_by(Task.created_at.asc())
        ).all()
    ]


def completed_session_task_artifact_facts(
    runtime: AgentRuntime,
    *,
    task_limit: int = 6,
    artifact_limit: int = 40,
) -> list[dict[str, Any]]:
    """Return bounded, owner-checked artifact facts for recent successful tasks.

    These records let the planner distinguish task IDs from artifact IDs after a
    long-running turn resumes. Only database-registered metadata is exposed; no
    filesystem path or storage key crosses the model boundary.
    """

    if runtime.session is None:
        return []
    tasks = runtime.db.scalars(
        select(Task)
        .where(
            Task.user_id == runtime.user.id,
            Task.session_id == runtime.session.id,
            Task.status == "succeeded",
        )
        .order_by(Task.updated_at.desc())
        .limit(task_limit)
    ).all()
    if not tasks:
        return []
    task_ids = [task.id for task in tasks]
    artifacts = runtime.db.scalars(
        select(Artifact)
        .where(
            Artifact.user_id == runtime.user.id,
            Artifact.task_id.in_(task_ids),
        )
        .order_by(Artifact.created_at.desc())
        .limit(artifact_limit)
    ).all()
    artifacts_by_task: dict[UUID, list[Artifact]] = {}
    for artifact in artifacts:
        if artifact.task_id is None:
            continue
        artifacts_by_task.setdefault(artifact.task_id, []).append(artifact)

    facts: list[dict[str, Any]] = []
    for task in tasks:
        task_artifacts = artifacts_by_task.get(task.id, [])
        if not task_artifacts:
            continue
        facts.append(
            {
                "task_id": str(task.id),
                "task_type": task.task_type,
                "status": task.status,
                "artifacts": [
                    {
                        "artifact_id": str(artifact.id),
                        "filename": artifact.filename,
                        "kind": artifact.kind,
                        "size_bytes": artifact.size_bytes,
                    }
                    for artifact in sorted(
                        task_artifacts,
                        key=lambda item: (item.filename, str(item.id)),
                    )
                ],
            }
        )
    return facts


def record_research_tool_evidence(
    runtime: AgentRuntime,
    *,
    tool_name: str,
    tool_call_id: str,
    arguments_hash: str,
    result: dict[str, Any],
) -> None:
    """只保存服务端实际执行事实，不把模型提供的自由文本当作科学证据。"""

    if tool_name == "run_alphafold3" or tool_name not in SCIENTIFIC_ACTIONS:
        # direct run_alphafold3 是 session-native 高成本任务，不代表候选、阶段或
        # target provenance。候选级 AF3 由 submit_research_top10_af3 单独绑定。
        # 控制类与报告类工具的执行同样不构成科学证据。
        return
    if runtime.turn_id is None:
        return
    if runtime_research_context(runtime) is None:
        return
    assert runtime.research_run is not None
    current_stage = runtime.research_run.current_stage
    target_hash = canonical_target_hash(runtime.research_run.target_json or {})
    evidence_id = hashlib.sha256(
        (
            f"{runtime.research_run.id}:{current_stage}:{target_hash}:"
            f"{runtime.turn_id}:{tool_call_id}:{arguments_hash}"
        ).encode("utf-8")
    ).hexdigest()
    evidence = list(runtime.research_run.evidence_json or [])
    if any(
        isinstance(item, dict) and item.get("evidence_id") == evidence_id
        for item in evidence
    ):
        return
    evidence.append(
        {
            "evidence_id": evidence_id,
            "kind": "agent_tool_execution",
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "arguments_hash": arguments_hash,
            "research_run_id": str(runtime.research_run.id),
            "stage_at_execution": current_stage,
            "target_hash": target_hash,
            "result_summary": safe_tool_event_result(result),
            "provenance": {
                "kind": "agent_tool",
                "agent_turn_id": str(runtime.turn_id),
                "created_at": now_utc().isoformat(),
            },
        }
    )
    runtime.research_run.evidence_json = evidence
    runtime.research_run.updated_at = now_utc()


def task_summaries_from_result(result: dict) -> list[dict]:
    summaries: list[dict] = []
    if result.get("task_id"):
        summaries.append(
            {
                "task_id": str(result["task_id"]),
                "task_type": result.get("task_type"),
                "status": result.get("status"),
            }
        )
    for item in result.get("tasks") or []:
        if not isinstance(item, dict):
            continue
        task = item.get("task") if isinstance(item.get("task"), dict) else item
        task_id = task.get("id") or task.get("task_id")
        if task_id:
            summaries.append(
                {
                    "task_id": str(task_id),
                    "task_type": task.get("task_type"),
                    "status": task.get("status"),
                }
            )
    return summaries


def build_suggestions(events: list[dict[str, Any]], artifacts: list[dict[str, Any]]) -> list[str]:
    if any(event.get("type") == "approval_required" for event in events):
        return ["确认并执行高成本工具", "调整参数后重新请求"]
    if any(event.get("type") == "agent_waiting" for event in events):
        return ["刷新任务状态", "任务完成后继续当前对话", "查看任务错误与结果文件"]
    if any(event.get("type") == "task_created" for event in events):
        return ["刷新任务状态", "任务完成后读取结果文件", "生成本次分析报告"]
    if artifacts:
        return ["读取最新结果文件", "总结关键残基和相互作用", "生成本次分析报告"]
    if any(event.get("type") == "error" for event in events):
        return ["检查运行环境", "换一个 PDB 示例重试", "生成故障排查报告"]
    return ["搜索一个合适的蛋白-RNA复合物", "下载 7U5E 并预测 RNA 结合位点", "生成本次分析报告"]


def retrieve_knowledge_node(state: AgentState) -> AgentState:
    query = state.get("latest_user_message", "")
    backend, chunks = retrieve(query, top_k=5)
    retrieved_knowledge = [
        {
            "source": chunk.source,
            "heading": chunk.heading,
            "score": chunk.score,
            "content": chunk.content,
        }
        for chunk in chunks
    ]
    source_payload = [
        {"source": item["source"], "heading": item["heading"], "score": item["score"]}
        for item in retrieved_knowledge
    ]
    system_message = SystemMessage(content=default_system_prompt(retrieved_knowledge))
    return {
        **state,
        "rag_backend": backend,
        "retrieved_knowledge": retrieved_knowledge,
        "messages": [{"role": "system", "content": system_message.content}, *state.get("messages", [])],
        "pending_events": [
            make_event("agent_step", label="检索 PSKit 项目知识库", status="completed"),
            make_event("knowledge_sources", backend=backend, sources=source_payload),
        ],
    }


def plan_next_action_node(runtime: AgentRuntime):
    def node(state: AgentState) -> AgentState:
        step_count = int(state.get("step_count") or 0) + 1
        planning_messages = list(state.get("messages", []))
        policy_decision = None
        tool_schemas = None
        required_tool = None
        events = []
        # ADR 0012：工具目录不再按阶段裁剪；任何会话都可调用全部已授权工具。
        catalog_actions = available_actions()
        research_context = runtime_research_context(runtime)
        if research_context is not None:
            policy_decision = build_policy_decision(
                runtime.db,
                runtime.user,
                research_context,
            )
            snapshot = public_orchestration_snapshot(policy_decision)
            guidance = {
                "role": "system",
                "content": (
                    "本会话已积累服务端验证的执行事实，可作为下一步依据。"
                    f"执行摘要：{json.dumps(snapshot, ensure_ascii=False)}。"
                    "工具清单顺序已由服务端依据这些事实确定。"
                    "这些事实不能绕过工具参数校验、权限检查或依赖检查。"
                    "任务归属和内部持久化由服务端处理，不得要求用户提供内部运行标识。"
                ),
            }
            if planning_messages and planning_messages[0].get("role") == "system":
                planning_messages = [planning_messages[0], guidance, *planning_messages[1:]]
            else:
                planning_messages = [guidance, *planning_messages]
            tool_schemas = openai_tool_schemas(
                set(catalog_actions),
                preferred_order=policy_decision["recommended_actions"],
            )
        else:
            tool_schemas = openai_tool_schemas(set(catalog_actions))
        completed_artifacts = completed_session_task_artifact_facts(runtime)
        if completed_artifacts:
            artifact_guidance = {
                "role": "system",
                "content": (
                    "以下是当前会话最近成功后台任务的服务端验证产物："
                    f"{json.dumps(completed_artifacts, ensure_ascii=False, separators=(',', ':'))}。"
                    "读取任务结果时，必须把相关 artifacts[].artifact_id 原样传给 "
                    "read_result_file 的 artifact_id。task_id 不是 artifact_id；不得把 task_id、"
                    "filename、download_url 或猜测的主机路径当作 artifact_id/file_path。"
                    "若相关产物已在清单中，直接读取它，不要先生成报告来发现产物。"
                    "用户需要候选内容时优先读取 candidates.json；只有明确要求原始响应时才读取 *_raw.json。"
                ),
            }
            if planning_messages and planning_messages[0].get("role") == "system":
                planning_messages = [
                    planning_messages[0],
                    artifact_guidance,
                    *planning_messages[1:],
                ]
            else:
                planning_messages = [artifact_guidance, *planning_messages]
        active_task_ids = list(
            dict.fromkeys(
                [
                    *(
                        policy_decision["active_task_ids"]
                        if policy_decision is not None
                        else []
                    ),
                    *active_session_task_ids(runtime),
                ]
            )
        )
        required_tool = explicit_required_tool(
            str(state.get("latest_user_message") or ""),
            catalog_actions,
        )
        if required_tool is not None:
            tool_schemas = [
                schema
                for schema in tool_schemas
                if schema.get("function", {}).get("name") == required_tool
            ]
            events.append(
                make_event(
                    "planner_contract",
                    required_tool=required_tool,
                    mode="named_tool_choice",
                )
            )
        if policy_decision is not None:
            # 完整策略事实只保存在服务端消息元数据，供内部反馈/Q-table 复核；
            # SSE 与历史 API 通过 public_agent_event 输出脱敏执行摘要。
            events.append(make_event("strategy_policy", **policy_decision))
        if active_task_ids and runtime.approved_tool_call is None:
            assistant = {
                "role": "assistant",
                "content": (
                    "本会话仍有长任务在队列中或执行中。"
                    "本轮不重复提交工具，待任务结束后再从持久化事实继续。"
                ),
            }
            events.extend(
                [
                    make_event(
                        "agent_waiting",
                        task_ids=active_task_ids,
                        message="本会话仍有长任务执行中；已阻止重复规划。",
                    ),
                    make_event(
                        "agent_step",
                        label=f"第 {step_count} 步：等待本会话长任务",
                        status="completed",
                    ),
                ]
            )
            return {
                **state,
                "step_count": step_count,
                "llm_response": assistant,
                "messages": [
                    *state.get("messages", []),
                    normalize_assistant_message(assistant),
                ],
                "tool_calls": [],
                "active_task_ids": active_task_ids,
                "waiting_for_tasks": True,
                "final_answer": state.get("final_answer"),
                "pending_events": events,
            }
        if runtime.approved_tool_call is not None:
            approved_call = dict(runtime.approved_tool_call)
            assistant = {
                "role": "assistant",
                "content": "",
                "tool_calls": [approved_call],
            }
            tool_name, _ = tool_call_name_and_args(approved_call)
            events.extend(
                [
                    make_event(
                        "approval_consumed",
                        tool_name=tool_name,
                        approval_id=approved_call.get("approval_id"),
                    ),
                    make_event(
                        "agent_step",
                        label=f"第 {step_count} 步：执行已确认工具",
                        status="completed",
                    ),
                ]
            )
            return {
                **state,
                "step_count": step_count,
                "llm_response": assistant,
                "messages": [
                    *state.get("messages", []),
                    normalize_assistant_message(assistant),
                ],
                "tool_calls": [approved_call],
                "max_tool_calls": int(state.get("tool_call_count") or 0) + 1,
                "final_answer": state.get("final_answer"),
                "pending_events": events,
            }
        assistant = runtime.llm.chat(
            planning_messages,
            tools=True,
            tool_schemas=tool_schemas,
            tool_choice=(
                {
                    "type": "function",
                    "function": {"name": required_tool},
                }
                if required_tool is not None
                else None
            ),
            # Planner 只负责受控工具选择；DeepSeek V4 在此统一使用非思考模式，
            # 最终回答仍由独立的 Synthesizer 流式生成。
            thinking=False,
        )
        messages = [*state.get("messages", []), normalize_assistant_message(assistant)]
        tool_calls = assistant.get("tool_calls") or []
        if required_tool is not None:
            actual_tools = [
                tool_call_name_and_args(call)[0]
                for call in tool_calls
                if isinstance(call, dict)
            ]
            if actual_tools != [required_tool]:
                raise AgentPlanningContractError(required_tool, actual_tools)
        events.append(
            make_event(
                "agent_step",
                label=f"第 {step_count} 步：Planner 选择下一步动作",
                status="completed",
            )
        )
        return {
            **state,
            "step_count": step_count,
            "llm_response": assistant,
            "messages": messages,
            "tool_calls": tool_calls,
            # wzf：显式工具契约一轮只允许一个真实动作；执行后利用既有上限路由
            # 直接进入 Synthesizer，避免同步工具被同一用户指令反复强制调用。
            "max_tool_calls": (
                min(
                    int(state.get("max_tool_calls") or 1),
                    int(state.get("tool_call_count") or 0) + 1,
                )
                if required_tool is not None
                else state.get("max_tool_calls")
            ),
            "final_answer": state.get("final_answer"),
            "pending_events": events,
        }

    return node


def execute_tools_node(runtime: AgentRuntime):
    def node(state: AgentState) -> AgentState:
        events: list[dict[str, Any]] = []
        messages = list(state.get("messages", []))
        artifacts = list(state.get("artifacts", []))
        diagnostics = list(state.get("diagnostics", []))
        active_task_ids = list(state.get("active_task_ids", []))
        tool_results = list(state.get("tool_results", []))
        fingerprints = list(state.get("executed_tool_call_fingerprints", []))
        tool_call_count = int(state.get("tool_call_count") or 0)
        max_tool_calls = int(state.get("max_tool_calls") or 1)
        max_result_chars = int(state.get("max_tool_result_chars") or 8000)
        waiting_for_tasks = False
        waiting_for_approval = False
        pending_approval = state.get("pending_approval")

        for call in state.get("tool_calls", []):
            call_id = call.get("id") or f"call_{len(events)}"
            name, raw_args = tool_call_name_and_args(call)
            fingerprint = tool_call_fingerprint(name, raw_args)
            failed_fingerprints = {
                item.get("arguments_hash")
                for item in diagnostics
                if isinstance(item, dict)
                and item.get("error_type") == "tool_execution_error"
            }
            if fingerprint in failed_fingerprints:
                error = {
                    "error_type": "duplicate_failed_tool_call",
                    "tool": name,
                    "arguments_hash": fingerprint,
                    "message": "相同参数的工具调用已经失败，本轮不再重复执行。",
                }
                diagnostics.append(error)
                events.append(make_event("error", error=error))
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps(error, ensure_ascii=False),
                    }
                )
                continue
            if waiting_for_tasks and name not in LONG_RUNNING_TOOL_NAMES:
                error = {
                    "error_type": "tool_call_deferred",
                    "tool": name,
                    "message": "已有长任务入队；为避免使用未完成结果，本轮不再执行后续同步工具。",
                }
                diagnostics.append(error)
                events.append(make_event("error", error=error))
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps(error, ensure_ascii=False),
                    }
                )
                continue
            if tool_call_count >= max_tool_calls:
                error = {
                    "error_type": "tool_call_limit_reached",
                    "tool": name,
                    "message": f"本轮工具调用已达到上限 {max_tool_calls}，已停止继续执行。",
                }
                diagnostics.append(error)
                events.append(make_event("error", error=error))
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps(error, ensure_ascii=False),
                    }
                )
                continue
            manifest = get_capability_manifest(name)
            if (
                manifest.approval_required
                and runtime.approved_tool_fingerprint != fingerprint
            ):
                pending_approval = pending_approval_for_call(
                    runtime,
                    tool_name=name,
                    raw_arguments=raw_args,
                    fingerprint=fingerprint,
                )
                public_approval = public_approval_payload(pending_approval)
                waiting_for_approval = True
                events.extend(
                    [
                        make_event("approval_required", **public_approval),
                        make_event(
                            "agent_step",
                            label=f"Executor 等待确认：{name}",
                            status="completed",
                        ),
                    ]
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps(
                            {
                                "status": "waiting_for_approval",
                                **public_approval,
                            },
                            ensure_ascii=False,
                        ),
                    }
                )
                break
            tool_call_count += 1
            events.append(make_event("agent_step", label=f"Executor 执行工具：{name}", status="running"))
            events.append(
                make_event(
                    "tool_call_started",
                    tool_call_id=call_id,
                    name=name,
                    argument_keys=sorted(
                        parse_tool_argument_keys(raw_args)
                    ),
                    arguments_hash=fingerprint,
                )
            )
            # 指纹仍用于服务端执行事实和审计，但不能把合法的重复调用当成错误。
            # 同一工具可能需要在不同参数上下文、重试或结果未满足时再次执行；
            # 工具总量上限和长任务等待门禁仍负责防止无限循环与未完成结果串用。
            fingerprints.append(fingerprint)
            try:
                if runtime.lease_guard is not None:
                    runtime.lease_guard()
                # wzf：Agent 只消费服务端运行时判定；模型参数中的 native/route
                # 声称不会改变旧路径。没有可信 Harness 执行器时继续走 ToolRunner。
                try:
                    raw_claims = json.loads(raw_args or "{}")
                except json.JSONDecodeError:
                    raw_claims = None
                persisted_dispatch = None
                if callable(runtime.harness_dispatch_factory):
                    try:
                        persisted_dispatch = runtime.harness_dispatch_factory(name)
                    except Exception:
                        # 接线异常不进入普通响应；缺少完整摘要时回退旧桥。
                        persisted_dispatch = None
                harness_executor_available = callable(runtime.harness_executor)
                wiring = resolve_runtime_wiring(
                    name,
                    user_id=runtime.user.id,
                    session_id=runtime.session.id,
                    turn_id=runtime.turn_id,
                    correlation_id=runtime.correlation_id,
                    client_claims=raw_claims if isinstance(raw_claims, dict) else None,
                    persisted_dispatch=persisted_dispatch,
                    require_persisted_dispatch=harness_executor_available,
                    context=runtime.harness_wiring_context,
                )
                effective_route = "session_native" if (
                    wiring.native and harness_executor_available
                ) else "legacy_bridge"
                events.append(
                    make_event(
                        "harness_route",
                        capability_id=name,
                        route=effective_route,
                        correlation_id=wiring.correlation_id,
                        server_verified=wiring.server_verified,
                        reason=(
                            wiring.reason
                            if effective_route == wiring.route
                            else "Harness 执行器未接线，已回落旧 ToolRunner"
                        ),
                    )
                )
                tool_context = ToolContext(
                    db=runtime.db,
                    user=runtime.user,
                    session_id=UUID(str(runtime.session.id)),
                    tool_call_id=call_id,
                    operation_id=(
                        # call_id keeps repeated identical calls distinct while
                        # retaining idempotency if the same call is replayed.
                        f"agent-turn:{runtime.turn_id}:{call_id}:{fingerprint}"
                        if runtime.turn_id is not None
                        else None
                    ),
                    agent_turn_id=runtime.turn_id,
                )
                if effective_route == "session_native":
                    # Harness adapter 由服务端装配层注入；它负责把调用转换为
                    # ExecuteCapabilityCommand 并在外层 UoW 中返回兼容 ToolRunner 的摘要。
                    result = runtime.harness_executor(
                        capability_id=name,
                        raw_arguments=raw_args,
                        tool_context=tool_context,
                        wiring=wiring,
                    )
                    if not isinstance(result, dict):
                        raise TypeError("Harness executor must return a mapping result")
                else:
                    result = execute_tool(name, raw_args, tool_context)
                if runtime.lease_guard is not None:
                    runtime.lease_guard()
                record_research_tool_evidence(
                    runtime,
                    tool_name=name,
                    tool_call_id=call_id,
                    arguments_hash=fingerprint,
                    result=result,
                )
                if result.get("artifact_id"):
                    artifact = {
                        "artifact_id": result["artifact_id"],
                        "filename": result.get("filename"),
                        "download_url": result.get("download_url"),
                        "kind": result.get("kind"),
                    }
                    artifacts.append(artifact)
                    events.append(make_event("artifact_created", artifact=artifact))
                for task_summary in task_summaries_from_result(result):
                    task_id = task_summary["task_id"]
                    if task_id not in active_task_ids:
                        active_task_ids.append(task_id)
                    if task_summary.get("status") in {"queued", "running"}:
                        waiting_for_tasks = True
                    events.append(
                        make_event(
                            "task_created",
                            task_id=task_id,
                            task_type=task_summary.get("task_type"),
                            status=task_summary.get("status"),
                        )
                    )
                tool_result = compact_tool_result(result, max_result_chars)
                compact_result = json.loads(tool_result)
                tool_results.append({"tool": name, "result": compact_result})
                events.append(
                    make_event(
                        "tool_call_finished",
                        tool_call_id=call_id,
                        name=name,
                        result_summary=safe_tool_event_result(result),
                    )
                )
            except AgentLeaseRevoked:
                raise
            except Exception as exc:
                logger.exception(
                    "Agent 工具执行失败 tool=%s tool_call_id=%s",
                    name,
                    call_id,
                )
                error = {
                    "error_type": "tool_execution_error",
                    "tool": name,
                    "detail_code": exc.__class__.__name__,
                    "arguments_hash": fingerprint,
                    "message": "工具执行失败，请记录调用 ID 并查看受控服务器日志。",
                }
                diagnostics.append(error)
                events.append(make_event("error", error=error))
                tool_result = json.dumps(error, ensure_ascii=False)

            tool_message = ToolMessage(content=tool_result, tool_call_id=call_id)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_message.tool_call_id,
                    "content": str(tool_message.content),
                }
            )
            events.append(make_event("agent_step", label=f"Executor 完成工具：{name}", status="completed"))

        if waiting_for_tasks:
            events.append(
                make_event(
                    "agent_waiting",
                    task_ids=active_task_ids,
                    message="长任务已进入队列；本轮暂停，待任务结束后从持久化事实继续。",
                )
            )
        return {
            **state,
            "messages": messages,
            "tool_results": tool_results,
            "tool_calls": [],
            "artifacts": artifacts,
            "diagnostics": diagnostics,
            "active_task_ids": active_task_ids,
            "tool_call_count": tool_call_count,
            "executed_tool_call_fingerprints": fingerprints,
            "waiting_for_tasks": waiting_for_tasks,
            "waiting_for_approval": waiting_for_approval,
            "pending_approval": pending_approval,
            "pending_events": events,
        }

    return node


def synthesize_answer_node(runtime: AgentRuntime):
    def node(state: AgentState) -> AgentState:
        execution_facts = execution_facts_from_state(state)
        if state.get("waiting_for_approval"):
            approval = state.get("pending_approval")
            tool_name = (
                str(approval.get("tool_name") or "高成本工具")
                if isinstance(approval, dict)
                else "高成本工具"
            )
            return {
                **state,
                "final_answer": (
                    f"{tool_name} 的参数已通过服务端校验，但该操作可能消耗较多计算资源。"
                    "系统尚未执行它；请在当前对话确认，或调整参数后重新请求。"
                ),
                "pending_events": [
                    make_event(
                        "agent_step",
                        label="Synthesizer 记录待确认操作",
                        status="completed",
                    )
                ],
            }
        if state.get("waiting_for_tasks"):
            return {
                **state,
                "final_answer": deterministic_waiting_answer(state),
                "pending_events": [
                    make_event(
                        "agent_step",
                        label="Synthesizer 记录后台任务等待状态",
                        status="completed",
                    )
                ],
            }
        messages = [
            *state.get("messages", []),
            {
                "role": "user",
                "content": (
                    "请基于以上对话、RAG 上下文和工具结果给出中文最终回答。"
                    "不要再调用工具，不要展示内部思维链，只输出面向用户的结论、证据和下一步建议。"
                    "所有关于工具已执行、任务状态、产物和错误的陈述，都必须与下面的服务端事实一致；"
                    "事实中不存在的执行结果必须明确标为未验证，禁止推测。"
                    f"服务端执行事实：{json.dumps(execution_facts, ensure_ascii=False)}"
                ),
            },
        ]
        return {
            **state,
            "messages": messages,
            "final_answer": state.get("final_answer"),
            "pending_events": [
                make_event("agent_step", label="Synthesizer 开始生成最终回答", status="running")
            ],
        }

    return node


def route_after_plan(state: AgentState) -> str:
    if (
        state.get("tool_calls")
        and int(state.get("step_count") or 0) <= int(state.get("max_steps") or 1)
        and int(state.get("tool_call_count") or 0) < int(state.get("max_tool_calls") or 1)
    ):
        return "execute_tools"
    return "synthesize_answer"


def route_after_execute(state: AgentState) -> str:
    if state.get("waiting_for_tasks") or state.get("waiting_for_approval"):
        return "synthesize_answer"
    if int(state.get("step_count") or 0) >= int(state.get("max_steps") or 1):
        return "synthesize_answer"
    if int(state.get("tool_call_count") or 0) >= int(state.get("max_tool_calls") or 1):
        return "synthesize_answer"
    return "plan_next_action"


def build_runtime_graph(runtime: AgentRuntime):
    graph = StateGraph(AgentState)
    graph.add_node("retrieve_knowledge", retrieve_knowledge_node)
    graph.add_node("plan_next_action", plan_next_action_node(runtime))
    graph.add_node("execute_tools", execute_tools_node(runtime))
    graph.add_node("synthesize_answer", synthesize_answer_node(runtime))
    graph.set_entry_point("retrieve_knowledge")
    graph.add_edge("retrieve_knowledge", "plan_next_action")
    graph.add_conditional_edges(
        "plan_next_action",
        route_after_plan,
        {"execute_tools": "execute_tools", "synthesize_answer": "synthesize_answer"},
    )
    graph.add_conditional_edges(
        "execute_tools",
        route_after_execute,
        {"plan_next_action": "plan_next_action", "synthesize_answer": "synthesize_answer"},
    )
    graph.add_edge("synthesize_answer", END)
    return graph.compile()


class LangGraphAgentRunner:
    def __init__(self, runtime: AgentRuntime, chat_history: list[dict[str, Any]]) -> None:
        self.runtime = runtime
        self.chat_history = normalize_chat_history(chat_history)
        self.record = AgentRunRecord()

    def initial_state(self, latest_user_message: str) -> AgentState:
        settings = get_settings()
        return {
            "latest_user_message": latest_user_message,
            # wzf：当前消息已从持久化历史查询中排除，必须在这里恰好追加一次供 Planner 使用。
            "messages": [
                *self.chat_history,
                {"role": "user", "content": latest_user_message},
            ],
            "tool_calls": [],
            "tool_results": [],
            "active_task_ids": [],
            "artifacts": [],
            "diagnostics": [],
            "step_count": 0,
            "tool_call_count": 0,
            "executed_tool_call_fingerprints": [],
            "waiting_for_tasks": False,
            "waiting_for_approval": False,
            "pending_approval": None,
            "max_steps": settings.agent_max_steps,
            "max_tool_calls": settings.agent_max_tool_calls,
            "max_tool_result_chars": settings.agent_max_tool_result_chars,
            "pending_events": [],
            "final_answer": None,
        }

    def finalize_record(self, final_state: AgentState, answer: str) -> None:
        self.record.final_answer = answer
        self.record.artifacts = list(final_state.get("artifacts") or [])
        self.record.execution_facts = execution_facts_from_state(final_state)
        self.record.continuation = continuation_from_state(final_state)
        pending_approval = final_state.get("pending_approval")
        self.record.pending_approval = (
            dict(pending_approval) if isinstance(pending_approval, dict) else None
        )
        self.record.rag_backend = str(final_state.get("rag_backend") or "none")
        self.record.sources = [
            {
                "source": item.get("source"),
                "heading": item.get("heading"),
                "score": item.get("score"),
            }
            for item in final_state.get("retrieved_knowledge", [])
        ]
        self.record.suggestions = build_suggestions(self.record.events, self.record.artifacts)
        tool_failed = any(
            isinstance(item, dict)
            and item.get("error_type") == "tool_execution_error"
            for item in final_state.get("diagnostics", [])
        )
        if tool_failed and not self.record.failed:
            # A tool-level failure is part of the scientific result, not a failed
            # message transport.  Preserve diagnostics while allowing the final
            # answer and any successful tools/artifacts to reach the UI normally.
            self.record.error_code = self.record.error_code or "tool_execution_warning"

    def iter_events(self, latest_user_message: str):
        final_state = self.initial_state(latest_user_message)
        graph = build_runtime_graph(self.runtime)

        try:
            for update in graph.stream(final_state):
                if not isinstance(update, dict):
                    continue
                for payload in update.values():
                    if not isinstance(payload, dict):
                        continue
                    final_state = {**final_state, **payload}
                    for event in payload.get("pending_events", []):
                        if self.runtime.turn_id is not None:
                            event.setdefault("turn_id", str(self.runtime.turn_id))
                        self.record.events.append(event)
                        public_event = public_agent_event(event)
                        if public_event is not None:
                            yield public_event
        except AgentPlanningContractError as exc:
            logger.warning(
                "Agent Planner 执行契约未满足 required_tool=%s actual_tools=%s",
                exc.required_tool,
                exc.actual_tools,
            )
            answer = (
                f"本轮明确要求的工具 {exc.required_tool} 未被模型实际选择，"
                "系统已停止本轮，未伪造工具调用或科研结果。"
            )
            event = make_event(
                "error",
                error={
                    "error_type": "required_tool_not_called",
                    "message": answer,
                    "required_tool": exc.required_tool,
                },
            )
            self.record.events.append(event)
            self.record.failed = True
            self.record.error_code = "required_tool_not_called"
            yield event
            final_state["final_answer"] = answer
        except LlmUnavailable as exc:
            logger.warning("Agent 大模型不可用：%s", exc.__class__.__name__)
            answer = (
                "PSKit 2.0 后端正在运行，但本次请求无法连接大语言模型。"
                "RAG 来源和工具状态仍会保留在页面中，请稍后恢复本轮。"
            )
            event = make_event(
                "error",
                error={
                    "error_type": "llm_unavailable",
                    "message": "大模型服务暂不可用。",
                },
            )
            self.record.events.append(event)
            self.record.failed = True
            self.record.error_code = "llm_unavailable"
            yield event
            final_state["final_answer"] = answer
        except Exception:
            logger.exception("Agent 图执行失败")
            answer = "Agent 执行失败，请记录当前轮次并查看受控服务器日志。"
            event = make_event(
                "error",
                error={
                    "error_type": "agent_runtime_error",
                    "message": "Agent 运行失败。",
                },
            )
            self.record.events.append(event)
            self.record.failed = True
            self.record.error_code = "agent_runtime_error"
            yield event
            final_state["final_answer"] = answer

        answer = str(final_state.get("final_answer") or "")
        answer_streamed = False
        if not answer:
            answer_parts: list[str] = []
            try:
                for delta in self.runtime.llm.stream_chat_sync(
                    final_state.get("messages", [])
                ):
                    answer_parts.append(delta)
                    yield make_event("message_delta", delta=delta)
                    answer_streamed = True
                answer = "".join(answer_parts)
                if not answer:
                    answer = (
                        "工具步骤已完成，但模型没有返回最终总结。"
                        "请查看右侧工具事件和结果文件，或选择下方建议继续。"
                    )
                    yield make_event("message_delta", delta=answer)
                    answer_streamed = True
                event = make_event(
                    "agent_step", label="Synthesizer 完成最终回答", status="completed"
                )
            except LlmUnavailable as exc:
                logger.warning("Agent 最终回答生成不可用：%s", exc.__class__.__name__)
                answer = "最终回答生成暂不可用，请稍后恢复本轮。"
                yield make_event("message_delta", delta=answer)
                answer_streamed = True
                event = make_event(
                    "error",
                    error={
                        "error_type": "llm_unavailable",
                        "message": "大模型服务暂不可用。",
                    },
                )
                self.record.failed = True
                self.record.error_code = "llm_unavailable"
            self.record.events.append(event)
            yield event

        self.finalize_record(final_state, answer)

        if not answer_streamed:
            for delta in chunk_text(answer):
                yield make_event("message_delta", delta=delta)
        yield make_event("suggestions", items=self.record.suggestions)
