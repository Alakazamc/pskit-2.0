from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    user_id: str
    session_id: str
    messages: list[dict[str, Any]]
    latest_user_message: str
    selected_skill: dict[str, Any] | None
    rag_backend: str
    retrieved_knowledge: list[dict[str, Any]]
    llm_response: dict[str, Any] | None
    tool_calls: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    active_task_ids: list[str]
    artifacts: list[dict[str, Any]]
    diagnostics: list[dict[str, Any]]
    pending_events: list[dict[str, Any]]
    step_count: int
    final_answer: str | None
