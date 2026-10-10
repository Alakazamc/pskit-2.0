import { useState } from "react";
import type { AdminJob, ReconcileRequest } from "../../api/admin";
import type { UsageReport } from "../../api/generated-compute";
import { useLanguage } from "../../i18n/LanguageProvider";
import { AdminListFrame, AdminTable, MutationFeedback, ReasonField, StateLabel } from "./AdminControls";
import { useAdmin, useAdminList, useAdminMutation } from "./adminQueries";
import { LegacyAf3UsageEditor } from "./LegacyAf3UsageEditor";

const metrics = ["wall_ms", "cpu_core_ms", "gpu_device_ms", "peak_memory_bytes", "peak_gpu_memory_bytes", "gpu_count"] as const;

function UsageEditor({ row, updated }: { row: AdminJob; updated: (row: AdminJob) => void }) {
  const { api, me } = useAdmin();
  const { t } = useLanguage();
  const [values, setValues] = useState<Record<(typeof metrics)[number], string>>({ wall_ms: "", cpu_core_ms: "", gpu_device_ms: "", peak_memory_bytes: "", peak_gpu_memory_bytes: "", gpu_count: "" });
  const [source, setSource] = useState<UsageReport["source"]>("unknown");
  const [terminal, setTerminal] = useState<ReconcileRequest["terminal_status"]>("completed");
  const [evidence, setEvidence] = useState("");
  const [stopped, setStopped] = useState(false);
  const [reason, setReason] = useState("");
  const canWrite = me.permissions.includes("usage:reconcile") && row.accounting_status === "pending_reconciliation";
  const valid = stopped && reason.trim().length >= 5 && evidence.trim().length >= 5 && metrics.every((metric) => values[metric] === "" || (Number.isSafeInteger(Number(values[metric])) && Number(values[metric]) >= 0));
  const save = useAdminMutation(() => {
    const usage = { source } as UsageReport;
    for (const metric of metrics) usage[metric] = values[metric] === "" ? null : Number(values[metric]);
    return api.reconcileAdminJob(row.job_id, { expected_revision: row.revision, reason, evidence, stopped: true, terminal_status: terminal, usage });
  }, updated);
  return <section className="admin-editor"><h2>{row.job_id}</h2><p className="admin-meta">{t("admin.revision", { revision: row.revision })} · <StateLabel state={row.accounting_status} /></p><p className="admin-hint">{t("admin.unknownHint")}</p><form className="admin-form" onSubmit={(event) => { event.preventDefault(); if (canWrite && valid) save.mutate(undefined); }}><fieldset disabled={!canWrite || save.isPending}>{metrics.map((metric) => <label key={metric}>{t(`admin.metric.${metric}`)}<input type="number" min={0} step={1} placeholder={t("admin.state.unknown")} value={values[metric]} onChange={(event) => setValues({ ...values, [metric]: event.target.value })} /></label>)}<label>{t("admin.source")}<select value={source} onChange={(event) => setSource(event.target.value as UsageReport["source"])}>{(["service_reported", "measured", "estimated", "unknown"] as const).map((item) => <option key={item} value={item}>{t(`admin.source.${item}`)}</option>)}</select></label><label>{t("admin.terminal")}<select value={terminal} onChange={(event) => setTerminal(event.target.value as ReconcileRequest["terminal_status"])}><option value="completed">{t("admin.state.completed")}</option><option value="failed">{t("admin.state.failed")}</option><option value="cancelled">{t("admin.state.cancelled")}</option></select></label><label>{t("admin.evidence")}<textarea required minLength={5} maxLength={2000} value={evidence} onChange={(event) => setEvidence(event.target.value)} /></label><label className="admin-check"><input required type="checkbox" checked={stopped} onChange={(event) => setStopped(event.target.checked)} />{t("admin.stoppedVerified")}</label></fieldset>{canWrite && <ReasonField value={reason} onChange={setReason} disabled={save.isPending} />}<MutationFeedback error={save.error} success={save.isSuccess} />{canWrite && <button type="submit" disabled={!valid || save.isPending}>{t("admin.submitReconcile")}</button>}</form></section>;
}

export function AdminUsagePage() {
  const { api, me } = useAdmin();
  const { t } = useLanguage();
  const query = useAdminList("reconciliation", "usage:read", api.listReconciliation, true);
  const rows = query.data?.pages.flatMap((page) => page.items) ?? [];
  const [selected, setSelected] = useState<AdminJob | null>(null);
  const editable = me.permissions.includes("usage:reconcile");
  return <div className="admin-split"><AdminListFrame loading={query.isPending} error={query.error} empty={!rows.length} hasMore={query.hasNextPage} loadingMore={query.isFetchingNextPage} refresh={() => query.refetch()} more={() => query.fetchNextPage()}><AdminTable rows={rows} rowKey={(row) => row.job_id} columns={[{ label: t("admin.job"), render: (row) => <>{row.job_id}<small>{row.user_id}</small></> }, { label: t("admin.status"), render: (row) => <StateLabel state={row.status} /> }, { label: t("admin.accounting"), render: (row) => <StateLabel state={row.accounting_status} /> }, { label: t("admin.actions"), render: (row) => <button type="button" aria-label={`${t(editable ? "admin.reconcile" : "admin.view")} ${row.job_id}`} onClick={() => setSelected(row)}>{t(editable ? "admin.reconcile" : "admin.view")}</button> }]} /></AdminListFrame>{selected && (selected.service_id === null && selected.capability_id === "af3" ? <LegacyAf3UsageEditor key={selected.job_id} row={selected} updated={setSelected} /> : <UsageEditor key={selected.job_id} row={selected} updated={setSelected} />)}</div>;
}
