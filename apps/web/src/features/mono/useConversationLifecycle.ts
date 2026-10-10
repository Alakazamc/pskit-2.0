import { useCallback, useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { ResearchApi, Session } from "../../api/types";
import { useRunEvents } from "../chat/useRunEvents";
import { useRunHistory } from "../chat/useRunHistory";

export function useConversationLifecycle(api: ResearchApi, userId: string, activeSessionId: string | null, activeProjectId: string | null,
  personalProjectId: string, currentSession: Session | undefined, currentRun: { sessionId: string; runId: string } | null, submitting: boolean) {
  const queryClient = useQueryClient();
  const previousTitleStatus = useRef({ sessionId: currentSession?.id, status: currentSession?.title_status });
  useEffect(() => {
    if (previousTitleStatus.current.sessionId === currentSession?.id && previousTitleStatus.current.status === "pending" && currentSession?.title_status !== "pending") {
      for (const resource of ["usage", "usage-entries", "usage-activity"]) {
        void queryClient.invalidateQueries({ queryKey: [resource, userId] });
      }
    }
    previousTitleStatus.current = { sessionId: currentSession?.id, status: currentSession?.title_status };
  }, [currentSession?.id, currentSession?.title_status, queryClient, userId]);
  const sessionRunId = currentSession && !["completed", "failed", "cancelled", "idle"].includes(currentSession.status ?? "") ? currentSession.latest_run_id ?? null : null;
  const visibleRunId = submitting ? null : currentRun?.sessionId === activeSessionId ? currentRun.runId : sessionRunId;
  const historyRunId = !submitting && currentRun?.sessionId !== activeSessionId &&
    (currentSession?.status === "failed" || currentSession?.status === "cancelled")
    ? currentSession.latest_run_id ?? null : null;
  const historyRun = useRunHistory(api, userId, historyRunId);
  const onRunCompleted = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["messages", userId, activeSessionId] });
    void queryClient.invalidateQueries({ queryKey: ["sessions", userId, activeProjectId ?? personalProjectId] });
    void queryClient.invalidateQueries({ queryKey: ["session", userId, activeSessionId] });
    void queryClient.invalidateQueries({ queryKey: ["usage", userId] });
    void queryClient.invalidateQueries({ queryKey: ["usage-entries", userId] });
    void queryClient.invalidateQueries({ queryKey: ["usage-activity", userId] });
    if (activeSessionId) void queryClient.invalidateQueries({ queryKey: ["session-artifacts", userId, activeSessionId] });
  }, [activeProjectId, activeSessionId, personalProjectId, queryClient, userId]);
  const liveRun = useRunEvents(api, visibleRunId, onRunCompleted);
  const run = historyRunId ? historyRun.view : liveRun;
  const runActive = (run.status === "running" || run.status === "waiting") ||
    (run.status === "idle" && !!visibleRunId && (
      (currentRun?.sessionId === activeSessionId && currentRun.runId === visibleRunId) ||
      currentSession?.status === "running" || currentSession?.status === "waiting"));
  const cancelRun = async () => {
    if (!visibleRunId) return;
    await api.cancelRun(visibleRunId);
    onRunCompleted();
  };
  const decideApproval = async (approvalId: string, decision: "approved" | "rejected") => {
    if (!visibleRunId) return;
    await api.decideApproval(visibleRunId, approvalId, decision);
    void queryClient.invalidateQueries({ queryKey: ["usage", userId] });
    void queryClient.invalidateQueries({ queryKey: ["usage-entries", userId] });
  };
  return { historyRunId, historyRun, visibleRunId, run, runActive, cancelRun, decideApproval };
}
