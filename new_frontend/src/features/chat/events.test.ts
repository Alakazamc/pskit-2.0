import { describe, expect, it } from "vitest";
import type { RunEvent } from "../../api/types";
import { emptyRun, MAX_RETAINED_RUN_EVENTS, projectEvents } from "./events";

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
    expect(view.tools).toEqual([{ id: "call-1", name: "af3", status: "completed", summary: "Done" }]);
    expect(view.status).toBe("running");
  });
  it("tracks typed tool lifecycle without exposing arguments or results", () => {
    const view = projectEvents(emptyRun, [
      { id: "1", run_id: "run-1", type: "tool.started", data: { tool_call_id: "call-1", tool: "search_pdb" } },
      { id: "2", run_id: "run-1", type: "tool.updated", data: { tool_call_id: "call-1", tool: "search_pdb" } },
      { id: "3", run_id: "run-1", type: "tool.finished", data: { tool_call_id: "call-1", tool: "search_pdb", status: "completed", summary: "Tool completed" } },
    ]);
    expect(view.tools).toEqual([{ id: "call-1", name: "search_pdb", status: "completed", summary: "Tool completed" }]);
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

  it("replaces text across old, reset and new batches without mutating the old view", () => {
    const old = projectEvents(emptyRun, [delta("1", "old")]);
    const reset = projectEvents(old, [retry("2")]);
    const next = projectEvents(reset, [delta("3", "new")]);
    expect(reset.text).toBe("");
    expect(next.text).toBe("new");
    expect(next.cursor).toBe("3");
    expect(next.retry).toEqual({ attempt: 1, maxAttempts: 3 });
    expect(old.text).toBe("old");
  });

  it("clears an earlier error on reset and keeps a failure occurring after that reset", () => {
    const failed = projectEvents(emptyRun, [{ id: "1", run_id: "run-1", type: "run.failed", data: {
      code: "PI_RUN_FAILED", message: "old error",
    } }]);
    const reset = projectEvents(failed, [retry("2")]);
    expect(reset.error).toBeUndefined();
    expect(reset.status).toBe("running");
    const next = projectEvents(failed, [retry("2"), { id: "3", run_id: "run-1", type: "run.failed", data: {
      code: "PI_RUN_FAILED", message: "new error",
    } }]);
    expect(next.error).toBe("new error");
    expect(next.status).toBe("failed");
  });

  it("deduplicates event ids within a batch and across replays without regressing the cursor", () => {
    const event = delta("1", "x");
    const first = projectEvents(emptyRun, [event, event]);
    expect(first.text).toBe("x");
    expect(first.events).toHaveLength(1);
    const next = projectEvents(first, [event, delta("2", "y"), event, delta("2", "y")]);
    expect(next.text).toBe("xy");
    expect(next.events.map((item) => item.id)).toEqual(["1", "2"]);
    expect(next.cursor).toBe("2");
    expect(projectEvents(next, [event, delta("2", "y")])).toBe(next);
  });

  it("applies multiple resets in order while retaining plan, tools and artifacts", () => {
    const previous = projectEvents(emptyRun, [
      { id: "p", run_id: "run-1", type: "plan.created", data: { steps: [
        { id: "inspect", title: "Inspect", status: "completed" },
      ] } },
      { id: "t", run_id: "run-1", type: "tool.finished", data: {
        tool_call_id: "call-1", tool: "search_pdb", status: "completed", summary: "Found structure",
      } },
      { id: "a", run_id: "run-1", type: "artifact.created", data: {
        artifact_id: "artifact-1", name: "report.md", kind: "document",
      } },
      delta("1", "old"),
    ]);
    const next = projectEvents(previous, [delta("2", "partial"), retry("3"), delta("4", "retry partial"),
      retry("5"), delta("6", "final")]);
    expect(next.text).toBe("final");
    expect(next.plan).toEqual([{ id: "inspect", title: "Inspect", status: "completed" }]);
    expect(next.tools).toEqual([{ id: "call-1", name: "search_pdb", status: "completed", summary: "Found structure" }]);
    expect(next.artifacts).toEqual([{ id: "artifact-1", name: "report.md", kind: "document" }]);
    expect(next.cursor).toBe("6");
  });

  it("retains text and errors for retries that do not reset the message", () => {
    const previous = projectEvents(emptyRun, [delta("1", "partial"), {
      id: "2", run_id: "run-1", type: "run.failed", data: { code: "PI_RUN_FAILED", message: "failure" },
    }]);
    const event = retry("3");
    const next = projectEvents(previous, [{ ...event, data: { ...event.data, reset_message: false } }]);
    expect(next.text).toBe("partial");
    expect(next.error).toBe("failure");
  });
});

function delta(id: string, text: string): RunEvent {
  return { id, run_id: "run-1", type: "message.delta", data: { delta: text } };
}

function retry(id: string): Extract<RunEvent, { type: "run.retrying" }> {
  return { id, run_id: "run-1", type: "run.retrying", data: {
    attempt: 1, max_attempts: 3, delay_ms: 1000, reset_message: true,
  } };
}

it("bounds raw history after projecting all display state and preserves the original run time", () => {
  const first = projectEvents(emptyRun, [
    { id: "start", run_id: "run-1", created_at: "2026-10-10T00:00:00Z", type: "message.start", data: { role: "assistant" } },
    { id: "task", run_id: "run-1", type: "task.updated", data: { job_id: "job-1", status: "completed", progress: 100, label: "Task" } },
    { id: "tool", run_id: "run-1", type: "tool.finished", data: { tool_call_id: "call-1", tool: "write", status: "completed", summary: "Saved" } },
    { id: "artifact", run_id: "run-1", type: "artifact.created", data: { artifact_id: "file-1", name: "report.md", kind: "document" } },
    { id: "plan", run_id: "run-1", type: "plan.created", data: { steps: [{ id: "p1", title: "Write", status: "completed" }] } },
  ]);
  const frames = Array.from({ length: MAX_RETAINED_RUN_EVENTS * 3 }, (_, index) => delta(`delta-${index}`, "x"));
  let view = first;
  for (const frame of frames) view = projectEvents(view, [frame]);
  expect(view.events).toHaveLength(MAX_RETAINED_RUN_EVENTS);
  expect(view.text).toHaveLength(frames.length);
  expect(view.startedAt).toBe("2026-10-10T00:00:00Z");
  expect(view.tasks?.[0].data.job_id).toBe("job-1");
  expect(view.tools[0].summary).toBe("Saved"); expect(view.plan[0].status).toBe("completed");
  expect(view.artifacts[0].name).toBe("report.md");
  expect(projectEvents(view, [frames.at(-1)!])).toBe(view);
  const finished = projectEvents(view, [{ id: "end", run_id: "run-1", type: "run.completed", data: { status: "completed" } }]);
  expect(finished.ended).toBe(true); expect(finished.events).toHaveLength(MAX_RETAINED_RUN_EVENTS);
  expect(first.events).toHaveLength(5);
});
