import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { SkillDetail } from "../../api/types";
import { useLanguage } from "../../i18n/LanguageProvider";
import { adminErrorKey, adminKey, useAdmin } from "./adminQueries";
import { AdminListFrame, AdminTable } from "./AdminControls";

export function AdminSkillsPage() {
  const { t } = useLanguage();
  const { api, me } = useAdmin();
  const cache = useQueryClient();
  const [selected, setSelected] = useState<SkillDetail | null>(null);
  const [reason, setReason] = useState("");
  const reviews = useQuery({
    queryKey: [...adminKey(me.user_id), "skills"],
    queryFn: api.listSkillReviews,
    enabled: me.permissions.includes("services:read"),
    retry: false,
  });
  const review = useMutation({
    mutationFn: (decision: "approve" | "reject") => api.reviewSkill(
      selected!.id, selected!.version ?? 1, decision, reason,
    ),
    onSuccess: async () => {
      setSelected(null); setReason("");
      await cache.invalidateQueries({ queryKey: [...adminKey(me.user_id), "skills"] });
    },
  });
  const rows = reviews.data ?? [];
  const canReview = me.permissions.includes("services:publish");
  return <div className="admin-split"><AdminListFrame loading={reviews.isPending} error={reviews.error} empty={!rows.length} hasMore={false} loadingMore={false} refresh={() => reviews.refetch()} more={() => {}}>
    <AdminTable rows={rows} rowKey={(row) => `${row.id}:${row.version}`} columns={[
      { label: t("admin.skill"), render: (row) => <>{row.name}<small>{row.id} · v{row.version}</small></> },
      { label: t("admin.status"), render: (row) => row.visibility },
      { label: t("admin.actions"), render: (row) => <button type="button" onClick={() => setSelected(row)}>{t("admin.view")}</button> },
    ]} />
  </AdminListFrame>{selected && <aside className="admin-editor"><h2>{selected.name}</h2><p className="admin-hint">{selected.description}</p><div className="skill-file-tree">{selected.files.map((file) => <span key={file.path}>{file.path}</span>)}</div>{canReview && <><label>{t("admin.reason")}<textarea value={reason} onChange={(event) => setReason(event.target.value)} /></label><div className="admin-actions"><button type="button" disabled={reason.trim().length < 5 || review.isPending} onClick={() => review.mutate("approve")}>{t("admin.approve")}</button><button type="button" disabled={reason.trim().length < 5 || review.isPending} onClick={() => review.mutate("reject")}>{t("admin.reject")}</button></div></>}{review.error && <p className="admin-error" role="alert">{t(adminErrorKey(review.error))}</p>}</aside>}</div>;
}
