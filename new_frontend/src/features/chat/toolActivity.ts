import type { MessagePart } from "../../api/types";

export type ToolActivityPart = Extract<MessagePart, { type: "tool_call" | "tool_result" }>;
export type ActivityCall = Extract<ToolActivityPart, { type: "tool_call" }>;
export type ActivityResult = Extract<ToolActivityPart, { type: "tool_result" }>;
export type ToolActivity = {
  key: string;
  call?: ActivityCall;
  results: ActivityResult[];
};
export type ToolActivityKind = "read" | "write" | "edit" | "bash" | "search" | "mcp" | "af3" | "unknown";
export type ToolActivityStatus = "running" | "completed" | "failed" | "pending" | "approval_required" | "cancelled" | "unknown" | "result";

export function collectToolActivities(parts: ToolActivityPart[]): ToolActivity[] {
  const activities: ToolActivity[] = [];
  const byId = new Map<string, ToolActivity>();

  for (const [index, part] of parts.entries()) {
    // A name is not an invocation identity. Legacy parts without IDs stay separate.
    const id = part.tool_call_id?.trim() ? part.tool_call_id : undefined;
    let activity = id ? byId.get(id) : undefined;
    if (!activity) {
      activity = { key: id ? `id:${id}` : `part:${part.type}:${index}`, results: [] };
      activities.push(activity);
      if (id) byId.set(id, activity);
    }
    // Keep the first position and the latest observable call state, plus every result.
    if (part.type === "tool_call") activity.call = part;
    else activity.results.push(part);
  }

  return activities;
}

export function activityTool(activity: ToolActivity): string {
  return activity.call?.tool ?? activity.results[0]?.tool ?? "";
}

export function toolActivityKind(tool: string): ToolActivityKind {
  const name = tool.trim().toLowerCase();
  if (name === "read" || name === "read_file") return "read";
  if (name === "write" || name === "write_file") return "write";
  if (name === "edit" || name === "edit_file") return "edit";
  if (name === "bash") return "bash";
  if (name === "search" || name === "find" || name === "find_files" || name === "grep" || name.startsWith("search_")) return "search";
  if (/(^|[_.:-])af3($|[_.:-])/.test(name)) return "af3";
  // MultiRemoteMcp advertises server_id__tool_name; single-server discovery
  // retains arbitrary names, which need the readable unknown fallback.
  if (name === "fetch_uniprot" || /^[a-z0-9_]+__[a-z0-9_]+$/.test(name) || /^mcp(?:$|[_.:-])/.test(name)) return "mcp";
  return "unknown";
}

export function activityStatus(activity: ToolActivity): ToolActivityStatus {
  if (!activity.call) return "result";
  switch (activity.call.status.trim().toLowerCase()) {
    case "running": return "running";
    case "completed": return "completed";
    case "failed": return "failed";
    case "pending": return "pending";
    case "approval_required": return "approval_required";
    case "cancelled": return "cancelled";
    default: return "unknown";
  }
}

const statusPriority: ToolActivityStatus[] = ["failed", "approval_required", "running", "pending", "unknown", "cancelled", "completed", "result"];

export function groupActivityStatus(activities: ToolActivity[]): ToolActivityStatus {
  const statuses = new Set(activities.map(activityStatus));
  return statusPriority.find(status => statuses.has(status)) ?? "unknown";
}

export function toolResultSummary(part: ActivityResult): string | undefined {
  for (const key of ["summary", "message", "title", "status"]) {
    const value = part.result[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return undefined;
}
