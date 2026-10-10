import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import type { ResearchApi } from "../../api/types";
import { emptyRun, projectEvents } from "./events";

// Failed and cancelled runs can contain replies that were not saved as messages.
export function useRunHistory(api: ResearchApi, userId: string, runId: string | null) {
  const query = useQuery({
    queryKey: ["run-history", userId, runId],
    queryFn: () => api.getRunEvents(runId!),
    enabled: !!runId,
    staleTime: Infinity,
  });
  const view = useMemo(() => query.data ? projectEvents(emptyRun, query.data) : emptyRun, [query.data]);
  return { query, view };
}
