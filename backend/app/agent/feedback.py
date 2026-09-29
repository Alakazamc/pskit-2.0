from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agent.policy import state_key


INVALID_CALL_ERROR_TYPES = {
    "duplicate_tool_call",
    "tool_call_deferred",
    "tool_call_limit_reached",
    "invalid_tool_arguments",
    "unknown_tool",
}


@dataclass(frozen=True)
class AgentActionTrace:
    tool_call_id: str
    action: str
    state: dict[str, Any]
    state_key: str
    execution_status: str
    success: bool | None
    invalid_call: bool
    error_type: str | None
    task_ids: tuple[str, ...]
    task_statuses: dict[str, str]
    artifact_ids: tuple[str, ...]
    event_index: int
    policy_version: int | None
    action_rank: int | None
    allowed_actions: tuple[str, ...]

    def outcome(self) -> dict[str, Any]:
        return {
            "execution_status": self.execution_status,
            "success": self.success,
            "invalid_call": self.invalid_call,
            "error_type": self.error_type,
            "task_ids": list(self.task_ids),
            "task_statuses": dict(self.task_statuses),
            "artifact_ids": list(self.artifact_ids),
            "event_index": self.event_index,
            "policy_version_at_action": self.policy_version,
            "action_rank_at_action": self.action_rank,
            "allowed_actions_at_action": list(self.allowed_actions),
        }


def extract_agent_action_traces(metadata: dict[str, Any] | None) -> list[AgentActionTrace]:
    """从服务端保存的 Agent 事件中提取可反馈的真实工具行动。"""
    events = (metadata or {}).get("events")
    if not isinstance(events, list):
        return []

    policy_state: dict[str, Any] | None = None
    traces: list[dict[str, Any]] = []
    active_trace: dict[str, Any] | None = None

    for event_index, event in enumerate(events):
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        if event_type == "strategy_policy":
            candidate_state = event.get("state")
            if isinstance(candidate_state, dict):
                policy_state = {
                    **candidate_state,
                    "_policy_version": event.get("version"),
                    "_allowed_actions": list(event.get("allowed_actions") or []),
                    "_recommended_actions": list(
                        event.get("recommended_actions") or []
                    ),
                }
            else:
                policy_state = None
            continue

        if event_type == "tool_call_started":
            call_id = str(event.get("tool_call_id") or "").strip()
            action = str(event.get("name") or "").strip()
            if not call_id or not action or policy_state is None:
                active_trace = None
                continue
            active_trace = {
                "tool_call_id": call_id,
                "action": action,
                "state": dict(policy_state),
                "state_key": state_key(policy_state),
                "finished": False,
                "error_type": None,
                "task_statuses": {},
                "artifact_ids": [],
                "event_index": event_index,
                "policy_version": (
                    int(policy_state.get("_policy_version"))
                    if isinstance(policy_state.get("_policy_version"), int)
                    else None
                ),
                "allowed_actions": tuple(
                    str(item)
                    for item in policy_state.get("_allowed_actions") or []
                    if str(item)
                ),
                "action_rank": (
                    list(policy_state.get("_recommended_actions") or []).index(action) + 1
                    if action in list(policy_state.get("_recommended_actions") or [])
                    else None
                ),
            }
            traces.append(active_trace)
            continue

        if active_trace is None:
            continue

        if event_type == "task_created":
            task_id = str(event.get("task_id") or "").strip()
            if task_id:
                active_trace["task_statuses"][task_id] = str(event.get("status") or "unknown")
            continue

        if event_type == "artifact_created":
            artifact = event.get("artifact")
            artifact_id = (
                str(artifact.get("artifact_id") or "").strip() if isinstance(artifact, dict) else ""
            )
            if artifact_id:
                active_trace["artifact_ids"].append(artifact_id)
            continue

        if event_type == "error":
            error = event.get("error")
            if not isinstance(error, dict):
                continue
            error_tool = str(error.get("tool") or "").strip()
            if error_tool and error_tool != active_trace["action"]:
                continue
            active_trace["error_type"] = str(error.get("error_type") or "tool_execution_error")
            continue

        if event_type == "tool_call_finished":
            call_id = str(event.get("tool_call_id") or "").strip()
            if call_id == active_trace["tool_call_id"]:
                active_trace["finished"] = True
                active_trace = None
            continue

    result: list[AgentActionTrace] = []
    for trace in traces:
        error_type = trace["error_type"]
        task_statuses = dict(trace["task_statuses"])
        if error_type:
            execution_status = "failed"
        elif any(status in {"queued", "running"} for status in task_statuses.values()):
            execution_status = "queued"
        elif trace["finished"]:
            execution_status = "completed"
        else:
            execution_status = "interrupted"
        result.append(
            AgentActionTrace(
                tool_call_id=trace["tool_call_id"],
                action=trace["action"],
                state={
                    key: value
                    for key, value in trace["state"].items()
                    if not key.startswith("_")
                },
                state_key=state_key(
                    {
                        key: value
                        for key, value in trace["state"].items()
                        if not key.startswith("_")
                    }
                ),
                execution_status=execution_status,
                success=bool(trace["finished"] and not error_type),
                invalid_call=error_type in INVALID_CALL_ERROR_TYPES,
                error_type=error_type,
                task_ids=tuple(task_statuses),
                task_statuses=task_statuses,
                artifact_ids=tuple(trace["artifact_ids"]),
                event_index=trace["event_index"],
                policy_version=trace["policy_version"],
                action_rank=trace["action_rank"],
                allowed_actions=trace["allowed_actions"],
            )
        )
    return result
