import { describe, expect, it } from "vitest";
import type { RunEvent } from "../../api/types";
import { emptyRun, projectEvents } from "./events";

describe("run event projection", () => {
  it("projects the latest structured plan snapshot across reconnects", () => {
    const created: RunEvent = { id: "1", run_id: "run-plan", type: "plan.created", data: {
      steps: [{ id: "inspect", title: "Inspect sequences", status: "in_progress" }],
    } };
    const first = projectEvents(emptyRun, [created]);
    const updated = projectEvents(first, [created, { id: "2", run_id: "run-plan", type: "plan.updated", data: {
      steps: [{ id: "inspect", title: "Inspect sequences", status: "completed" },
        { id: "report", title: "Write report", status: "pending" }],
    } }]);
    expect(updated.plan).toEqual([
      { id: "inspect", title: "Inspect sequences", status: "completed" },
      { id: "report", title: "Write report", status: "pending" },
    ]);
    expect(updated.events).toHaveLength(2);
  });
  it("keeps assistant message boundaries in the cursor without duplicating text", () => {
    const view = projectEvents(emptyRun, [
      { id: "1", run_id: "run-1", type: "message.start", data: { role: "assistant" } },
      { id: "2", run_id: "run-1", type: "message.delta", data: { delta: "Hello" } },
      { id: "3", run_id: "run-1", type: "message.end", data: { role: "assistant" } },
    ]);
    expect(view.text).toBe("Hello");
    expect(view.cursor).toBe("3");
    expect(view.status).toBe("running");
  });
  it("discards partial text when a recovered Pi run starts a safe retry", () => {
    const view = projectEvents(emptyRun, [
      { id: "0", run_id: "run-1", type: "plan.created", data: { steps: [
        { id: "inspect", title: "Inspect", status: "completed" },
      ] } },
      { id: "0a", run_id: "run-1", type: "tool.finished", data: {
        tool_call_id: "call-1", tool: "af3", status: "completed", summary: "Done",
      } },
      { id: "1", run_id: "run-1", type: "message.delta", data: { delta: "partial" } },
      { id: "2", run_id: "run-1", type: "run.retrying", data: {
        attempt: 1, max_attempts: 3, delay_ms: 1000, reset_message: true,
      } },
      { id: "3", run_id: "run-1", type: "message.delta", data: { delta: "complete" } },
    ]);
    expect(view.text).toBe("complete");
    expect(view.plan).toHaveLength(1);
    expect(view.tools).toEqual([{ id: "call-1", name: "af3", status: "completed" }]);
    expect(view.status).toBe("running");
  });
  it("tracks typed tool lifecycle without exposing arguments or results", () => {
    const view = projectEvents(emptyRun, [
      { id: "1", run_id: "run-1", type: "tool.started", data: { tool_call_id: "call-1", tool: "search_pdb" } },
      { id: "2", run_id: "run-1", type: "tool.updated", data: { tool_call_id: "call-1", tool: "search_pdb" } },
      { id: "3", run_id: "run-1", type: "tool.finished", data: { tool_call_id: "call-1", tool: "search_pdb", status: "completed", summary: "Tool completed" } },
    ]);
    expect(view.tools).toEqual([{ id: "call-1", name: "search_pdb", status: "completed" }]);
    expect(view.cursor).toBe("3");
  });
  it("deduplicates replayed events and tracks task completion", () => {
    const start: RunEvent[] = [
      { id: "1", run_id: "run-1", type: "message.delta", data: { delta: "Working" } },
      { id: "2", run_id: "run-1", type: "task.updated", data: { job_id: "job-1", status: "queued", progress: 0 } },
    ];
    const first = projectEvents(emptyRun, start);
    const replay = projectEvents(first, [
      start[1],
      { id: "3", run_id: "run-1", type: "artifact.created", data: { artifact_id: "a1", name: "result.cif", kind: "structure" } },
      { id: "4", run_id: "run-1", type: "run.completed", data: { status: "completed" } },
    ]);
    expect(first.status).toBe("waiting");
    expect(replay.events).toHaveLength(4);
    expect(replay.text).toBe("Working");
    expect(replay.artifacts).toEqual([{ id: "a1", name: "result.cif", kind: "structure" }]);
    expect(replay.status).toBe("completed");
    expect(replay.cursor).toBe("4");
  });

  it("shows a Pi failure as a terminal error", () => {
    const view = projectEvents(emptyRun, [
      { id: "1", run_id: "run-2", type: "run.failed", data: { code: "PI_RUN_FAILED", message: "Pi Agent 执行失败" } },
    ]);
    expect(view.status).toBe("failed");
    expect(view.error).toBe("Pi Agent 执行失败");
  });

  it("treats run cancellation as a terminal event", () => {
    const view = projectEvents(emptyRun, [
      { id: "1", run_id: "run-3", type: "task.updated", data: { job_id: "job-1", status: "cancelled", progress: 0 } },
      { id: "2", run_id: "run-3", type: "run.cancelled", data: { status: "cancelled" } },
    ]);
    expect(view.status).toBe("cancelled");
    expect(view.cursor).toBe("2");
  });
});
