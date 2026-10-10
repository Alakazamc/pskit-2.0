import { useState } from "react";
import type { AdminJob } from "../../api/admin";
import { useLanguage } from "../../i18n/LanguageProvider";
import { MutationFeedback, ReasonField, StateLabel } from "./AdminControls";
import { useAdmin, useAdminMutation } from "./adminQueries";

export function LegacyAf3UsageEditor({ row, updated }: { row: AdminJob; updated: (row: AdminJob) => void }) {
  const { api, me } = useAdmin();
  const { t } = useLanguage();
  const [minutes, setMinutes] = useState("");
  const [evidence, setEvidence] = useState("");
  const [stopped, setStopped] = useState(false);
  const [reason, setReason] = useState("");
  const canWrite = me.permissions.includes("usage:reconcile") && row.accounting_status === "pending_reconciliation";
  const actual = Number(minutes);
  const valid = minutes !== "" && Number.isSafeInteger(actual) && actual >= 0 && actual <= 1440 && stopped && reason.trim().length >= 5 && evidence.trim().length >= 5;
  const save = useAdminMutation(() => api.reconcileAdminAf3Job(row.job_id, {
    expected_revision: row.revision,
    reason,
    evidence,
    stopped: true,
    actual_gpu_minutes: actual,
    source: "legacy_wall",
  }), updated);
  return <section className="admin-editor">
    <h2>{row.job_id}</h2>
    <p className="admin-meta">{t("admin.revision", { revision: row.revision })} · <StateLabel state={row.accounting_status} /></p>
    <p className="admin-hint">{t("admin.legacyAf3Hint")}</p>
    <form className="admin-form" onSubmit={(event) => { event.preventDefault(); if (canWrite && valid) save.mutate(undefined); }}>
      <fieldset disabled={!canWrite || save.isPending}>
        <label>{t("admin.legacyAf3Minutes")}<input type="number" required min={0} max={1440} step={1} value={minutes} onChange={(event) => setMinutes(event.target.value)} /></label>
        <label>{t("admin.evidence")}<textarea required minLength={5} maxLength={2000} value={evidence} onChange={(event) => setEvidence(event.target.value)} /></label>
        <label className="admin-check"><input required type="checkbox" checked={stopped} onChange={(event) => setStopped(event.target.checked)} />{t("admin.stoppedVerified")}</label>
      </fieldset>
      {canWrite && <ReasonField value={reason} onChange={setReason} disabled={save.isPending} />}
      <MutationFeedback error={save.error} success={save.isSuccess} />
      {canWrite && <button type="submit" disabled={!valid || save.isPending}>{t("admin.submitReconcile")}</button>}
    </form>
  </section>;
}
