import type { PlanStep, RunEvent } from "../../api/types";

// Raw frames are a bounded reconnect/debug window; display state lives in projections.
export const MAX_RETAINED_RUN_EVENTS = 512;
type TaskEvent = Extract<RunEvent, { type: "task.updated" }>;
export type RunView = { events: RunEvent[]; cursor?: string; startedAt?: string; ended?: boolean; tasks?: TaskEvent[]; status: "idle" | "running" | "waiting" | "completed" | "failed" | "cancelled"; jobId?: string; jobLabel?: string; progress: number; text: string; error?: string; retry?: { attempt: number; maxAttempts: number }; approval?: { id: string; capability: string; estimatedMinutes: number }; artifacts: { id: string; name: string; kind: string }[]; tools: { id: string; name: string; status: string; summary: string }[]; plan: PlanStep[] };
export const emptyRun: RunView = { events: [], status: "idle", progress: 0, text: "", artifacts: [], tools: [], plan: [] };

export function projectEvents(previous: RunView, incoming: RunEvent[]): RunView {
  if (incoming.length === 0) return previous;

  // Filter out duplicate events first
  const seen = new Set(previous.events.map((event) => event.id));
  const newEvents = incoming.filter((event) => {
    if (seen.has(event.id)) return false;
    seen.add(event.id);
    return true;
  });

  if (newEvents.length === 0) return previous;

  // Start with previous view, only clone arrays when necessary
  let next = previous;
  let eventsChanged = false;
  let artifactsChanged = false;
  let toolsChanged = false;
  let planChanged = false;

  // Accumulate changes before creating new objects
  const eventsToAdd: RunEvent[] = [];
  const newArtifacts: typeof previous.artifacts = [];
  const toolUpdates = new Map<string, { id: string; name: string; status: string; summary: string }>();
  let newPlan: PlanStep[] | undefined;
  let statusChange: RunView["status"] | undefined;
  let text = previous.text;
  let errorMessage = previous.error;
  let newRetry: RunView["retry"] | undefined;
  let newApproval: RunView["approval"] | undefined;
  let approvalCleared = false;
  let newJobId: string | undefined;
  let newJobLabel: string | undefined;
  let newProgress: number | undefined;
  let tasks: Map<string, TaskEvent> | undefined;
  let tasksChanged = false;
  let ended = previous.ended;

  for (const event of newEvents) {
    eventsToAdd.push(event);

    switch (event.type) {
      case "message.start":
      case "message.end":
        break;
      case "message.delta":
        text += event.data.delta;
        break;
      case "tool.started":
        toolUpdates.set(event.data.tool_call_id, {
          id: event.data.tool_call_id,
          name: event.data.tool,
          status: "running",
          summary: "Tool running"
        });
        toolsChanged = true;
        break;
      case "tool.updated":
        {
          const current = toolUpdates.get(event.data.tool_call_id)
            ?? previous.tools.find(tool => tool.id === event.data.tool_call_id);
          toolUpdates.set(event.data.tool_call_id, {
            id: event.data.tool_call_id,
            name: event.data.tool,
            status: current?.status ?? "running",
            summary: event.data.summary ?? current?.summary ?? "Tool running"
          });
          toolsChanged = true;
        }
        break;
      case "tool.finished":
        toolUpdates.set(event.data.tool_call_id, {
          id: event.data.tool_call_id,
          name: event.data.tool,
          status: event.data.status,
          summary: event.data.summary
        });
        toolsChanged = true;
        break;
      case "plan.created":
      case "plan.updated":
        newPlan = event.data.steps;
        planChanged = true;
        break;
      case "task.updated":
        tasks ??= new Map((previous.tasks ?? []).map((item) => [item.data.job_id, item]));
        tasks.set(event.data.job_id, event);
        tasksChanged = true;
        newJobId = event.data.job_id;
        newJobLabel = event.data.label ?? undefined;
        newProgress = event.data.progress;
        statusChange = event.data.status === "completed" ? "running"
          : event.data.status === "failed" ? "failed"
          : event.data.status === "cancelled" ? "cancelled"
          : "waiting";
        break;
      case "artifact.created":
        newArtifacts.push({
          id: event.data.artifact_id,
          name: event.data.name,
          kind: event.data.kind
        });
        artifactsChanged = true;
        break;
      case "run.completed":
        ended = true;
        statusChange = "completed";
        break;
      case "run.failed":
        ended = true;
        statusChange = "failed";
        errorMessage = event.data.message;
        break;
      case "run.cancelled":
        ended = true;
        statusChange = "cancelled";
        break;
      case "run.retrying":
        newRetry = {
          attempt: event.data.attempt,
          maxAttempts: event.data.max_attempts
        };
        if (event.data.reset_message) {
          ended = false;
          text = "";
          errorMessage = undefined;
          statusChange = "running";
        }
        break;
      case "approval.required":
        newApproval = {
          id: event.data.approval_id,
          capability: event.data.capability,
          estimatedMinutes: event.data.estimated_gpu_minutes
        };
        statusChange = "waiting";
        break;
      case "approval.resolved":
        approvalCleared = true;
        break;
      case "usage.updated":
        break;
    }
  }

  // Only create new object if something actually changed
  if (eventsToAdd.length > 0) {
    next = { ...next, events: [...next.events, ...eventsToAdd].slice(-MAX_RETAINED_RUN_EVENTS),
      startedAt: previous.startedAt ?? newEvents.find((event) => event.created_at)?.created_at, ended };
    next.cursor = eventsToAdd[eventsToAdd.length - 1]?.id;
    eventsChanged = true;
  }

  if (tasksChanged && tasks) next = { ...next, tasks: [...tasks.values()] };

  if (text !== previous.text) {
    next = { ...next, text };
  }

  if (artifactsChanged) {
    next = { ...next, artifacts: [...next.artifacts, ...newArtifacts] };
  }

  if (toolsChanged) {
    const existingIds = new Set(next.tools.map(tool => tool.id));
    const updatedTools = next.tools.map(tool => toolUpdates.get(tool.id) ?? tool);
    for (const tool of toolUpdates.values()) {
      if (!existingIds.has(tool.id)) updatedTools.push(tool);
    }
    next = { ...next, tools: updatedTools };
  }

  if (planChanged && newPlan) {
    next = { ...next, plan: newPlan };
  }

  if (statusChange !== undefined) {
    next = { ...next, status: statusChange };
  }

  if (errorMessage !== previous.error) {
    next = { ...next, error: errorMessage };
  }

  if (newRetry !== undefined) {
    next = { ...next, retry: newRetry };
  }

  if (newApproval !== undefined) {
    next = { ...next, approval: newApproval };
  }

  if (approvalCleared) {
    next = { ...next, approval: undefined };
  }

  if (newJobId !== undefined) {
    next = { ...next, jobId: newJobId };
  }

  if (newJobLabel !== undefined) {
    next = { ...next, jobLabel: newJobLabel };
  }

  if (newProgress !== undefined) {
    next = { ...next, progress: newProgress };
  }

  if (next.status === "idle" && eventsChanged) {
    next = { ...next, status: "running" };
  }

  return next;
}
