// Generated from contracts/run-event.schema.json. Do not edit by hand.

export type ApprovalRequiredData = {
  "approval_id": string;
  "capability": string;
  "estimated_gpu_minutes": number;
};

export type ApprovalRequiredEvent = {
  "id": string;
  "run_id": string;
  "created_at"?: string;
  "type": "approval.required";
  "data": ApprovalRequiredData;
};

export type ApprovalResolvedData = {
  "approval_id": string;
  "decision": "approved" | "rejected";
  "job_id"?: string | null;
};

export type ApprovalResolvedEvent = {
  "id": string;
  "run_id": string;
  "created_at"?: string;
  "type": "approval.resolved";
  "data": ApprovalResolvedData;
};

export type ArtifactCreatedData = {
  "artifact_id": string;
  "name": string;
  "kind": string;
};

export type ArtifactCreatedEvent = {
  "id": string;
  "run_id": string;
  "created_at"?: string;
  "type": "artifact.created";
  "data": ArtifactCreatedData;
};

export type MessageBoundaryData = {
  "role"?: "assistant";
};

export type MessageDeltaData = {
  "delta": string;
};

export type MessageDeltaEvent = {
  "id": string;
  "run_id": string;
  "created_at"?: string;
  "type": "message.delta";
  "data": MessageDeltaData;
};

export type MessageEndEvent = {
  "id": string;
  "run_id": string;
  "created_at"?: string;
  "type": "message.end";
  "data": MessageBoundaryData;
};

export type MessageStartEvent = {
  "id": string;
  "run_id": string;
  "created_at"?: string;
  "type": "message.start";
  "data": MessageBoundaryData;
};

export type PlanCreatedEvent = {
  "id": string;
  "run_id": string;
  "created_at"?: string;
  "type": "plan.created";
  "data": PlanSnapshot;
};

export type PlanSnapshot = {
  "steps": (PlanStep)[];
};

export type PlanStep = {
  "id": string;
  "title": string;
  "status": "pending" | "in_progress" | "completed" | "blocked";
};

export type PlanUpdatedEvent = {
  "id": string;
  "run_id": string;
  "created_at"?: string;
  "type": "plan.updated";
  "data": PlanSnapshot;
};

export type RunCancelledData = {
  "status"?: "cancelled";
};

export type RunCancelledEvent = {
  "id": string;
  "run_id": string;
  "created_at"?: string;
  "type": "run.cancelled";
  "data": RunCancelledData;
};

export type RunCompletedData = {
  "status"?: "completed";
};

export type RunCompletedEvent = {
  "id": string;
  "run_id": string;
  "created_at"?: string;
  "type": "run.completed";
  "data": RunCompletedData;
};

export type RunFailedData = {
  "code": string;
  "message": string;
};

export type RunFailedEvent = {
  "id": string;
  "run_id": string;
  "created_at"?: string;
  "type": "run.failed";
  "data": RunFailedData;
};

export type RunRetryingData = {
  "attempt": number;
  "max_attempts": number;
  "delay_ms": number;
  "reset_message"?: boolean;
};

export type RunRetryingEvent = {
  "id": string;
  "run_id": string;
  "created_at"?: string;
  "type": "run.retrying";
  "data": RunRetryingData;
};

export type TaskUpdatedData = {
  "job_id": string;
  "label"?: string | null;
  "status": "queued" | "running" | "completed" | "failed" | "cancelled";
  "progress": number;
};

export type TaskUpdatedEvent = {
  "id": string;
  "run_id": string;
  "created_at"?: string;
  "type": "task.updated";
  "data": TaskUpdatedData;
};

export type ToolFinishedData = {
  "tool_call_id": string;
  "tool": string;
  "status": "completed" | "failed" | "pending" | "approval_required";
  "summary": string;
};

export type ToolFinishedEvent = {
  "id": string;
  "run_id": string;
  "created_at"?: string;
  "type": "tool.finished";
  "data": ToolFinishedData;
};

export type ToolStartedData = {
  "tool_call_id": string;
  "tool": string;
};

export type ToolStartedEvent = {
  "id": string;
  "run_id": string;
  "created_at"?: string;
  "type": "tool.started";
  "data": ToolStartedData;
};

export type ToolUpdatedData = {
  "tool_call_id": string;
  "tool": string;
  "summary"?: string | null;
};

export type ToolUpdatedEvent = {
  "id": string;
  "run_id": string;
  "created_at"?: string;
  "type": "tool.updated";
  "data": ToolUpdatedData;
};

export type UsageUpdatedData = {
  "gpu_remaining": number;
};

export type UsageUpdatedEvent = {
  "id": string;
  "run_id": string;
  "created_at"?: string;
  "type": "usage.updated";
  "data": UsageUpdatedData;
};

export type RunEvent =
  MessageStartEvent
  | MessageDeltaEvent
  | MessageEndEvent
  | ToolStartedEvent
  | ToolUpdatedEvent
  | ToolFinishedEvent
  | PlanCreatedEvent
  | PlanUpdatedEvent
  | RunCompletedEvent
  | RunFailedEvent
  | RunCancelledEvent
  | RunRetryingEvent
  | ApprovalRequiredEvent
  | ApprovalResolvedEvent
  | TaskUpdatedEvent
  | UsageUpdatedEvent
  | ArtifactCreatedEvent;
