import type { PlanStep, RunEvent } from "../../api/types";

export type RunView = { events: RunEvent[]; cursor?: string; status: "idle" | "running" | "waiting" | "completed" | "failed" | "cancelled"; jobId?: string; jobLabel?: string; progress: number; text: string; error?: string; retry?: { attempt: number; maxAttempts: number }; approval?: { id: string; capability: string; estimatedMinutes: number }; artifacts: { id: string; name: string; kind: string }[]; tools: { id: string; name: string; status: string }[]; plan: PlanStep[] };
export const emptyRun: RunView = { events: [], status: "idle", progress: 0, text: "", artifacts: [], tools: [], plan: [] };

export function projectEvents(previous: RunView, incoming: RunEvent[]): RunView {
  if (incoming.length === 0) return previous;

  // Filter out duplicate events first
  const seen = new Set(previous.events.map((event) => event.id));
  const newEvents = incoming.filter((event) => !seen.has(event.id));

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
  const toolUpdates = new Map<string, { id: string; name: string; status: string }>();
  let newPlan: PlanStep[] | undefined;
  let statusChange: RunView["status"] | undefined;
  let textDelta = "";
  let errorMessage: string | undefined;
  let newRetry: RunView["retry"] | undefined;
  let newApproval: RunView["approval"] | undefined;
  let approvalCleared = false;
  let newJobId: string | undefined;
  let newJobLabel: string | undefined;
  let newProgress: number | undefined;

  for (const event of newEvents) {
    eventsToAdd.push(event);

    switch (event.type) {
      case "message.start":
      case "message.end":
        break;
      case "message.delta":
        textDelta += event.data.delta;
        break;
      case "tool.started":
        toolUpdates.set(event.data.tool_call_id, {
          id: event.data.tool_call_id,
          name: event.data.tool,
          status: "running"
        });
        toolsChanged = true;
        break;
      case "tool.updated":
        break;
      case "tool.finished":
        toolUpdates.set(event.data.tool_call_id, {
          id: event.data.tool_call_id,
          name: event.data.tool,
          status: event.data.status
        });
        toolsChanged = true;
        break;
      case "plan.created":
      case "plan.updated":
        newPlan = event.data.steps;
        planChanged = true;
        break;
      case "task.updated":
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
        statusChange = "completed";
        break;
      case "run.failed":
        statusChange = "failed";
        errorMessage = event.data.message;
        break;
      case "run.cancelled":
        statusChange = "cancelled";
        break;
      case "run.retrying":
        newRetry = {
          attempt: event.data.attempt,
          maxAttempts: event.data.max_attempts
        };
        if (event.data.reset_message) {
          textDelta = "";
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
    next = { ...next, events: [...next.events, ...eventsToAdd] };
    next.cursor = eventsToAdd[eventsToAdd.length - 1]?.id;
    eventsChanged = true;
  }

  if (textDelta) {
    next = { ...next, text: next.text + textDelta };
  }

  if (artifactsChanged) {
    next = { ...next, artifacts: [...next.artifacts, ...newArtifacts] };
  }

  if (toolsChanged) {
    const updatedTools = next.tools.filter(tool => !toolUpdates.has(tool.id));
    next = { ...next, tools: [...updatedTools, ...Array.from(toolUpdates.values())] };
  }

  if (planChanged && newPlan) {
    next = { ...next, plan: newPlan };
  }

  if (statusChange !== undefined) {
    next = { ...next, status: statusChange };
  }

  if (errorMessage !== undefined) {
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
