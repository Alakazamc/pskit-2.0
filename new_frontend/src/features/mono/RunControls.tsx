import { useState } from "react";
import type { RunView } from "../chat/events";
import { errorTranslationKey } from "../../i18n/errors";
import { useLanguage } from "../../i18n/LanguageProvider";

type Decision = "approved" | "rejected";

export function RunControls({ run, onCancel, onApproval }: {
  run: RunView;
  onCancel: () => Promise<void>;
  onApproval: (approvalId: string, decision: Decision) => Promise<void>;
}) {
  const { t } = useLanguage();
  const [busy, setBusy] = useState(false);
  const [cancelRequested, setCancelRequested] = useState(false);
  const [handledApproval, setHandledApproval] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if ((run.status !== "running" && run.status !== "waiting") || !run.approval) return null;

  const cancel = async () => {
    setBusy(true);
    setError(null);
    try {
      await onCancel();
      setCancelRequested(true);
    } catch (caught) {
      const key = errorTranslationKey(caught);
      setError(key ? t(key) : t("workspace.cancelFailed"));
    } finally {
      setBusy(false);
    }
  };

  const decide = async (approvalId: string, decision: Decision) => {
    setBusy(true);
    setError(null);
    try {
      await onApproval(approvalId, decision);
      setHandledApproval(approvalId);
    } catch (caught) {
      const key = errorTranslationKey(caught);
      setError(key ? t(key) : t("workspace.approvalFailed"));
    } finally {
      setBusy(false);
    }
  };

  const approval = run.approval;
  const decisionDisabled = busy || cancelRequested || handledApproval === approval?.id;
  return <section className="mono-run-controls" aria-label={t("agent.currentRun")}>
    <div className="mono-run-controls-main">
      <div className="mono-run-controls-description">
        <strong>{approval ? t("agent.approvalRequired", { minutes: approval.estimatedMinutes }) : t("agent.running")}</strong>
        {run.jobLabel && !approval && <span>{run.jobLabel} · {t("agent.progress", { progress: run.progress })}</span>}
      </div>
      <div className="mono-run-controls-actions">
        {approval && <>
          <button type="button" className="mono-run-approve" disabled={decisionDisabled} onClick={() => void decide(approval.id, "approved")}>{t("agent.approve")}</button>
          <button type="button" disabled={decisionDisabled} onClick={() => void decide(approval.id, "rejected")}>{t("agent.reject")}</button>
        </>}
        <button type="button" disabled={busy || cancelRequested} onClick={() => void cancel()}>{t(cancelRequested ? "agent.cancelled" : "agent.cancel")}</button>
      </div>
    </div>
    {error && <p role="alert" className="mono-run-controls-error">{error}</p>}
  </section>;
}
