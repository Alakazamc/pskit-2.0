import { Check, Loader2, X } from "lucide-react";
import type { ComputeJob } from "../../api/types";
import { useLanguage } from "../../i18n/LanguageProvider";

export function CoralProgress({ job, submitting }: { job?: ComputeJob; submitting: boolean }) {
  const { t } = useLanguage();
  const done = job?.status === "completed" && job.report?.status === "completed";
  const failed = job?.status === "failed";
  const generation = done ? "done" : failed ? "error" : job?.status === "running" ? "active" : "pending";
  const states = [job ? "done" : submitting ? "active" : "pending", generation, done ? "done" : "pending"];
  const labels = [t("coral.stepInput"), t("coral.stepGenerate"), t("coral.stepResult")];
  const status = submitting ? t("workspace.loadingCatalog") : t(
    !job ? "coral.idle" : job.status === "queued" ? "coral.queued" : job.status === "running" ? "coral.running" :
    job.status === "cancelling" ? "coral.cancelling" : done ? "coral.completed" : job.status === "cancelled" ? "coral.cancelled" : "coral.failed",
  );
  return <section className="coral-progress" aria-label={t("coral.progress")}>
    <ol className="coral-steps">{labels.map((label, index) => <li key={label} data-state={states[index]} aria-current={states[index] === "active" ? "step" : undefined}>
      <span className="coral-node" aria-hidden="true">{states[index] === "done" ? <Check size={14} /> : states[index] === "active" ? <Loader2 size={14} className="coral-spinner" /> : states[index] === "error" ? <X size={14} /> : index + 1}</span>
      <span>{label}</span>
      <span className="sr-only">{states[index] === "done" ? t("coral.completed") : states[index] === "active" ? t("coral.running") : states[index] === "error" ? t("coral.failed") : t("coral.idle")}</span>
    </li>)}</ol>
    <div className="coral-progress-status" role="status"><span>{status}</span>{job?.status === "running" && <span>{Math.min(100, job.progress ?? 0)}%</span>}</div>
  </section>;
}
