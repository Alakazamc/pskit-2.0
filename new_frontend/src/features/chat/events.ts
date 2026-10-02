import type { PlanStep, RunEvent } from "../../api/types";

export type RunView = { events: RunEvent[]; cursor?: string; status: "idle" | "running" | "waiting" | "completed" | "failed" | "cancelled"; jobId?: string; jobLabel?: string; progress: number; text: string; error?: string; retry?: { attempt: number; maxAttempts: number }; approval?: { id: string; capability: string; estimatedMinutes: number }; artifacts: { id: string; name: string; kind: string }[]; tools: { id: string; name: string; status: string }[]; plan: PlanStep[] };
export const emptyRun: RunView = { events: [], status: "idle", progress: 0, text: "", artifacts: [], tools: [], plan: [] };

export function projectEvents(previous: RunView, incoming: RunEvent[]): RunView {
  let next = { ...previous, events: [...previous.events], artifacts: [...previous.artifacts], tools: [...previous.tools], plan: [...previous.plan] };
  const seen = new Set(previous.events.map((event) => event.id));
  for (const event of incoming) {
    if (seen.has(event.id)) continue;
    seen.add(event.id);
    next.events.push(event);
    next.cursor = event.id;
    switch (event.type) {
      case "message.start":
      case "message.end": break;
      case "message.delta": next.text += event.data.delta; break;
      case "tool.started":
        next.tools = [...next.tools.filter((tool) => tool.id !== event.data.tool_call_id),
          { id: event.data.tool_call_id, name: event.data.tool, status: "running" }];
        break;
      case "tool.updated": break;
      case "tool.finished":
        next.tools = [...next.tools.filter((tool) => tool.id !== event.data.tool_call_id),
          { id: event.data.tool_call_id, name: event.data.tool, status: event.data.status }];
        break;
      case "plan.created":
      case "plan.updated": next.plan = event.data.steps; break;
      case "task.updated":
        next.jobId = event.data.job_id; next.jobLabel = event.data.label ?? undefined; next.progress = event.data.progress;
        next.status = event.data.status === "completed" ? "running" : event.data.status === "failed" ? "failed" : event.data.status === "cancelled" ? "cancelled" : "waiting";
        break;
      case "artifact.created":
        next.artifacts.push({ id: event.data.artifact_id, name: event.data.name, kind: event.data.kind });
        break;
      case "run.completed": next.status = "completed"; break;
      case "run.failed": next.status = "failed"; next.error = event.data.message; break;
      case "run.cancelled": next.status = "cancelled"; break;
      case "run.retrying":
        next.retry = { attempt: event.data.attempt, maxAttempts: event.data.max_attempts };
        if (event.data.reset_message) {
          next.text = "";
          next.error = undefined;
          next.status = "running";
        }
        break;
      case "approval.required":
        next.approval = { id: event.data.approval_id, capability: event.data.capability, estimatedMinutes: event.data.estimated_gpu_minutes };
        next.status = "waiting";
        break;
      case "approval.resolved": next.approval = undefined; break;
      case "usage.updated": break;
    }
  }
  if (next.status === "idle" && next.events.length) next = { ...next, status: "running" };
  return next;
}
