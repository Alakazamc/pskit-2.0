import * as Tabs from "@radix-ui/react-tabs";
import { Activity, Check, Circle, Cpu, FileText, LoaderCircle, Sparkles } from "lucide-react";
import type { ArtifactRef, RunEvent, UsageSnapshot } from "../../api/types";
import type { RunView } from "./events";
import { useLanguage } from "../../i18n/LanguageProvider";
import { localizedRunError } from "./runErrors";

type TaskEvent = Extract<RunEvent, { type: "task.updated" }>;

export function AgentPanel({ run, usage, savedArtifacts, onCancel, onApproval }: {
  run: RunView;
  usage?: UsageSnapshot;
  savedArtifacts: ArtifactRef[];
  onCancel?: () => void;
  onApproval?: (approvalId: string, decision: "approved" | "rejected") => void;
}) {
  const { t } = useLanguage();
  const artifacts = [...savedArtifacts, ...run.artifacts].filter(
    (item, index, all) => all.findIndex((other) => other.id === item.id) === index,
  );
  const taskEvents = run.events.filter((event): event is TaskEvent => event.type === "task.updated");
  const tasks = [...new Map(taskEvents.map((event) => [event.data.job_id, event])).values()];
  const runError = localizedRunError(run, t);

  return <aside className="agent-panel">
    <div className="agent-panel-title"><div className="agent-title-icon"><Sparkles size={17} /></div><div><b>{t("agent.title")}</b><span>{t("agent.subtitle")}</span></div></div>
    <Tabs.Root defaultValue="activity" className="agent-tabs">
      <Tabs.List className="tab-list"><Tabs.Trigger value="activity">{t("agent.activity")}</Tabs.Trigger><Tabs.Trigger value="plan">{t("agent.plan")}</Tabs.Trigger><Tabs.Trigger value="artifacts">{t("agent.artifacts")}</Tabs.Trigger><Tabs.Trigger value="compute">{t("agent.compute")}</Tabs.Trigger></Tabs.List>
      <Tabs.Content value="activity" className="tab-content">
        <div className="panel-section-title">{t("agent.currentRun")} <span>{t(run.status === "idle" ? "agent.idle" : run.status === "completed" ? "agent.completed" : run.status === "failed" ? "agent.failed" : run.status === "cancelled" ? "agent.cancelled" : "agent.running")}</span></div>
        {onCancel && ["running", "waiting"].includes(run.status) && <button type="button" onClick={onCancel}>{t("agent.cancel")}</button>}
        {run.approval && onApproval && <div className="agent-approval-card"><b>{t("agent.approvalRequired", { minutes: run.approval.estimatedMinutes })}</b><div><button type="button" onClick={() => onApproval(run.approval!.id, "approved")}>{t("agent.approve")}</button><button type="button" onClick={() => onApproval(run.approval!.id, "rejected")}>{t("agent.reject")}</button></div></div>}
        {run.retry && run.status === "running" && <div className="agent-retry-status" role="status">{t("agent.retrying", { attempt: run.retry.attempt, maximum: run.retry.maxAttempts })}</div>}
        {tasks.length ? <div className="activity-timeline">{tasks.map((event) => <div className="activity-step" key={event.data.job_id}>
          <div className={`step-icon ${event.data.status === "completed" ? "complete" : "active"}`}>
            {event.data.status === "completed" ? <Check size={14} /> : <LoaderCircle size={14} />}
          </div>
          <div><b>{event.data.label || t("conversation.backgroundTask")}</b><small>{t(event.data.status === "completed" ? "agent.completed" : event.data.status === "failed" ? "agent.failed" : event.data.status === "cancelled" ? "agent.cancelled" : "agent.progress", { progress: event.data.progress })}</small></div>
        </div>)}</div> : <div className="panel-empty"><Activity size={28} /><b>{t(run.status === "idle" ? "agent.noRun" : run.status === "completed" ? "agent.turnCompleted" : run.status === "failed" ? "agent.runFailed" : "agent.processing")}</b><p>{runError ?? t(run.status === "idle" ? "agent.sendToSeeEvents" : "agent.waitingEvents")}</p></div>}
      </Tabs.Content>
      <Tabs.Content value="plan" className="tab-content">
        <div className="panel-section-title">{t("agent.plan")} <span>{run.plan.length}</span></div>
        {run.plan.length ? <ol className="agent-plan-list">{run.plan.map((step) => <li key={step.id}>
          <span className={`agent-plan-icon ${step.status}`}>{step.status === "completed" ? <Check size={13} /> : step.status === "in_progress" ? <LoaderCircle size={13} /> : <Circle size={13} />}</span>
          <span><b>{step.title}</b><small>{t(step.status === "completed" ? "agent.planCompleted" : step.status === "in_progress" ? "agent.planInProgress" : step.status === "blocked" ? "agent.planBlocked" : "agent.planPending")}</small></span>
        </li>)}</ol> : <div className="panel-empty"><Activity size={28} /><b>{t("agent.noPlan")}</b><p>{t("agent.noPlanDescription")}</p></div>}
      </Tabs.Content>
      <Tabs.Content value="artifacts" className="tab-content"><div className="panel-section-title">{t("agent.researchArtifacts")} <span>{artifacts.length}</span></div>{artifacts.length ? artifacts.map((artifact) => <div className="artifact-card" key={artifact.id}><FileText size={20} /><div><b>{artifact.name}</b><span>{artifact.kind}</span></div></div>) : <div className="panel-empty"><FileText size={28} /><b>{t("agent.noArtifacts")}</b><p>{t("agent.artifactsDescription")}</p></div>}</Tabs.Content>
      <Tabs.Content value="compute" className="tab-content"><div className="panel-section-title">{t("agent.computeUsage")}</div>{usage ? <><div className="compute-stats"><span>{t("agent.gpuRemaining")}</span><b>{usage.gpu.remaining} {t("usage.minutes")}</b></div><div className="compute-stats"><span>{t("agent.reserved")}</span><b>{usage.gpu.reserved} {t("usage.minutes")}</b></div></> : <div className="panel-empty"><Cpu size={28} /><b>{t("agent.loadingQuota")}</b></div>}</Tabs.Content>
    </Tabs.Root>
  </aside>;
}
