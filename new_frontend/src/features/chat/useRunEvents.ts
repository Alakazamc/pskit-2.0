import { useEffect, useState } from "react";
import type { ResearchApi } from "../../api/types";
import { emptyRun, projectEvents, type RunView } from "./events";

export function useRunEvents(api: ResearchApi, runId: string | null, onCompleted: () => void): RunView {
  const [state, setState] = useState<{ runId: string | null; view: RunView }>({ runId: null, view: emptyRun });
  useEffect(() => {
    setState({ runId, view: emptyRun });
    if (!runId) return;
    let cancelled = false;
    let cursor: string | undefined;
    let terminal = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    const accept = (events: Awaited<ReturnType<ResearchApi["getRunEvents"]>>) => {
      if (cancelled || events.length === 0) return;
      cursor = events.at(-1)?.id;
      setState((previous) => ({ runId, view: projectEvents(previous.runId === runId ? previous.view : emptyRun, events) }));
      for (const event of events) {
        if (event.type === "run.completed" || event.type === "run.failed" || event.type === "run.cancelled") {
          terminal = true;
        }
      }
      if (terminal) {
        controller.abort();
        onCompleted();
      }
    };
    const tick = async () => {
      try {
        if (api.streamRunEvents) {
          await api.streamRunEvents(runId, cursor, (event) => accept([event]), controller.signal);
        } else {
          accept(await api.getRunEvents(runId, cursor));
        }
      } catch (error) {
        if (cancelled) return;
        // Log errors for debugging but don't break the loop
        console.error("Run events fetch error:", error);
      }
      if (cancelled || terminal) return;
      // Use shorter interval for active streams (200ms), longer for completed (1200ms)
      const pollInterval = terminal ? 1200 : 200;
      timer = setTimeout(tick, pollInterval);
    };
    void tick();
    return () => { cancelled = true; controller.abort(); clearTimeout(timer); };
  }, [api, runId, onCompleted]);
  return state.runId === runId ? state.view : emptyRun;
}
