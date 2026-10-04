import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, Clock3 } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import type { ResearchApi } from "../../api/types";
import { useLanguage } from "../../i18n/LanguageProvider";
import { coralCapabilityId } from "./coralResult";

export function CoralHistory({ api, userId }: { api: ResearchApi; userId: string }) {
  const { t, language } = useLanguage();
  const [, setParams] = useSearchParams();
  const history = useQuery({ queryKey: ["compute-history", userId, coralCapabilityId],
    queryFn: () => api.getComputeJobs(coralCapabilityId) });
  if (history.isLoading) return <p role="status" className="coral-note">{t("tools.loadingRuns")}</p>;
  if (history.isError) return <p role="alert" className="mono-form-error">{t("tools.readFailed")}</p>;
  if (!history.data?.length) return <div className="coral-history-empty"><Clock3 size={22} /><p>{t("tools.noRuns")}</p></div>;
  return <div className="coral-history">{history.data.map((job) => <button type="button" key={job.id} onClick={() => setParams((previous) => {
    const next = new URLSearchParams(previous); next.delete("tab"); next.set("job", job.id); return next;
  }, { replace: true })}>
    <span><strong>{String(job.arguments.pdb_id ?? "CORAL")} · {String(job.arguments.chain ?? "")}</strong>
      <small>{new Date(job.created_at).toLocaleString(language)} · {job.id.slice(-8)}</small></span>
    <span className="coral-history-status">{t(job.status === "completed" ? "coral.completed" : job.status === "queued" ? "coral.queued" : job.status === "running" ? "coral.running" : job.status === "cancelling" ? "coral.cancelling" : job.status === "cancelled" ? "coral.cancelled" : "coral.failed")}<ArrowUpRight size={14} /></span>
  </button>)}</div>;
}
