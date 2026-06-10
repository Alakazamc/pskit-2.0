from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session

from app.agent.llm import LlmUnavailable, OpenAICompatibleClient, default_system_prompt
from app.agent.state import AgentState
from app.db.models import AgentSession, User
from app.rag.retriever import retrieve
from app.tools.runner import ToolContext, execute_tool


MAX_AGENT_STEPS = 6


@dataclass
class AgentRunRecord:
    events: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    rag_backend: str = "none"
    final_answer: str = ""
    suggestions: list[str] = field(default_factory=list)


@dataclass
class AgentRuntime:
    db: Session
    user: User
    session: AgentSession
    llm: OpenAICompatibleClient = field(default_factory=OpenAICompatibleClient)


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
    return {"type": event_type, **payload}


def build_suggestions(events: list[dict[str, Any]], artifacts: list[dict[str, Any]]) -> list[str]:
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
        assistant = runtime.llm.chat(state.get("messages", []), tools=True)
        messages = [*state.get("messages", []), normalize_assistant_message(assistant)]
        tool_calls = assistant.get("tool_calls") or []
        content = assistant.get("content") or assistant.get("reasoning_content") or ""
        events = [
            make_event(
                "agent_step",
                label=f"第 {step_count} 步：Planner 选择下一步动作",
                status="completed",
            )
        ]
        return {
            **state,
            "step_count": step_count,
            "llm_response": assistant,
            "messages": messages,
            "tool_calls": tool_calls,
            "final_answer": content if content and not tool_calls else state.get("final_answer"),
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

        for call in state.get("tool_calls", []):
            call_id = call.get("id") or f"call_{len(events)}"
            name, raw_args = tool_call_name_and_args(call)
            events.append(make_event("agent_step", label=f"Executor 执行工具：{name}", status="running"))
            events.append(
                make_event(
                    "tool_call_started",
                    tool_call_id=call_id,
                    name=name,
                    args=raw_args,
                )
            )
            try:
                result = execute_tool(
                    name,
                    raw_args,
                    ToolContext(
                        db=runtime.db,
                        user=runtime.user,
                        session_id=UUID(str(runtime.session.id)),
                        tool_call_id=call_id,
                    ),
                )
                events.append(
                    make_event(
                        "tool_call_finished",
                        tool_call_id=call_id,
                        name=name,
                        result=result,
                    )
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
                if result.get("task_id"):
                    active_task_ids.append(str(result["task_id"]))
                    events.append(
                        make_event(
                            "task_created",
                            task_id=result["task_id"],
                            task_type=result.get("task_type"),
                            status=result.get("status"),
                        )
                    )
                tool_result = json.dumps(result, ensure_ascii=False)
                tool_results.append({"tool": name, "result": result})
            except Exception as exc:
                error = {
                    "error_type": "tool_execution_error",
                    "tool": name,
                    "message": str(exc),
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

        return {
            **state,
            "messages": messages,
            "tool_results": tool_results,
            "tool_calls": [],
            "artifacts": artifacts,
            "diagnostics": diagnostics,
            "active_task_ids": active_task_ids,
            "pending_events": events,
        }

    return node


def synthesize_answer_node(runtime: AgentRuntime):
    def node(state: AgentState) -> AgentState:
        final_answer = state.get("final_answer")
        if final_answer:
            return {
                **state,
                "pending_events": [
                    make_event("agent_step", label="Synthesizer 生成最终回答", status="completed")
                ],
            }

        messages = [
            *state.get("messages", []),
            {
                "role": "user",
                "content": "请基于以上工具结果给出中文最终回答，不要再调用工具。",
            },
        ]
        assistant = runtime.llm.chat(messages, tools=False)
        final_answer = (
            assistant.get("content")
            or "工具步骤已完成，但模型没有返回最终总结。请查看右侧工具事件和结果文件，或选择下方建议继续。"
        )
        return {
            **state,
            "messages": [*messages, normalize_assistant_message(assistant)],
            "final_answer": final_answer,
            "pending_events": [
                make_event("agent_step", label="Synthesizer 汇总工具结果", status="completed")
            ],
        }

    return node


def route_after_plan(state: AgentState) -> str:
    if state.get("tool_calls") and int(state.get("step_count") or 0) <= MAX_AGENT_STEPS:
        return "execute_tools"
    return "synthesize_answer"


def route_after_execute(state: AgentState) -> str:
    if int(state.get("step_count") or 0) >= MAX_AGENT_STEPS:
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

    def iter_events(self, latest_user_message: str):
        final_state: AgentState = {
            "latest_user_message": latest_user_message,
            "messages": self.chat_history,
            "tool_calls": [],
            "tool_results": [],
            "active_task_ids": [],
            "artifacts": [],
            "diagnostics": [],
            "step_count": 0,
            "pending_events": [],
            "final_answer": None,
        }
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
                        self.record.events.append(event)
                        yield event
        except LlmUnavailable as exc:
            answer = (
                "PSKit 2.0 后端正在运行，但本次请求无法连接大语言模型。"
                f"原因：{exc}。RAG 来源和工具状态仍会保留在页面中。"
            )
            event = make_event("error", error={"error_type": "llm_unavailable", "message": str(exc)})
            self.record.events.append(event)
            yield event
            final_state["final_answer"] = answer
        except Exception as exc:
            answer = f"Agent 执行失败：{exc}"
            event = make_event("error", error={"error_type": "agent_runtime_error", "message": str(exc)})
            self.record.events.append(event)
            yield event
            final_state["final_answer"] = answer

        answer = str(final_state.get("final_answer") or "")
        self.record.final_answer = answer
        self.record.artifacts = list(final_state.get("artifacts") or [])
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

        for delta in chunk_text(answer):
            yield make_event("message_delta", delta=delta)
        yield make_event("suggestions", items=self.record.suggestions)
