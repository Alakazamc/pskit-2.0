import { useState } from "react";
import type { AdminJob } from "../../api/admin";
import { useLanguage } from "../../i18n/LanguageProvider";
import { AdminListFrame, AdminTable, StateLabel } from "./AdminControls";
import { AdminOperationEditor } from "./AdminOperationEditor";
import { useAdmin, useAdminList } from "./adminQueries";

export function AdminJobsPage() {
  const { api, me } = useAdmin();
  const { t } = useLanguage();
  const query = useAdminList("jobs", "jobs:read", api.listAdminJobs, true);
  const rows = query.data?.pages.flatMap((page) => page.items) ?? [];
  const [selected, setSelected] = useState<AdminJob | null>(null);
  const current = rows.find((row) => row.job_id === selected?.job_id) ?? selected;
  const canCancel = (row: AdminJob) => me.permissions.includes("jobs:cancel") && ["queued", "running"].includes(row.status) && row.cancellation_state === "none";
  return <div className="admin-split"><AdminListFrame loading={query.isPending} error={query.error} empty={!rows.length} hasMore={query.hasNextPage} loadingMore={query.isFetchingNextPage} refresh={() => query.refetch()} more={() => query.fetchNextPage()}><AdminTable rows={rows} rowKey={(row) => row.job_id} columns={[{ label: t("admin.job"), render: (row) => <>{row.job_id}<small>{row.user_id} · {row.service_id ?? "—"}</small></> }, { label: t("admin.status"), render: (row) => <><StateLabel state={row.status} /><small>{row.progress}%</small></> }, { label: t("admin.accounting"), render: (row) => <StateLabel state={row.accounting_status} /> }, { label: t("admin.cancellation"), render: (row) => row.cancellation_state === "none" ? "—" : <StateLabel state={row.cancellation_state} /> }, { label: t("admin.actions"), render: (row) => <button type="button" aria-label={`${t(canCancel(row) ? "admin.cancel" : "admin.view")} ${row.job_id}`} onClick={() => setSelected(row)}>{t(canCancel(row) ? "admin.cancel" : "admin.view")}</button> }]} /></AdminListFrame>{selected && current && <AdminOperationEditor key={selected.job_id} id={selected.job_id} revision={selected.revision} confirmed={current.cancellation_state === "confirmed"} canAct={canCancel(current)} label="admin.requestCancel" hint="admin.cancelHint" action={(revision) => api.cancelAdminJob(selected.job_id, revision)}><dl className="admin-detail"><dt>{t("admin.user")}</dt><dd>{current.user_id}</dd><dt>{t("admin.capability")}</dt><dd>{current.capability_id ?? "—"}</dd><dt>{t("admin.status")}</dt><dd><StateLabel state={current.status} /></dd><dt>{t("admin.accounting")}</dt><dd><StateLabel state={current.accounting_status} /></dd></dl></AdminOperationEditor>}</div>;
}
