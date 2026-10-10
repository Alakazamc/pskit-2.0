import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ResearchApi, RunEvent } from "../../api/types";
import { ApiError, createHttpApi } from "../../api/http";
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

describe("run stream reconnection", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(Math, "random").mockReturnValue(0.5);
    vi.spyOn(console, "error").mockImplementation(() => {});
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it.each([401, 403, 404])("stops retrying HTTP %s and exposes the error", async (status) => {
    const streamRunEvents = vi.fn().mockRejectedValue(new ApiError(status, "Cannot access run"));
    const api = { streamRunEvents } as unknown as ResearchApi;
    const onCompleted = vi.fn();
    const { result } = renderHook(() => useRunEvents(api, "run-1", onCompleted));
    await advance(60_000);
    expect(streamRunEvents).toHaveBeenCalledTimes(1);
    expect(result.current.error).toBe("Cannot access run");
    expect(onCompleted).not.toHaveBeenCalled();
  });

  it("applies the same permanent-error stop to the polling fallback", async () => {
    const getRunEvents = vi.fn().mockRejectedValue(new ApiError(404, "Run not found"));
    const api = { getRunEvents } as unknown as ResearchApi;
    const onCompleted = vi.fn();
    const { result } = renderHook(() => useRunEvents(api, "run-1", onCompleted));
    await advance(60_000);
    expect(getRunEvents).toHaveBeenCalledTimes(1);
    expect(result.current.error).toBe("Run not found");
    expect(onCompleted).not.toHaveBeenCalled();
  });

  it("waits for server Retry-After before resuming from the latest unique cursor", async () => {
    const streamRunEvents = vi.fn(async (_id: string, _after: string | undefined,
      onEvent: (event: RunEvent) => void) => {
      if (streamRunEvents.mock.calls.length === 1) {
        onEvent(delta("1", "old"));
        onEvent(delta("2", "new"));
        onEvent(delta("1", "old"));
        throw new ApiError(429, "Slow down", "42");
      }
      onEvent({ id: "3", run_id: "run-1", type: "run.completed", data: { status: "completed" } });
    });
    const api = { streamRunEvents } as unknown as ResearchApi;
    const onCompleted = vi.fn();
    const { result } = renderHook(() => useRunEvents(api, "run-1", onCompleted));
    await advance(41_999);
    expect(streamRunEvents).toHaveBeenCalledTimes(1);
    expect(result.current.text).toBe("oldnew");
    expect(result.current.cursor).toBe("2");
    await advance(1);
    expect(streamRunEvents).toHaveBeenCalledTimes(2);
    expect(streamRunEvents.mock.calls[1][1]).toBe("2");
    expect(result.current.status).toBe("completed");
    expect(onCompleted).toHaveBeenCalledTimes(1);
  });

  it.each([new ApiError(503, "Unavailable"), new TypeError("Failed to fetch")])(
    "backs off transient failures, then stops after a bounded number of retries", async (error) => {
      const streamRunEvents = vi.fn().mockRejectedValue(error);
      const api = { streamRunEvents } as unknown as ResearchApi;
      const onCompleted = vi.fn();
      const { result } = renderHook(() => useRunEvents(api, "run-1", onCompleted));
      await advance(999);
      expect(streamRunEvents).toHaveBeenCalledTimes(1);
      await advance(1);
      expect(streamRunEvents).toHaveBeenCalledTimes(2);
      await advance(1_999);
      expect(streamRunEvents).toHaveBeenCalledTimes(2);
      await advance(1);
      expect(streamRunEvents).toHaveBeenCalledTimes(3);
      await advance(180_000);
      expect(streamRunEvents).toHaveBeenCalledTimes(7);
      expect(result.current.error).toBe(error.message);
      expect(onCompleted).not.toHaveBeenCalled();
      await advance(60_000);
      expect(streamRunEvents).toHaveBeenCalledTimes(7);
    },
  );

  it("reconnects a clean stream EOF with backoff and retains the cursor", async () => {
    const streamRunEvents = vi.fn(async (_id: string, _after: string | undefined, onEvent: (event: RunEvent) => void) => {
      if (streamRunEvents.mock.calls.length === 1) onEvent(delta("1", "live"));
      else await new Promise(() => {});
    });
    const api = { streamRunEvents } as unknown as ResearchApi;
    const onCompleted = vi.fn();
    renderHook(() => useRunEvents(api, "run-1", onCompleted));
    await advance(999);
    expect(streamRunEvents).toHaveBeenCalledTimes(1);
    await advance(1);
    expect(streamRunEvents.mock.calls[1][1]).toBe("1");
  });

  it.each(["EOF", "network error"])("keeps observing a waiting run through healthy heartbeat-only streams ending in %s", async (ending) => {
    const waiting: RunEvent = { id: "1", run_id: "run-1", type: "task.updated", data: {
      job_id: "job-1", status: "running", label: "Background analysis", progress: 50,
    } };
    const outputs: RunEvent[] = [
      { id: "2", run_id: "run-1", type: "tool.finished", data: {
        tool_call_id: "call-1", tool: "read_file", status: "completed", summary: "Read file",
      } },
      { id: "3", run_id: "run-1", type: "artifact.created", data: {
        artifact_id: "artifact-1", name: "report.md", kind: "document",
      } },
      { id: "4", run_id: "run-1", type: "run.completed", data: { status: "completed" } },
    ];
    const encoder = new TextEncoder();
    const fetcher = vi.fn(async (_input: RequestInfo | URL) => {
      void _input;
      const attempt = fetcher.mock.calls.length;
      let heartbeat: ReturnType<typeof setInterval>;
      let disconnect: ReturnType<typeof setTimeout>;
      return new Response(new ReadableStream<Uint8Array>({ start(controller) {
        if (attempt === 9) {
          controller.enqueue(encoder.encode(outputs.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("")));
          controller.close();
          return;
        }
        if (attempt === 1) controller.enqueue(encoder.encode(`data: ${JSON.stringify(waiting)}\n\n`));
        heartbeat = setInterval(() => controller.enqueue(encoder.encode(": heartbeat\n\n")), 15_000);
        disconnect = setTimeout(() => {
          clearInterval(heartbeat);
          if (ending === "EOF") controller.close();
          else controller.error(new TypeError("Connection interrupted"));
        }, 45_000);
      }, cancel() {
        clearInterval(heartbeat);
        clearTimeout(disconnect);
      } }));
    });
    const api = createHttpApi({ token: () => null, fetcher });
    const onCompleted = vi.fn();
    const { result } = renderHook(() => useRunEvents(api, "run-1", onCompleted));
    for (let attempt = 1; attempt <= 8; attempt++) {
      await advance(45_000);
      expect(result.current.status).toBe("waiting");
      expect(result.current.error).toBeUndefined();
      expect(fetcher).toHaveBeenCalledTimes(attempt);
      await advance(999);
      expect(fetcher).toHaveBeenCalledTimes(attempt);
      await advance(1);
      expect(fetcher).toHaveBeenCalledTimes(attempt + 1);
    }
    expect(fetcher.mock.calls.slice(1).map(([input]) => input))
      .toEqual(Array(8).fill("/api/v1/runs/run-1/events?after=1"));
    expect(result.current.status).toBe("completed");
    expect(result.current.cursor).toBe("4");
    expect(result.current.artifacts).toEqual([{ id: "artifact-1", name: "report.md", kind: "document" }]);
    expect(result.current.tools).toEqual([{ id: "call-1", name: "read_file", status: "completed", summary: "Read file" }]);
    expect(onCompleted).toHaveBeenCalledTimes(1);
  });

  it("keeps delayed HTTP errors inside the bounded failure budget", async () => {
    const streamRunEvents = vi.fn(async () => {
      await new Promise((resolve) => setTimeout(resolve, 45_000));
      throw new ApiError(503, "Service unavailable");
    });
    const api = { streamRunEvents } as unknown as ResearchApi;
    const onCompleted = vi.fn();
    const { result } = renderHook(() => useRunEvents(api, "run-1", onCompleted));
    for (const delay of [1_000, 2_000, 4_000, 8_000, 16_000, 30_000]) {
      await advance(45_000);
      await advance(delay);
    }
    await advance(45_000);
    expect(streamRunEvents).toHaveBeenCalledTimes(7);
    expect(result.current.error).toBe("Service unavailable");
    await advance(120_000);
    expect(streamRunEvents).toHaveBeenCalledTimes(7);
    expect(onCompleted).not.toHaveBeenCalled();
  });

  it("bounds delayed pre-response transport failures through the actual HTTP adapter", async () => {
    const fetcher = vi.fn(async () => {
      await new Promise((resolve) => setTimeout(resolve, 45_000));
      throw new TypeError("Unable to connect");
    });
    const api = createHttpApi({ token: () => null, fetcher });
    const onCompleted = vi.fn();
    const { result } = renderHook(() => useRunEvents(api, "run-1", onCompleted));
    for (const delay of [1_000, 2_000, 4_000, 8_000, 16_000, 30_000]) {
      await advance(45_000);
      await advance(delay);
    }
    await advance(45_000);
    expect(fetcher).toHaveBeenCalledTimes(7);
    expect(result.current.error).toBe("Unable to connect");
    await advance(120_000);
    expect(fetcher).toHaveBeenCalledTimes(7);
    expect(onCompleted).not.toHaveBeenCalled();
  });

  it.each(["EOF", "network error"])("does not count slow response headers as healthy time before a short stream %s", async (ending) => {
    const fetcher = vi.fn(async () => {
      await new Promise((resolve) => setTimeout(resolve, 45_000));
      let disconnect: ReturnType<typeof setTimeout>;
      return new Response(new ReadableStream<Uint8Array>({ start(controller) {
        controller.enqueue(new TextEncoder().encode(": heartbeat\n\n"));
        disconnect = setTimeout(() => {
          if (ending === "EOF") controller.close();
          else controller.error(new TypeError("Short connection interrupted"));
        }, 1_000);
      }, cancel() { clearTimeout(disconnect); } }));
    });
    const api = createHttpApi({ token: () => null, fetcher });
    const onCompleted = vi.fn();
    const { result } = renderHook(() => useRunEvents(api, "run-1", onCompleted));
    for (const delay of [1_000, 2_000, 4_000, 8_000, 16_000, 30_000]) {
      await advance(46_000);
      await advance(delay);
    }
    await advance(46_000);
    expect(fetcher).toHaveBeenCalledTimes(7);
    expect(result.current.error).toBe(ending === "EOF" ? "Event stream closed before the run ended" : "Short connection interrupted");
    await advance(120_000);
    expect(fetcher).toHaveBeenCalledTimes(7);
    expect(onCompleted).not.toHaveBeenCalled();
  });

  it("cancels scheduled retries and suppresses late events after unmount", async () => {
    let accept!: (event: RunEvent) => void;
    let signal!: AbortSignal;
    const streamRunEvents = vi.fn(async (_id, _after, onEvent, streamSignal) => {
      accept = onEvent;
      signal = streamSignal;
      throw new TypeError("Failed to fetch");
    });
    const api = { streamRunEvents } as unknown as ResearchApi;
    const onCompleted = vi.fn();
    const { unmount } = renderHook(() => useRunEvents(api, "run-1", onCompleted));
    await advance(0);
    unmount();
    accept({ id: "1", run_id: "run-1", type: "run.completed", data: { status: "completed" } });
    await advance(60_000);
    expect(signal.aborted).toBe(true);
    expect(streamRunEvents).toHaveBeenCalledTimes(1);
    expect(onCompleted).not.toHaveBeenCalled();
  });

  it("treats abort as a quiet stop", async () => {
    const streamRunEvents = vi.fn().mockRejectedValue(new DOMException("Cancelled", "AbortError"));
    const api = { streamRunEvents } as unknown as ResearchApi;
    const onCompleted = vi.fn();
    renderHook(() => useRunEvents(api, "run-1", onCompleted));
    await advance(60_000);
    expect(streamRunEvents).toHaveBeenCalledTimes(1);
    expect(console.error).not.toHaveBeenCalled();
    expect(onCompleted).not.toHaveBeenCalled();
  });

  it.each(["run.completed", "run.failed", "run.cancelled"] as const)("quietly stops once on %s despite an abort rejection", async (type) => {
    const streamRunEvents = vi.fn(async (_id, _after, onEvent: (event: RunEvent) => void) => {
      const event: RunEvent = type === "run.failed"
        ? { id: "1", run_id: "run-1", type, data: { code: "PI_RUN_FAILED", message: "Failed" } }
        : { id: "1", run_id: "run-1", type, data: { status: type === "run.completed" ? "completed" : "cancelled" } } as RunEvent;
      onEvent(event);
      onEvent(event);
      throw new DOMException("Cancelled", "AbortError");
    });
    const api = { streamRunEvents } as unknown as ResearchApi;
    const onCompleted = vi.fn();
    renderHook(() => useRunEvents(api, "run-1", onCompleted));
    await advance(60_000);
    expect(onCompleted).toHaveBeenCalledTimes(1);
    expect(streamRunEvents).toHaveBeenCalledTimes(1);
    expect(console.error).not.toHaveBeenCalled();
  });
});

async function advance(ms: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

function delta(id: string, text: string): RunEvent {
  return { id, run_id: "run-1", type: "message.delta", data: { delta: text } };
}

it("recognizes completion after raw event history reaches its retention bound", async () => {
  const events: RunEvent[] = Array.from({ length: 600 }, (_, index) => ({ id: String(index), run_id: "bounded", type: "message.delta", data: { delta: "x" } }));
  events.push({ id: "completed", run_id: "bounded", type: "run.completed", data: { status: "completed" } });
  const api = { getRunEvents: vi.fn().mockResolvedValue(events) } as unknown as ResearchApi;
  const onCompleted = vi.fn();
  const { result } = renderHook(() => useRunEvents(api, "bounded", onCompleted));
  await waitFor(() => expect(result.current.status).toBe("completed"));
  expect(result.current.events).toHaveLength(512); expect(result.current.text).toHaveLength(600);
  expect(onCompleted).toHaveBeenCalledTimes(1);
});
