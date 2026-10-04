import { useState } from "react";
import type { SandboxSummary } from "../../api/admin";
import { useLanguage } from "../../i18n/LanguageProvider";
import { AdminListFrame, AdminTable, StateLabel } from "./AdminControls";
import { AdminOperationEditor } from "./AdminOperationEditor";
import { useAdmin, useAdminList } from "./adminQueries";

export function AdminSandboxesPage() {
  const { api, me } = useAdmin();
  const { t, language } = useLanguage();
  const query = useAdminList("sandboxes", "sandboxes:read", api.listAdminSandboxes, true);
  const rows = query.data?.pages.flatMap((page) => page.items) ?? [];
  const [selected, setSelected] = useState<SandboxSummary | null>(null);
  const current = rows.find((row) => row.owner_id === selected?.owner_id) ?? selected;
  const canDrain = (row: SandboxSummary) => me.permissions.includes("sandboxes:drain") && row.state === "ready" && row.runtime_state === "running";
  return <div className="admin-split"><AdminListFrame loading={query.isPending} error={query.error} empty={!rows.length} hasMore={query.hasNextPage} loadingMore={query.isFetchingNextPage} refresh={() => query.refetch()} more={() => query.fetchNextPage()}><AdminTable rows={rows} rowKey={(row) => row.owner_id} columns={[{ label: t("admin.owner"), render: (row) => <>{row.owner_id}<small>{row.instance_id}</small></> }, { label: t("admin.status"), render: (row) => <StateLabel state={row.state} /> }, { label: t("admin.runtime"), render: (row) => <StateLabel state={row.runtime_state} /> }, { label: t("admin.activeSessions"), render: (row) => row.active_sessions.length }, { label: t("admin.actions"), render: (row) => <button type="button" aria-label={`${t(canDrain(row) ? "admin.drain" : "admin.view")} ${row.owner_id}`} onClick={() => setSelected(row)}>{t(canDrain(row) ? "admin.drain" : "admin.view")}</button> }]} /></AdminListFrame>{selected && current && <AdminOperationEditor key={selected.owner_id} id={selected.owner_id} revision={selected.revision} confirmed={current.runtime_state === "stopped"} canAct={canDrain(current)} label="admin.requestDrain" hint="admin.drainHint" action={(revision) => api.drainAdminSandbox(selected.owner_id, revision)}><dl className="admin-detail"><dt>{t("admin.instance")}</dt><dd>{current.instance_id}</dd><dt>{t("admin.volume")}</dt><dd>{current.volume_id}</dd><dt>{t("admin.image")}</dt><dd>{current.image_digest}</dd><dt>{t("admin.runtime")}</dt><dd><StateLabel state={current.runtime_state} /></dd><dt>{t("admin.activeSessions")}</dt><dd>{current.active_sessions.length ? <ul>{current.active_sessions.map((session) => <li key={session}>{session}</li>)}</ul> : "0"}</dd><dt>{t("admin.lastCompleted")}</dt><dd>{current.last_completed_at ? new Date(current.last_completed_at * 1000).toLocaleString(language === "zh" ? "zh-CN" : "en-US") : t("admin.state.unknown")}</dd></dl></AdminOperationEditor>}</div>;
}
