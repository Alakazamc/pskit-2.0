import { renderHook, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import type { ResearchApi } from "../../api/types";
import { useRunEvents } from "./useRunEvents";

it("observes run events without driving a capability-specific job endpoint", async () => {
  const getRunEvents = vi.fn().mockResolvedValue([{
    id: "1", run_id: "run-1", type: "task.updated",
    data: { job_id: "job-1", label: "Background analysis", status: "running", progress: 50 },
  }]);
  const getAf3Job = vi.fn();
  const api = { getRunEvents, getAf3Job } as unknown as ResearchApi;
  const onCompleted = vi.fn();

  const { result } = renderHook(() => useRunEvents(api, "run-1", onCompleted));

  await waitFor(() => expect(result.current.status).toBe("waiting"));
  expect(getAf3Job).not.toHaveBeenCalled();
});

it("uses the live stream and updates before it closes", async () => {
  const getRunEvents = vi.fn();
  let release!: () => void;
  const keepOpen = new Promise<void>((resolve) => { release = resolve; });
  const streamRunEvents = vi.fn(async (_runId, _after, onEvent: (event: unknown) => void) => {
    onEvent({ id: "1", run_id: "run-1", type: "message.delta", data: { delta: "live" } });
    await keepOpen;
    onEvent({ id: "2", run_id: "run-1", type: "run.completed", data: { status: "completed" } });
  });
  const api = { getRunEvents, streamRunEvents } as unknown as ResearchApi;
  const onCompleted = vi.fn();
  const { result, unmount } = renderHook(() => useRunEvents(api, "run-1", onCompleted));

  await waitFor(() => expect(result.current.text).toBe("live"));
  expect(getRunEvents).not.toHaveBeenCalled();
  release();
  await waitFor(() => expect(result.current.status).toBe("completed"));
  expect(onCompleted).toHaveBeenCalledTimes(1);
  unmount();
});

it.each(["next-run", null])("never renders the previous reply while switching to %s", async (nextRun) => {
  const api = { getRunEvents: async (id: string) => id === "old-run" ? [
    { id: "1", run_id: id, type: "message.delta", data: { delta: "Previous reply" } },
    { id: "2", run_id: id, type: "run.completed", data: { status: "completed" } },
  ] : [] } as unknown as ResearchApi;
  const onCompleted = vi.fn();
  const rendered: { id: string | null; text: string }[] = [];
  const { result, rerender, unmount } = renderHook(({ id }: { id: string | null }) => {
    const view = useRunEvents(api, id, onCompleted);
    rendered.push({ id, text: view.text });
    return view;
  }, { initialProps: { id: "old-run" as string | null } });
  await waitFor(() => expect(result.current.status).toBe("completed"));
  rerender({ id: nextRun });
  expect(rendered.filter((frame) => frame.id === nextRun).every((frame) => frame.text === "")).toBe(true);
  unmount();
});
