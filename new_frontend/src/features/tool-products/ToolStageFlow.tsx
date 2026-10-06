import { Check, Circle, LoaderCircle, X } from "lucide-react";
import type { ToolEvent } from "./toolUiSchema";

type Stage = { id: string; label: string; progress: number; status: "running" | "completed" | "failed" | "cancelled" };

function stageData(events: ToolEvent[]): Stage[] {
  const stages = new Map<string, Stage>();
  for (const event of [...events].sort((a, b) => a.sequence - b.sequence)) {
    if (!event.type.startsWith("stage.")) continue;
    const id = typeof event.data.job_id === "string" ? event.data.job_id : `stage-${event.sequence}`;
    const current = stages.get(id) ?? { id, label: `Step ${stages.size + 1}`, progress: 0, status: "running" as const };
    if (typeof event.data.label === "string" && event.data.label.trim()) current.label = event.data.label;
    if (event.type === "stage.progress" && typeof event.data.progress === "number") current.progress = Math.max(current.progress, Math.min(100, event.data.progress));
    if (event.type === "stage.completed") {
      const status = event.data.status;
      current.status = status === "failed" || status === "cancelled" ? status : "completed";
      if (current.progress === 0) current.progress = 100;
    }
    stages.set(id, current);
  }
  return [...stages.values()];
}

export function ToolStageFlow({ events }: { events: ToolEvent[] }) {
  const stages = stageData(events);
  if (!stages.length) return null;
  return <ol className="tool-stage-flow">{stages.map((stage) => <li key={stage.id} data-status={stage.status}>
    <span className="tool-stage-marker" aria-hidden="true">{stage.status === "completed" ? <Check /> : stage.status === "running" ? <LoaderCircle /> : stage.status === "failed" ? <X /> : <Circle />}</span>
    <span><strong>{stage.label}</strong><small>{stage.progress}%</small></span>
  </li>)}</ol>;
}
