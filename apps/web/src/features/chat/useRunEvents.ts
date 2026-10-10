import { useEffect, useState } from "react";
import type { ResearchApi } from "../../api/types";
import { SseTransportError } from "../../api/http";
import { emptyRun, projectEvents, type RunView } from "./events";
import { getReconnectDecision, isAbortError } from "./reconnectPolicy";

export function useRunEvents(api: ResearchApi, runId: string | null, onCompleted: () => void): RunView {
  const [state, setState] = useState<{ runId: string | null; view: RunView }>({ runId: null, view: emptyRun });
  useEffect(() => {
    setState({ runId, view: emptyRun });
    if (!runId) return;
    let cancelled = false;
    let cursor: string | undefined;
    let terminal = false;
    let pollCount = 0;
    let failures = 0;
    let view = emptyRun;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const controller = new AbortController();
    const accept = (events: Awaited<ReturnType<ResearchApi["getRunEvents"]>>) => {
      if (cancelled || terminal || events.length === 0) return;
      const next = projectEvents(view, events);
      if (next === view) return;
      view = next;
      cursor = view.cursor;
      failures = 0;
      setState({ runId, view });
      terminal = Boolean(next.ended);
      if (terminal) {
        controller.abort();
        onCompleted();
      }
    };
    const tick = async () => {
      let delayMs: number;
      let streaming = false;
      try {
        if (api.streamRunEvents) {
          streaming = true;
          await api.streamRunEvents(runId, cursor, (event) => accept([event]), controller.signal);
          if (cancelled || terminal) return;
          throw new Error("Event stream closed before the run ended");
        } else {
          accept(await api.getRunEvents(runId, cursor));
          failures = 0;
          pollCount++;
          delayMs = pollCount <= 3 ? 50 : 200;
        }
      } catch (error) {
        if (cancelled || terminal || controller.signal.aborted) return;
        // Only the established reader can confirm a long, responsive connection.
        if (streaming && error instanceof SseTransportError && error.healthy) failures = 0;
        const decision = getReconnectDecision(error, failures);
        if (isAbortError(error)) return;
        console.error("Run events fetch error:", error);
        if (decision.kind === "stop") {
          view = { ...view, error: error instanceof Error ? error.message : "Unable to receive run events" };
          setState({ runId, view });
          return;
        }
        failures++;
        delayMs = decision.delayMs;
      }
      if (cancelled || terminal) return;
      timer = setTimeout(tick, delayMs);
    };
    void tick();
    return () => { cancelled = true; controller.abort(); clearTimeout(timer); };
  }, [api, runId, onCompleted]);
  return state.runId === runId ? state.view : emptyRun;
}
