import { useLanguage } from "../../i18n/LanguageProvider";
import { AdminListFrame, AdminTable } from "./AdminControls";
import { useAdmin, useAdminList } from "./adminQueries";

export function AdminAuditPage() {
  const { api } = useAdmin();
  const { t } = useLanguage();
  const query = useAdminList("audit", "audit:read", api.listAuditEvents);
  const rows = query.data?.pages.flatMap((page) => page.items) ?? [];
  return <AdminListFrame loading={query.isPending} error={query.error} empty={!rows.length} hasMore={query.hasNextPage} loadingMore={query.isFetchingNextPage} refresh={() => query.refetch()} more={() => query.fetchNextPage()}><AdminTable rows={rows} rowKey={(row) => row.event_id} columns={[{ label: t("admin.event"), render: (row) => <>{row.action}<small>{row.created_at}</small></> }, { label: t("admin.actor"), render: (row) => row.actor_user_id }, { label: t("admin.resource"), render: (row) => row.resource_id }, { label: t("admin.reason"), render: (row) => row.reason }, { label: t("admin.changes"), render: (row) => <details><summary>{t("admin.view")}</summary><dl className="admin-detail"><dt>{t("admin.eventId")}</dt><dd>{row.event_id}</dd><dt>{t("admin.requestId")}</dt><dd>{row.request_id}</dd><dt>{t("admin.before")}</dt><dd><pre>{JSON.stringify(row.before, null, 2)}</pre></dd><dt>{t("admin.after")}</dt><dd><pre>{JSON.stringify(row.after, null, 2)}</pre></dd></dl></details> }]} /></AdminListFrame>;
}
